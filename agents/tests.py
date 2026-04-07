import json

from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.test import Client as HttpClient, TestCase
from django.test.utils import override_settings
from django.urls import reverse
from django.utils import timezone

from .admin import (
    ClientAccessAdmin,
    ClientAdmin,
    ClientInvitationAdmin,
    MachineAdmin,
    ScopedUserAdmin,
    ServiceRequestAdmin,
    admin,
)
from .models import Alert, AuditLog, Client, ClientAccess, ClientInvitation, ClientNotification, Machine, ServiceRequest, ServiceRequestNote, ServiceRequestPublicUpdate


class CommunicationHubTests(TestCase):
    def setUp(self):
        self.client = HttpClient()
        self.url = "/api/hub/"
        self.auth_headers = {"HTTP_X_API_KEY": settings.AGENT_KEY}

    def test_rejects_unauthorized_requests(self):
        response = self.client.post(
            self.url,
            data=json.dumps({"hostname": "PC-01"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(Machine.objects.count(), 0)

    def test_rejects_invalid_json_payload(self):
        response = self.client.post(
            self.url,
            data="{not-json",
            content_type="application/json",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 400)
        self.assertJSONEqual(response.content, {"error": "Invalid JSON payload"})

    def test_heartbeat_creates_machine_client_and_normalized_fields(self):
        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "client": "Acme",
                    "hostname": "PC-01",
                    "os": "Windows 11 Pro",
                    "cpu": "Intel Core i7",
                    "ram": 16,
                    "disk_percent": 73,
                    "processes": "explorer | 3% | 120 MB",
                    "mac": "AA-BB-CC-DD-EE-FF",
                    "brand": "Dell",
                    "model": "Latitude 7440",
                    "boot_time": "2026-03-28 10:15:00",
                    "remote_id": "123456789",
                    "remote_pass": "secret",
                }
            ),
            content_type="application/json",
            REMOTE_ADDR="10.10.10.15",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {"task": "None", "download_url": "", "run_maintenance": False},
        )

        machine = Machine.objects.get(hostname="PC-01")
        self.assertEqual(machine.client, Client.objects.get(name="Acme"))
        self.assertEqual(machine.ip_address, "10.10.10.15")
        self.assertEqual(machine.disk_usage_percent, 73)
        self.assertEqual(machine.remote_id, "123456789")
        self.assertIsNotNone(machine.last_boot_time)
        self.assertTrue(timezone.is_aware(machine.last_boot_time))
        self.assertTrue(
            AuditLog.objects.filter(machine=machine, action="Agent Registered").exists()
        )

    def test_placeholder_client_does_not_override_real_manual_assignment(self):
        real_client = Client.objects.create(name="Techmedics")
        default_client = Client.objects.create(name="DefaultClient")
        machine = Machine.objects.create(hostname="PC-REAL", client=real_client)

        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "client": "DefaultClient",
                    "hostname": "PC-REAL",
                    "os": "Windows 11 Pro",
                }
            ),
            content_type="application/json",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        machine.refresh_from_db()
        self.assertEqual(machine.client, real_client)
        self.assertNotEqual(machine.client, default_client)

    def test_real_client_can_replace_placeholder_assignment(self):
        default_client = Client.objects.create(name="DefaultClient")
        machine = Machine.objects.create(hostname="PC-NEW", client=default_client)

        response = self.client.post(
            self.url,
            data=json.dumps(
                {
                    "client": "Techmedics",
                    "hostname": "PC-NEW",
                    "os": "Windows 11 Pro",
                }
            ),
            content_type="application/json",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        machine.refresh_from_db()
        self.assertEqual(machine.client, Client.objects.get(name="Techmedics"))

    def test_output_acknowledgement_clears_pending_command(self):
        machine = Machine.objects.create(hostname="PC-02", pending_command="Restart-Computer -Force")

        response = self.client.post(
            self.url,
            data=json.dumps({"hostname": "PC-02", "output": "Success (No Output)"}),
            content_type="application/json",
            **self.auth_headers,
        )

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, {"status": "Task Cleared"})

        machine.refresh_from_db()
        self.assertEqual(machine.pending_command, "None")
        self.assertEqual(machine.command_results, "Success (No Output)")
        self.assertTrue(
            AuditLog.objects.filter(machine=machine, action="Task Finished").exists()
        )

    def test_heartbeat_creates_disk_alert_for_high_usage(self):
        self.client.post(
            self.url,
            data=json.dumps(
                {
                    "client": "Acme",
                    "hostname": "PC-04",
                    "disk_percent": 96,
                    "os": "Windows 11",
                }
            ),
            content_type="application/json",
            REMOTE_ADDR="10.10.10.16",
            **self.auth_headers,
        )

        alert = Alert.objects.get(machine__hostname="PC-04", category=Alert.CATEGORY_DISK)
        self.assertEqual(alert.status, Alert.STATUS_ACTIVE)
        self.assertEqual(alert.severity, Alert.SEVERITY_WARNING)

    def test_error_output_creates_command_failure_alert(self):
        machine = Machine.objects.create(
            hostname="PC-05",
            client=Client.objects.create(name="Acme"),
            pending_command="Restart-Computer -Force",
        )

        self.client.post(
            self.url,
            data=json.dumps({"hostname": "PC-05", "output": "Error: Access denied"}),
            content_type="application/json",
            **self.auth_headers,
        )

        alert = Alert.objects.get(machine=machine, category=Alert.CATEGORY_COMMAND)
        self.assertEqual(alert.status, Alert.STATUS_ACTIVE)


class SendCommandTests(TestCase):
    def setUp(self):
        self.client = HttpClient()
        self.staff_user = User.objects.create_user(
            username="staff.operator",
            password="pass12345",
            is_staff=True,
        )

    def test_kill_command_is_queued_with_safe_process_name(self):
        machine = Machine.objects.create(hostname="PC-03")
        self.client.login(username="staff.operator", password="pass12345")

        response = self.client.post(
            reverse("send_command", args=[machine.id]),
            data={"cmd_type": "kill", "process_name": 'bad"name'},
        )

        self.assertEqual(response.status_code, 302)
        machine.refresh_from_db()
        self.assertEqual(
            machine.pending_command,
            'Stop-Process -Name "bad`"name" -Force -ErrorAction SilentlyContinue',
        )
        self.assertTrue(
            AuditLog.objects.filter(
                machine=machine,
                action="Kill Command Queued",
                details='Target: bad"name',
            ).exists()
        )

    def test_send_command_requires_staff_dashboard_access(self):
        machine = Machine.objects.create(hostname="PC-SECURE")

        response = self.client.post(
            reverse("send_command", args=[machine.id]),
            data={"cmd_type": "kill", "process_name": "calc"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    def test_client_linked_staff_cannot_queue_command_for_other_org_machine(self):
        alpha = Client.objects.create(name="Alpha Commands")
        beta = Client.objects.create(name="Beta Commands")
        alpha_machine = Machine.objects.create(hostname="ALPHA-CMD", client=alpha)
        beta_machine = Machine.objects.create(hostname="BETA-CMD", client=beta)
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="staff.operator", password="pass12345")

        response = self.client.post(
            reverse("send_command", args=[beta_machine.id]),
            data={"cmd_type": "kill", "process_name": "calc"},
        )

        self.assertEqual(response.status_code, 404)
        alpha_machine.refresh_from_db()
        beta_machine.refresh_from_db()
        self.assertEqual(alpha_machine.pending_command, "None")
        self.assertEqual(beta_machine.pending_command, "None")


class ClientPortalTests(TestCase):
    def setUp(self):
        self.client = HttpClient()
        self.alpha = Client.objects.create(name="Alpha")
        self.beta = Client.objects.create(name="Beta")
        self.alpha_machine = Machine.objects.create(hostname="ALPHA-PC", client=self.alpha)
        self.beta_machine = Machine.objects.create(hostname="BETA-PC", client=self.beta)
        self.portal_user = User.objects.create_user(username="alpha.user", password="pass12345")
        ClientAccess.objects.create(user=self.portal_user, client=self.alpha)

    def test_client_login_redirects_to_client_dashboard(self):
        response = self.client.post(
            reverse("client_login"),
            data={"username": "alpha.user", "password": "pass12345"},
        )

        self.assertRedirects(response, reverse("client_dashboard"))

    def test_client_dashboard_only_shows_owned_machines(self):
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ALPHA-PC")
        self.assertNotContains(response, "BETA-PC")

    def test_client_service_request_page_only_shows_owned_company_machines(self):
        ServiceRequest.objects.create(
            client=self.alpha,
            machine=self.alpha_machine,
            requester=self.portal_user,
            subject="Alpha request",
            description="Visible to Alpha only.",
        )
        other_user = User.objects.create_user(username="beta.user", password="pass12345")
        ServiceRequest.objects.create(
            client=self.beta,
            machine=self.beta_machine,
            requester=other_user,
            subject="Beta request",
            description="Should stay hidden.",
        )
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_service_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ALPHA-PC")
        self.assertNotContains(response, "BETA-PC")
        self.assertNotContains(response, "Alpha request")
        self.assertNotContains(response, "Beta request")

    def test_client_service_request_page_hides_ticket_status_and_resolution(self):
        ServiceRequest.objects.create(
            client=self.alpha,
            machine=self.alpha_machine,
            requester=self.portal_user,
            subject="Resolved ticket",
            description="Issue details.",
            status=ServiceRequest.STATUS_CLOSED,
            resolution_summary="Printer driver was reinstalled successfully.",
        )
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_service_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Resolved ticket")
        self.assertNotContains(response, "Closed")
        self.assertNotContains(response, "Printer driver was reinstalled successfully.")

    def test_client_request_updates_page_shows_status_resolution_and_public_updates(self):
        service_request = ServiceRequest.objects.create(
            client=self.alpha,
            machine=self.alpha_machine,
            requester=self.portal_user,
            subject="Resolved ticket",
            description="Issue details.",
            status=ServiceRequest.STATUS_CLOSED,
            resolution_summary="Printer driver was reinstalled successfully.",
        )
        ServiceRequestPublicUpdate.objects.create(
            service_request=service_request,
            author=self.portal_user,
            body="The technician is waiting for the final reboot window.",
        )
        beta_user = User.objects.create_user(username="beta.req.status", password="pass12345")
        beta_request = ServiceRequest.objects.create(
            client=self.beta,
            machine=self.beta_machine,
            requester=beta_user,
            subject="Beta hidden ticket",
            description="Should not show up.",
        )
        ServiceRequestPublicUpdate.objects.create(
            service_request=beta_request,
            author=beta_user,
            body="Beta update.",
        )
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_request_updates"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Resolved ticket")
        self.assertContains(response, "Closed")
        self.assertContains(response, "Printer driver was reinstalled successfully.")
        self.assertContains(response, "The technician is waiting for the final reboot window.")
        self.assertNotContains(response, "Beta hidden ticket")
        self.assertNotContains(response, "Beta update.")

    def test_client_cannot_queue_actions_for_another_clients_machine(self):
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_machine_action", args=[self.beta_machine.id]),
            data={"action": "reboot"},
        )

        self.assertEqual(response.status_code, 404)
        self.beta_machine.refresh_from_db()
        self.assertEqual(self.beta_machine.pending_command, "None")

    def test_client_can_toggle_maintenance_for_owned_machine(self):
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_machine_action", args=[self.alpha_machine.id]),
            data={"action": "toggle_maintenance"},
        )

        self.assertRedirects(response, reverse("client_dashboard"))
        self.alpha_machine.refresh_from_db()
        self.assertTrue(self.alpha_machine.auto_maintenance)
        self.assertTrue(
            AuditLog.objects.filter(
                machine=self.alpha_machine,
                action="Client Maintenance Preference Updated",
            ).exists()
        )

    def test_client_can_acknowledge_owned_alert(self):
        alert = Alert.objects.create(
            client=self.alpha,
            machine=self.alpha_machine,
            category=Alert.CATEGORY_COMMAND,
            severity=Alert.SEVERITY_WARNING,
            title="Command failed",
            message="A maintenance command failed.",
        )
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_alert_action", args=[alert.id]),
            data={"action": "acknowledge"},
        )

        self.assertRedirects(response, reverse("client_dashboard"))
        alert.refresh_from_db()
        self.assertEqual(alert.status, Alert.STATUS_ACKNOWLEDGED)
        self.assertEqual(alert.acknowledged_by, "alpha.user")

    def test_client_cannot_acknowledge_other_clients_alert(self):
        other_alert = Alert.objects.create(
            client=self.beta,
            machine=self.beta_machine,
            category=Alert.CATEGORY_OFFLINE,
            severity=Alert.SEVERITY_CRITICAL,
            title="Offline",
            message="Machine is offline.",
        )
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_alert_action", args=[other_alert.id]),
            data={"action": "acknowledge"},
        )

        self.assertEqual(response.status_code, 404)

    def test_client_dashboard_can_filter_resolved_alert_history(self):
        Alert.objects.create(
            client=self.alpha,
            machine=self.alpha_machine,
            category=Alert.CATEGORY_COMMAND,
            severity=Alert.SEVERITY_WARNING,
            status=Alert.STATUS_RESOLVED,
            title="Resolved issue",
            message="This issue was fixed.",
        )
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_dashboard"), {"alert_status": "resolved"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Resolved issue")

    def test_client_user_is_redirected_away_from_technician_dashboard(self):
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("dashboard"))

        self.assertRedirects(response, reverse("client_dashboard"))

    def test_client_can_submit_support_request_for_owned_machine(self):
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_submit_request"),
            data={
                "machine": self.alpha_machine.id,
                "subject": "Printer issue",
                "priority": ServiceRequest.PRIORITY_HIGH,
                "description": "Printing stopped after the last reboot.",
            },
        )

        self.assertRedirects(response, reverse("client_service_requests"))
        service_request = ServiceRequest.objects.get(subject="Printer issue")
        self.assertEqual(service_request.client, self.alpha)
        self.assertEqual(service_request.machine, self.alpha_machine)
        self.assertEqual(service_request.requester, self.portal_user)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        SUPPORT_NOTIFICATION_EMAILS=["ops@tj-rmm.test"],
    )
    def test_support_request_submission_sends_notification_email(self):
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_submit_request"),
            data={
                "machine": self.alpha_machine.id,
                "subject": "Needs software",
                "priority": ServiceRequest.PRIORITY_NORMAL,
                "description": "Please install the finance package.",
            },
        )

        self.assertRedirects(response, reverse("client_service_requests"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("ops@tj-rmm.test", mail.outbox[0].to)
        self.assertIn("Needs software", mail.outbox[0].body)
        notification = ClientNotification.objects.get(client=self.alpha, user=self.portal_user)
        self.assertEqual(notification.category, ClientNotification.CATEGORY_TICKET)
        self.assertIn("Support request submitted", notification.title)

    def test_client_cannot_submit_support_request_for_other_clients_machine(self):
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_submit_request"),
            data={
                "machine": self.beta_machine.id,
                "subject": "Unauthorized device",
                "priority": ServiceRequest.PRIORITY_NORMAL,
                "description": "This should not be allowed.",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")
        self.assertFalse(ServiceRequest.objects.filter(subject="Unauthorized device").exists())

    def test_client_request_updates_page_requires_client_login(self):
        response = self.client.get(reverse("client_request_updates"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/client/login/", response.url)

    def test_client_can_close_owned_support_request(self):
        service_request = ServiceRequest.objects.create(
            client=self.alpha,
            machine=self.alpha_machine,
            requester=self.portal_user,
            subject="VPN issue",
            description="VPN does not connect.",
        )
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_request_action", args=[service_request.id]),
            data={"action": "close"},
        )

        self.assertRedirects(response, reverse("client_service_requests"))
        service_request.refresh_from_db()
        self.assertEqual(service_request.status, ServiceRequest.STATUS_CLOSED)
        self.assertEqual(service_request.closed_by, "alpha.user")

    def test_viewer_role_cannot_manage_devices(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_VIEWER
        access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_machine_action", args=[self.alpha_machine.id]),
            data={"action": "cleanup"},
        )

        self.assertRedirects(response, reverse("client_dashboard"))
        self.alpha_machine.refresh_from_db()
        self.assertEqual(self.alpha_machine.pending_command, "None")

    def test_viewer_role_cannot_submit_support_requests(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_VIEWER
        access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_submit_request"),
            data={
                "machine": self.alpha_machine.id,
                "subject": "Should fail",
                "priority": ServiceRequest.PRIORITY_NORMAL,
                "description": "Viewer should not create tickets.",
            },
        )

        self.assertRedirects(response, reverse("client_service_requests"))
        self.assertFalse(ServiceRequest.objects.filter(subject="Should fail").exists())

    def test_admin_role_can_manage_devices(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_ADMIN
        access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_machine_action", args=[self.alpha_machine.id]),
            data={"action": "cleanup"},
        )

        self.assertRedirects(response, reverse("client_dashboard"))
        self.alpha_machine.refresh_from_db()
        self.assertEqual(self.alpha_machine.pending_command, 'Remove-Item "$env:TEMP\\*" -Recurse -Force -ErrorAction SilentlyContinue')

    def test_owner_can_open_team_management_page(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_OWNER
        access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_team"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invite teammates into Alpha")

    def test_member_cannot_open_team_management_page(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_MEMBER
        access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_team"))

        self.assertRedirects(response, reverse("client_dashboard"))

    def test_owner_can_create_client_invitation(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_OWNER
        access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_invite_teammate"),
            data={"email": "teammate@alpha.test", "role": ClientAccess.ROLE_MEMBER},
        )

        self.assertRedirects(response, reverse("client_team"))
        invitation = ClientInvitation.objects.get(email="teammate@alpha.test")
        self.assertEqual(invitation.client, self.alpha)
        self.assertEqual(invitation.role, ClientAccess.ROLE_MEMBER)
        self.assertEqual(invitation.invited_by, self.portal_user)
        self.assertEqual(invitation.status, ClientInvitation.STATUS_PENDING)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        CLIENT_PORTAL_BASE_URL="http://127.0.0.1:8000",
    )
    def test_invitation_creation_sends_email_notification(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_OWNER
        access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_invite_teammate"),
            data={"email": "notify@alpha.test", "role": ClientAccess.ROLE_MEMBER},
        )

        self.assertRedirects(response, reverse("client_team"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("notify@alpha.test", mail.outbox[0].to)
        self.assertIn("/client/invite/", mail.outbox[0].body)
        notification = ClientNotification.objects.get(client=self.alpha, category=ClientNotification.CATEGORY_TEAM)
        self.assertIn("Invitation created", notification.title)

    def test_team_page_lists_only_selectable_unassigned_users(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_OWNER
        access.save(update_fields=["role"])
        selectable_user = User.objects.create_user(username="free.user", password="pass12345", email="free@test.local")
        linked_user = User.objects.create_user(username="linked.user", password="pass12345", email="linked@test.local")
        ClientAccess.objects.create(user=linked_user, client=self.beta, role=ClientAccess.ROLE_MEMBER)
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_team"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "free.user")
        self.assertNotContains(response, "linked.user")

    def test_team_page_renders_clickable_user_links(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_OWNER
        access.save(update_fields=["role"])
        teammate = User.objects.create_user(username="alpha.click", password="pass12345", email="click@test.local")
        teammate_access = ClientAccess.objects.create(user=teammate, client=self.alpha, role=ClientAccess.ROLE_MEMBER)
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_team"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("client_team_member_detail", args=[teammate_access.id]))

    def test_owner_can_add_existing_unassigned_user_to_client(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_OWNER
        access.save(update_fields=["role"])
        selectable_user = User.objects.create_user(username="free.attach", password="pass12345", email="attach@test.local")
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_add_existing_user"),
            data={
                "user": selectable_user.id,
                "role": ClientAccess.ROLE_MEMBER,
                "can_restart_machines": "on",
            },
        )

        self.assertRedirects(response, reverse("client_team"))
        attached_access = ClientAccess.objects.get(user=selectable_user)
        self.assertEqual(attached_access.client, self.alpha)
        self.assertEqual(attached_access.role, ClientAccess.ROLE_MEMBER)
        self.assertTrue(attached_access.can_restart_machines)

    def test_owner_can_open_clickable_team_member_editor(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_OWNER
        access.save(update_fields=["role"])
        teammate = User.objects.create_user(username="alpha.detail", password="pass12345", email="detail@test.local")
        teammate_access = ClientAccess.objects.create(user=teammate, client=self.alpha, role=ClientAccess.ROLE_MEMBER)
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_team_member_detail", args=[teammate_access.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Update teammate permissions")
        self.assertContains(response, "alpha.detail")

    def test_admin_cannot_add_user_already_linked_to_another_client(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_ADMIN
        access.save(update_fields=["role"])
        linked_user = User.objects.create_user(username="already.linked", password="pass12345", email="linked2@test.local")
        ClientAccess.objects.create(user=linked_user, client=self.beta, role=ClientAccess.ROLE_MEMBER)
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_add_existing_user"),
            data={
                "user": linked_user.id,
                "role": ClientAccess.ROLE_MEMBER,
                "can_restart_machines": "",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Select a valid choice")
        self.assertEqual(ClientAccess.objects.filter(user=linked_user).count(), 1)

    def test_viewer_cannot_create_client_invitation(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_VIEWER
        access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_invite_teammate"),
            data={"email": "blocked@alpha.test", "role": ClientAccess.ROLE_MEMBER},
        )

        self.assertRedirects(response, reverse("client_dashboard"))
        self.assertFalse(ClientInvitation.objects.filter(email="blocked@alpha.test").exists())

    def test_accept_client_invitation_creates_scoped_account(self):
        invitation = ClientInvitation.objects.create(
            client=self.alpha,
            email="new.user@alpha.test",
            role=ClientAccess.ROLE_VIEWER,
            invited_by=self.portal_user,
            token="accept-token-123",
        )

        response = self.client.post(
            reverse("accept_client_invitation", args=[invitation.token]),
            data={
                "username": "alpha.new",
                "first_name": "New",
                "last_name": "User",
                "password1": "pass12345!",
                "password2": "pass12345!",
            },
        )

        self.assertRedirects(response, reverse("client_dashboard"))
        user = User.objects.get(username="alpha.new")
        access = ClientAccess.objects.get(user=user)
        invitation.refresh_from_db()
        self.assertEqual(user.email, "new.user@alpha.test")
        self.assertEqual(access.client, self.alpha)
        self.assertEqual(access.role, ClientAccess.ROLE_VIEWER)
        self.assertEqual(invitation.status, ClientInvitation.STATUS_ACCEPTED)
        self.assertEqual(invitation.accepted_by, user)

    def test_revoked_invitation_redirects_to_client_login(self):
        invitation = ClientInvitation.objects.create(
            client=self.alpha,
            email="revoked@alpha.test",
            role=ClientAccess.ROLE_MEMBER,
            invited_by=self.portal_user,
            token="revoked-token-123",
            status=ClientInvitation.STATUS_REVOKED,
        )

        response = self.client.get(reverse("accept_client_invitation", args=[invitation.token]))

        self.assertRedirects(response, reverse("client_login"))

    def test_team_page_only_shows_same_client_members_and_invites(self):
        owner_access = self.portal_user.client_access
        owner_access.role = ClientAccess.ROLE_OWNER
        owner_access.save(update_fields=["role"])
        teammate = User.objects.create_user(username="alpha.team", password="pass12345", email="alpha.team@test.local")
        ClientAccess.objects.create(user=teammate, client=self.alpha, role=ClientAccess.ROLE_MEMBER)
        outsider = User.objects.create_user(username="beta.team", password="pass12345", email="beta.team@test.local")
        ClientAccess.objects.create(user=outsider, client=self.beta, role=ClientAccess.ROLE_MEMBER)
        ClientInvitation.objects.create(
            client=self.alpha,
            email="alpha.pending@test.local",
            role=ClientAccess.ROLE_MEMBER,
            invited_by=self.portal_user,
            token="alpha-pending-token",
        )
        ClientInvitation.objects.create(
            client=self.beta,
            email="beta.pending@test.local",
            role=ClientAccess.ROLE_MEMBER,
            invited_by=outsider,
            token="beta-pending-token",
        )
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_team"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "alpha.team@test.local")
        self.assertContains(response, "alpha.pending@test.local")
        self.assertNotContains(response, "beta.team@test.local")
        self.assertNotContains(response, "beta.pending@test.local")

    def test_owner_can_update_teammate_role_and_restart_rights(self):
        owner_access = self.portal_user.client_access
        owner_access.role = ClientAccess.ROLE_OWNER
        owner_access.save(update_fields=["role"])
        teammate = User.objects.create_user(username="alpha.editor", password="pass12345", email="alpha.editor@test.local")
        teammate_access = ClientAccess.objects.create(user=teammate, client=self.alpha, role=ClientAccess.ROLE_MEMBER, can_restart_machines=True)
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_team_member_action", args=[teammate_access.id]),
            data={
                "action": "update",
                f"member-{teammate_access.id}-role": ClientAccess.ROLE_VIEWER,
                f"member-{teammate_access.id}-can_restart_machines": "",
            },
        )

        self.assertRedirects(response, reverse("client_team"))
        teammate_access.refresh_from_db()
        self.assertEqual(teammate_access.role, ClientAccess.ROLE_VIEWER)
        self.assertFalse(teammate_access.can_restart_machines)

    def test_admin_cannot_manage_owner_account(self):
        owner = User.objects.create_user(username="alpha.owner", password="pass12345", email="alpha.owner@test.local")
        owner_access = ClientAccess.objects.create(user=owner, client=self.alpha, role=ClientAccess.ROLE_OWNER, can_restart_machines=True)
        admin_access = self.portal_user.client_access
        admin_access.role = ClientAccess.ROLE_ADMIN
        admin_access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_team_member_action", args=[owner_access.id]),
            data={
                "action": "update",
                f"member-{owner_access.id}-role": ClientAccess.ROLE_MEMBER,
                f"member-{owner_access.id}-can_restart_machines": "on",
            },
        )

        self.assertRedirects(response, reverse("client_team"))
        owner_access.refresh_from_db()
        self.assertEqual(owner_access.role, ClientAccess.ROLE_OWNER)

    def test_admin_can_remove_member_access(self):
        admin_access = self.portal_user.client_access
        admin_access.role = ClientAccess.ROLE_ADMIN
        admin_access.save(update_fields=["role"])
        teammate = User.objects.create_user(username="alpha.remove", password="pass12345", email="alpha.remove@test.local")
        teammate_access = ClientAccess.objects.create(user=teammate, client=self.alpha, role=ClientAccess.ROLE_MEMBER)
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_team_member_action", args=[teammate_access.id]),
            data={"action": "remove"},
        )

        self.assertRedirects(response, reverse("client_team"))
        self.assertFalse(ClientAccess.objects.filter(id=teammate_access.id).exists())

    def test_user_cannot_remove_their_own_access(self):
        owner_access = self.portal_user.client_access
        owner_access.role = ClientAccess.ROLE_OWNER
        owner_access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_team_member_action", args=[owner_access.id]),
            data={"action": "remove"},
        )

        self.assertRedirects(response, reverse("client_team"))
        self.assertTrue(ClientAccess.objects.filter(id=owner_access.id).exists())

    def test_owner_can_open_company_settings_page(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_OWNER
        access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_settings"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Manage tenant branding and contact details.")

    def test_member_cannot_open_company_settings_page(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_MEMBER
        access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_settings"))

        self.assertRedirects(response, reverse("client_dashboard"))

    def test_owner_can_update_company_contact_email_and_logo(self):
        access = self.portal_user.client_access
        access.role = ClientAccess.ROLE_OWNER
        access.save(update_fields=["role"])
        self.client.login(username="alpha.user", password="pass12345")
        logo = SimpleUploadedFile(
            "logo.gif",
            b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
            content_type="image/gif",
        )

        response = self.client.post(
            reverse("client_settings"),
            data={
                "contact_email": "support@alpha.test",
                "logo": logo,
            },
        )

        self.assertRedirects(response, reverse("client_settings"))
        self.alpha.refresh_from_db()
        self.assertEqual(self.alpha.contact_email, "support@alpha.test")
        self.assertTrue(bool(self.alpha.logo))

    def test_client_notifications_page_is_scoped_to_current_user_and_client(self):
        own_notification = ClientNotification.objects.create(
            client=self.alpha,
            user=self.portal_user,
            category=ClientNotification.CATEGORY_TICKET,
            title="Your update",
            message="Ticket moved to in progress.",
        )
        teammate = User.objects.create_user(username="alpha.other", password="pass12345")
        teammate_access = ClientAccess.objects.create(user=teammate, client=self.alpha, role=ClientAccess.ROLE_MEMBER)
        ClientNotification.objects.create(
            client=self.alpha,
            user=teammate,
            category=ClientNotification.CATEGORY_TEAM,
            title="Other user only",
            message="Private teammate note.",
        )
        ClientNotification.objects.create(
            client=self.alpha,
            category=ClientNotification.CATEGORY_INFO,
            title="Shared notice",
            message="Shared to the whole client.",
        )
        ClientNotification.objects.create(
            client=self.beta,
            category=ClientNotification.CATEGORY_INFO,
            title="Other client notice",
            message="Should stay hidden.",
        )
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.get(reverse("client_notifications"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, own_notification.title)
        self.assertContains(response, "Shared notice")
        self.assertNotContains(response, "Other user only")
        self.assertNotContains(response, "Other client notice")

    def test_client_can_mark_notification_read(self):
        notification = ClientNotification.objects.create(
            client=self.alpha,
            user=self.portal_user,
            category=ClientNotification.CATEGORY_INFO,
            title="Unread notice",
            message="Please review this.",
        )
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(
            reverse("client_notification_action", args=[notification.id]),
            data={"action": "mark_read"},
        )

        self.assertRedirects(response, reverse("client_notifications"))
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)
        self.assertIsNotNone(notification.read_at)

    def test_client_can_mark_all_notifications_read(self):
        ClientNotification.objects.create(
            client=self.alpha,
            user=self.portal_user,
            category=ClientNotification.CATEGORY_INFO,
            title="First",
            message="One",
        )
        ClientNotification.objects.create(
            client=self.alpha,
            category=ClientNotification.CATEGORY_TEAM,
            title="Second",
            message="Two",
        )
        self.client.login(username="alpha.user", password="pass12345")

        response = self.client.post(reverse("client_notification_mark_all_read"))

        self.assertRedirects(response, reverse("client_notifications"))
        self.assertEqual(
            ClientNotification.objects.filter(client=self.alpha, is_read=False).count(),
            0,
        )


class TechnicianDashboardAccessTests(TestCase):
    def setUp(self):
        self.client = HttpClient()
        self.staff_user = User.objects.create_user(
            username="tech.admin",
            password="pass12345",
            is_staff=True,
        )

    def test_dashboard_redirects_anonymous_users_to_admin_login(self):
        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response.url)

    def test_staff_user_can_access_dashboard(self):
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)

    def test_staff_user_can_access_machine_workspace(self):
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.get(reverse("technician_machines"))

        self.assertEqual(response.status_code, 200)

    def test_staff_user_can_access_alert_workspace(self):
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.get(reverse("technician_alerts"))

        self.assertEqual(response.status_code, 200)

    def test_client_linked_staff_dashboard_only_shows_own_org_machines_and_alerts(self):
        alpha = Client.objects.create(name="Alpha Dashboard")
        beta = Client.objects.create(name="Beta Dashboard")
        alpha_machine = Machine.objects.create(hostname="ALPHA-DASH-PC", client=alpha, disk_usage_percent=95)
        beta_machine = Machine.objects.create(hostname="BETA-DASH-PC", client=beta, disk_usage_percent=96)
        alpha_alert = Alert.objects.create(
            client=alpha,
            machine=alpha_machine,
            category=Alert.CATEGORY_DISK,
            title="Alpha disk alert",
            message="Alpha device is low on disk space.",
            status=Alert.STATUS_ACTIVE,
        )
        beta_alert = Alert.objects.create(
            client=beta,
            machine=beta_machine,
            category=Alert.CATEGORY_DISK,
            title="Beta disk alert",
            message="Beta device is low on disk space.",
            status=Alert.STATUS_ACTIVE,
        )
        alpha_requester = User.objects.create_user(username="alpha.dashboard.user", password="pass12345")
        beta_requester = User.objects.create_user(username="beta.dashboard.user", password="pass12345")
        alpha_request = ServiceRequest.objects.create(
            client=alpha,
            machine=alpha_machine,
            requester=alpha_requester,
            subject="Alpha ticket",
            description="Visible on the alpha dashboard.",
        )
        ServiceRequest.objects.create(
            client=beta,
            machine=beta_machine,
            requester=beta_requester,
            subject="Beta ticket",
            description="Must stay hidden.",
        )
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.get(reverse("dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, alpha_machine.hostname)
        self.assertNotContains(response, beta_machine.hostname)
        self.assertContains(response, alpha_request.subject)
        self.assertNotContains(response, "Beta ticket")
        self.assertIn(alpha_alert, response.context["open_alerts"])
        self.assertNotIn(beta_alert, response.context["open_alerts"])
        self.assertEqual(response.context["staff_client_access"].client, alpha)
        self.assertEqual(response.context["total_online"] + response.context["total_offline"], 1)
        self.assertEqual(response.context["total_alerts"], 1)
        self.assertEqual(response.context["total_requests"], 1)

    def test_client_linked_staff_machine_workspace_only_shows_own_org(self):
        alpha = Client.objects.create(name="Alpha Machines")
        beta = Client.objects.create(name="Beta Machines")
        alpha_machine = Machine.objects.create(hostname="ALPHA-WORKSPACE-PC", client=alpha)
        beta_machine = Machine.objects.create(hostname="BETA-WORKSPACE-PC", client=beta)
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.get(reverse("technician_machines"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, alpha_machine.hostname)
        self.assertNotContains(response, beta_machine.hostname)
        self.assertEqual(response.context["staff_client_access"].client, alpha)

    def test_client_linked_staff_machine_detail_only_opens_own_org(self):
        alpha = Client.objects.create(name="Alpha Detail")
        beta = Client.objects.create(name="Beta Detail")
        alpha_machine = Machine.objects.create(hostname="ALPHA-DETAIL-PC", client=alpha)
        beta_machine = Machine.objects.create(hostname="BETA-DETAIL-PC", client=beta)
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        allowed_response = self.client.get(reverse("technician_machine_detail", args=[alpha_machine.id]))
        blocked_response = self.client.get(reverse("technician_machine_detail", args=[beta_machine.id]))

        self.assertEqual(allowed_response.status_code, 200)
        self.assertContains(allowed_response, alpha_machine.hostname)
        self.assertEqual(blocked_response.status_code, 404)

    def test_client_linked_staff_cannot_queue_machine_action_for_other_org(self):
        alpha = Client.objects.create(name="Alpha Machine Actions")
        beta = Client.objects.create(name="Beta Machine Actions")
        alpha_machine = Machine.objects.create(hostname="ALPHA-ACTION-PC", client=alpha)
        beta_machine = Machine.objects.create(hostname="BETA-ACTION-PC", client=beta)
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.post(
            reverse("technician_machine_action", args=[beta_machine.id]),
            data={"action": "reboot"},
        )

        self.assertEqual(response.status_code, 404)
        alpha_machine.refresh_from_db()
        beta_machine.refresh_from_db()
        self.assertEqual(alpha_machine.pending_command, "None")
        self.assertEqual(beta_machine.pending_command, "None")

    def test_client_linked_staff_alert_workspace_only_shows_own_org(self):
        alpha = Client.objects.create(name="Alpha Alert Workspace")
        beta = Client.objects.create(name="Beta Alert Workspace")
        alpha_machine = Machine.objects.create(hostname="ALPHA-ALERT-WORKSPACE", client=alpha)
        beta_machine = Machine.objects.create(hostname="BETA-ALERT-WORKSPACE", client=beta)
        alpha_alert = Alert.objects.create(
            client=alpha,
            machine=alpha_machine,
            category=Alert.CATEGORY_COMMAND,
            title="Alpha command failure",
            message="Alpha issue.",
            status=Alert.STATUS_ACTIVE,
        )
        beta_alert = Alert.objects.create(
            client=beta,
            machine=beta_machine,
            category=Alert.CATEGORY_COMMAND,
            title="Beta command failure",
            message="Beta issue.",
            status=Alert.STATUS_ACTIVE,
        )
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.get(reverse("technician_alerts"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, alpha_alert.title)
        self.assertNotContains(response, beta_alert.title)
        self.assertEqual(response.context["staff_client_access"].client, alpha)

    def test_staff_user_can_start_support_request(self):
        client = Client.objects.create(name="Gamma")
        machine = Machine.objects.create(hostname="GAMMA-PC", client=client)
        requester = User.objects.create_user(username="gamma.user", password="pass12345")
        service_request = ServiceRequest.objects.create(
            client=client,
            machine=machine,
            requester=requester,
            subject="Install app",
            description="Need a new finance tool installed.",
        )
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.post(
            reverse("technician_request_action", args=[service_request.id]),
            data={
                "status": ServiceRequest.STATUS_IN_PROGRESS,
                "resolution_summary": "",
            },
        )

        self.assertRedirects(response, reverse("technician_request_detail", args=[service_request.id]))
        service_request.refresh_from_db()
        self.assertEqual(service_request.status, ServiceRequest.STATUS_IN_PROGRESS)

    def test_staff_user_can_access_support_portal(self):
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.get(reverse("technician_service_requests"))

        self.assertEqual(response.status_code, 200)

    def test_staff_user_can_access_ticket_detail(self):
        client = Client.objects.create(name="Detail Client")
        requester = User.objects.create_user(username="detail.user", password="pass12345")
        service_request = ServiceRequest.objects.create(
            client=client,
            requester=requester,
            subject="Open detail",
            description="Need the detail page.",
        )
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.get(reverse("technician_request_detail", args=[service_request.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, service_request.subject)

    def test_staff_user_can_assign_ticket_to_self(self):
        client = Client.objects.create(name="Assigned")
        requester = User.objects.create_user(username="assigned.user", password="pass12345")
        service_request = ServiceRequest.objects.create(
            client=client,
            requester=requester,
            subject="Claim me",
            description="Needs a technician.",
        )
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.post(
            reverse("technician_request_action", args=[service_request.id]),
            data={"action": "assign_to_me"},
        )

        self.assertRedirects(response, reverse("technician_request_detail", args=[service_request.id]))
        service_request.refresh_from_db()
        self.assertEqual(service_request.assigned_to, self.staff_user)

    def test_support_portal_can_filter_my_queue(self):
        client = Client.objects.create(name="FilterCo")
        requester = User.objects.create_user(username="filter.user", password="pass12345")
        mine = ServiceRequest.objects.create(
            client=client,
            requester=requester,
            subject="Mine",
            description="Assigned to current tech.",
            assigned_to=self.staff_user,
        )
        other_staff = User.objects.create_user(username="other.tech", password="pass12345", is_staff=True)
        ServiceRequest.objects.create(
            client=client,
            requester=requester,
            subject="Other",
            description="Assigned elsewhere.",
            assigned_to=other_staff,
        )
        ServiceRequest.objects.create(
            client=client,
            requester=requester,
            subject="Nobody",
            description="Still unassigned.",
        )
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.get(reverse("technician_service_requests"), {"ownership": "mine"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, mine.subject)
        self.assertNotContains(response, "Other")
        self.assertNotContains(response, "Nobody")

    def test_support_portal_can_filter_unassigned_queue(self):
        client = Client.objects.create(name="UnassignedCo")
        requester = User.objects.create_user(username="unassigned.user", password="pass12345")
        ServiceRequest.objects.create(
            client=client,
            requester=requester,
            subject="Owned",
            description="Already claimed.",
            assigned_to=self.staff_user,
        )
        unassigned = ServiceRequest.objects.create(
            client=client,
            requester=requester,
            subject="Unassigned",
            description="Waiting for claim.",
        )
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.get(reverse("technician_service_requests"), {"ownership": "unassigned"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, unassigned.subject)
        self.assertNotContains(response, "Owned")

    def test_staff_user_can_publish_resolution_to_ticket(self):
        client = Client.objects.create(name="Delta")
        machine = Machine.objects.create(hostname="DELTA-PC", client=client)
        requester = User.objects.create_user(username="delta.user", password="pass12345")
        service_request = ServiceRequest.objects.create(
            client=client,
            machine=machine,
            requester=requester,
            subject="App install",
            description="Need accounting app installed.",
        )
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.post(
            reverse("technician_request_action", args=[service_request.id]),
            data={
                "status": ServiceRequest.STATUS_CLOSED,
                "resolution_summary": "Installed the requested app and verified launch.",
            },
        )

        self.assertRedirects(response, reverse("technician_request_detail", args=[service_request.id]))
        service_request.refresh_from_db()
        self.assertEqual(service_request.status, ServiceRequest.STATUS_CLOSED)
        self.assertEqual(service_request.resolution_summary, "Installed the requested app and verified launch.")
        self.assertEqual(service_request.closed_by, "tech.admin")

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_ticket_update_sends_requester_notification_email(self):
        client = Client.objects.create(name="Echo", contact_email="support@echo.test")
        requester = User.objects.create_user(
            username="echo.user",
            password="pass12345",
            email="echo.user@test.local",
        )
        service_request = ServiceRequest.objects.create(
            client=client,
            requester=requester,
            subject="VPN issue",
            description="VPN is failing to connect.",
        )
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.post(
            reverse("technician_request_action", args=[service_request.id]),
            data={
                "status": ServiceRequest.STATUS_IN_PROGRESS,
                "resolution_summary": "We are investigating the VPN profile.",
            },
        )

        self.assertRedirects(response, reverse("technician_request_detail", args=[service_request.id]))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("echo.user@test.local", mail.outbox[0].to)
        self.assertIn("VPN issue", mail.outbox[0].body)

    def test_client_linked_staff_support_portal_only_shows_own_org_tickets(self):
        alpha = Client.objects.create(name="Alpha Support")
        beta = Client.objects.create(name="Beta Support")
        alpha_requester = User.objects.create_user(username="alpha.req", password="pass12345")
        beta_requester = User.objects.create_user(username="beta.req", password="pass12345")
        alpha_ticket = ServiceRequest.objects.create(
            client=alpha,
            requester=alpha_requester,
            subject="Alpha ticket",
            description="Visible ticket.",
        )
        ServiceRequest.objects.create(
            client=beta,
            requester=beta_requester,
            subject="Beta ticket",
            description="Should stay hidden.",
        )
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.get(reverse("technician_service_requests"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, alpha_ticket.subject)
        self.assertNotContains(response, "Beta ticket")
        self.assertEqual(response.context["staff_client_access"].client, alpha)

    def test_client_linked_staff_ticket_detail_only_opens_own_org(self):
        alpha = Client.objects.create(name="Alpha Ticket Detail")
        beta = Client.objects.create(name="Beta Ticket Detail")
        alpha_requester = User.objects.create_user(username="alpha.detail.req", password="pass12345")
        beta_requester = User.objects.create_user(username="beta.detail.req", password="pass12345")
        alpha_ticket = ServiceRequest.objects.create(
            client=alpha,
            requester=alpha_requester,
            subject="Alpha detail",
            description="Visible detail ticket.",
        )
        beta_ticket = ServiceRequest.objects.create(
            client=beta,
            requester=beta_requester,
            subject="Beta detail",
            description="Hidden detail ticket.",
        )
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        allowed_response = self.client.get(reverse("technician_request_detail", args=[alpha_ticket.id]))
        blocked_response = self.client.get(reverse("technician_request_detail", args=[beta_ticket.id]))

        self.assertEqual(allowed_response.status_code, 200)
        self.assertContains(allowed_response, alpha_ticket.subject)
        self.assertEqual(blocked_response.status_code, 404)

    def test_client_linked_staff_can_add_internal_note_to_own_org_ticket(self):
        alpha = Client.objects.create(name="Alpha Notes")
        requester = User.objects.create_user(username="alpha.notes.req", password="pass12345")
        service_request = ServiceRequest.objects.create(
            client=alpha,
            requester=requester,
            subject="Need notes",
            description="Track internal work.",
        )
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.post(
            reverse("technician_request_note_action", args=[service_request.id]),
            data={"body": "Checked logs and confirmed a profile issue."},
        )

        self.assertRedirects(response, reverse("technician_request_detail", args=[service_request.id]))
        note = ServiceRequestNote.objects.get(service_request=service_request)
        self.assertEqual(note.author, self.staff_user)
        self.assertEqual(note.body, "Checked logs and confirmed a profile issue.")

    def test_client_linked_staff_cannot_add_internal_note_to_other_org_ticket(self):
        alpha = Client.objects.create(name="Alpha Notes Locked")
        beta = Client.objects.create(name="Beta Notes Locked")
        alpha_requester = User.objects.create_user(username="alpha.notes.locked", password="pass12345")
        beta_requester = User.objects.create_user(username="beta.notes.locked", password="pass12345")
        ServiceRequest.objects.create(
            client=alpha,
            requester=alpha_requester,
            subject="Alpha note ticket",
            description="Visible note ticket.",
        )
        beta_ticket = ServiceRequest.objects.create(
            client=beta,
            requester=beta_requester,
            subject="Beta note ticket",
            description="Protected note ticket.",
        )
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.post(
            reverse("technician_request_note_action", args=[beta_ticket.id]),
            data={"body": "This should not be saved."},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(ServiceRequestNote.objects.filter(service_request=beta_ticket).exists())

    def test_client_linked_staff_can_post_public_update_to_own_org_ticket(self):
        alpha = Client.objects.create(name="Alpha Public Updates")
        requester = User.objects.create_user(username="alpha.public.req", password="pass12345")
        service_request = ServiceRequest.objects.create(
            client=alpha,
            requester=requester,
            subject="Need progress",
            description="Track progress publicly.",
        )
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.post(
            reverse("technician_request_public_update_action", args=[service_request.id]),
            data={"body": "We have identified the root cause and are preparing the fix."},
        )

        self.assertRedirects(response, reverse("technician_request_detail", args=[service_request.id]))
        public_update = ServiceRequestPublicUpdate.objects.get(service_request=service_request)
        self.assertEqual(public_update.author, self.staff_user)
        self.assertEqual(public_update.body, "We have identified the root cause and are preparing the fix.")

    def test_client_linked_staff_cannot_post_public_update_to_other_org_ticket(self):
        alpha = Client.objects.create(name="Alpha Public Locked")
        beta = Client.objects.create(name="Beta Public Locked")
        alpha_requester = User.objects.create_user(username="alpha.public.locked", password="pass12345")
        beta_requester = User.objects.create_user(username="beta.public.locked", password="pass12345")
        ServiceRequest.objects.create(
            client=alpha,
            requester=alpha_requester,
            subject="Alpha public ticket",
            description="Visible ticket.",
        )
        beta_ticket = ServiceRequest.objects.create(
            client=beta,
            requester=beta_requester,
            subject="Beta public ticket",
            description="Protected ticket.",
        )
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.post(
            reverse("technician_request_public_update_action", args=[beta_ticket.id]),
            data={"body": "This should never post."},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(ServiceRequestPublicUpdate.objects.filter(service_request=beta_ticket).exists())

    def test_client_linked_staff_cannot_update_other_org_ticket(self):
        alpha = Client.objects.create(name="Alpha Locked")
        beta = Client.objects.create(name="Beta Locked")
        alpha_requester = User.objects.create_user(username="alpha.locked", password="pass12345")
        beta_requester = User.objects.create_user(username="beta.locked", password="pass12345")
        ServiceRequest.objects.create(
            client=alpha,
            requester=alpha_requester,
            subject="Alpha visible",
            description="Visible ticket.",
        )
        beta_ticket = ServiceRequest.objects.create(
            client=beta,
            requester=beta_requester,
            subject="Beta hidden",
            description="Protected ticket.",
        )
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.post(
            reverse("technician_request_action", args=[beta_ticket.id]),
            data={"action": "assign_to_me"},
        )

        self.assertEqual(response.status_code, 404)

    def test_client_linked_staff_cannot_update_other_org_alert(self):
        alpha = Client.objects.create(name="Alpha Alerts")
        beta = Client.objects.create(name="Beta Alerts")
        alpha_machine = Machine.objects.create(hostname="ALPHA-ALERT-PC", client=alpha)
        beta_machine = Machine.objects.create(hostname="BETA-ALERT-PC", client=beta)
        Alert.objects.create(
            client=alpha,
            machine=alpha_machine,
            category=Alert.CATEGORY_OFFLINE,
            title="Alpha alert",
            message="Visible alert.",
            status=Alert.STATUS_ACTIVE,
        )
        beta_alert = Alert.objects.create(
            client=beta,
            machine=beta_machine,
            category=Alert.CATEGORY_OFFLINE,
            title="Beta alert",
            message="Hidden alert.",
            status=Alert.STATUS_ACTIVE,
        )
        ClientAccess.objects.create(user=self.staff_user, client=alpha)
        self.client.login(username="tech.admin", password="pass12345")

        response = self.client.post(
            reverse("technician_alert_action", args=[beta_alert.id]),
            data={"action": "acknowledge"},
        )

        self.assertEqual(response.status_code, 404)


class AdminScopingTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.site = AdminSite()
        self.alpha = Client.objects.create(name="Alpha")
        self.beta = Client.objects.create(name="Beta")
        self.alpha_machine = Machine.objects.create(hostname="ALPHA-ADMIN-PC", client=self.alpha)
        self.beta_machine = Machine.objects.create(hostname="BETA-ADMIN-PC", client=self.beta)
        self.staff_user = User.objects.create_user(
            username="alpha.staff",
            password="pass12345",
            is_staff=True,
        )
        ClientAccess.objects.create(user=self.staff_user, client=self.alpha)

    def test_machine_admin_queryset_is_scoped_to_client_access(self):
        request = self.factory.get("/admin/agents/machine/")
        request.user = self.staff_user

        queryset = MachineAdmin(Machine, self.site).get_queryset(request)

        self.assertIn(self.alpha_machine, queryset)
        self.assertNotIn(self.beta_machine, queryset)

    def test_service_request_admin_queryset_is_scoped_to_client_access(self):
        alpha_ticket = ServiceRequest.objects.create(
            client=self.alpha,
            machine=self.alpha_machine,
            requester=self.staff_user,
            subject="Alpha only",
            description="Visible in admin.",
        )
        teammate = User.objects.create_user(username="alpha.assigned", password="pass12345")
        ClientAccess.objects.create(user=teammate, client=self.alpha)
        assigned_ticket = ServiceRequest.objects.create(
            client=self.alpha,
            machine=self.alpha_machine,
            requester=teammate,
            assigned_to=self.staff_user,
            subject="Assigned to me",
            description="Also visible in admin.",
        )
        teammate_only_ticket = ServiceRequest.objects.create(
            client=self.alpha,
            machine=self.alpha_machine,
            requester=teammate,
            subject="Teammate only",
            description="Should stay hidden from this user.",
        )
        beta_user = User.objects.create_user(username="beta.staff", password="pass12345")
        beta_ticket = ServiceRequest.objects.create(
            client=self.beta,
            machine=self.beta_machine,
            requester=beta_user,
            subject="Beta hidden",
            description="Should not be visible.",
        )
        request = self.factory.get("/admin/agents/servicerequest/")
        request.user = self.staff_user

        queryset = ServiceRequestAdmin(ServiceRequest, self.site).get_queryset(request)

        self.assertIn(alpha_ticket, queryset)
        self.assertIn(assigned_ticket, queryset)
        self.assertNotIn(teammate_only_ticket, queryset)
        self.assertNotIn(beta_ticket, queryset)

    def test_user_admin_queryset_is_scoped_to_same_client_users(self):
        teammate = User.objects.create_user(username="alpha.teammate", password="pass12345", is_staff=True)
        ClientAccess.objects.create(user=teammate, client=self.alpha)
        outsider = User.objects.create_user(username="beta.outsider", password="pass12345", is_staff=True)
        ClientAccess.objects.create(user=outsider, client=self.beta)
        request = self.factory.get("/admin/auth/user/")
        request.user = self.staff_user

        queryset = ScopedUserAdmin(User, self.site).get_queryset(request)

        self.assertIn(self.staff_user, queryset)
        self.assertIn(teammate, queryset)
        self.assertNotIn(outsider, queryset)

    def test_client_config_modules_are_hidden_for_client_scoped_user(self):
        request = self.factory.get("/admin/")
        request.user = self.staff_user

        self.assertFalse(ClientAdmin(Client, self.site).has_module_permission(request))
        self.assertFalse(ClientAccessAdmin(ClientAccess, self.site).has_module_permission(request))
        self.assertFalse(ClientInvitationAdmin(ClientInvitation, self.site).has_module_permission(request))

    def test_client_scoped_admin_navigation_only_lists_operational_models(self):
        request = self.factory.get("/admin/")
        request.user = self.staff_user

        context = admin.site.each_context(request)

        allowed_model_names = {"Machine", "ServiceRequest", "Alert", "AuditLog"}
        for app in context["available_apps"]:
            self.assertEqual(app["app_label"], "agents")
            model_names = {model["object_name"] for model in app["models"]}
            self.assertTrue(model_names.issubset(allowed_model_names))

    def test_client_scoped_user_does_not_get_auth_module_in_admin(self):
        request = self.factory.get("/admin/auth/user/")
        request.user = self.staff_user

        has_module = ScopedUserAdmin(User, self.site).has_module_permission(request)

        self.assertFalse(has_module)
