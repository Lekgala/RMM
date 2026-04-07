import json
import secrets
import stripe
from datetime import datetime, timedelta
from hmac import compare_digest
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.contrib import messages
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import logout
from django.contrib.auth import login as auth_login
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.contrib.auth.views import LoginView
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt

from .forms import (
    AgentInstallerUploadForm,
    ClientAccessUpdateForm,
    ClientBillingForm,
    ClientInvitationAcceptForm,
    ClientInvitationForm,
    TrialSignupForm,
    ClientLoginForm,
    ClientSettingsForm,
    ExistingUserAccessForm,
    ServiceRequestForm,
    TechnicianServiceRequestNoteForm,
    TechnicianServiceRequestPublicUpdateForm,
    TechnicianServiceRequestUpdateForm,
)
from .models import (
    Alert,
    AuditLog,
    Client,
    ClientAccess,
    ClientInvitation,
    ClientNotification,
    Machine,
    ServiceRequest,
    ServiceRequestNote,
    ServiceRequestPublicUpdate,
    SubscriptionPlan,
    ClientSubscription,
)
from .notifications import (
    send_client_invitation_notification,
    send_client_invitation_accepted_notification,
    send_support_request_created_notification,
    send_support_request_public_update_notification,
    send_support_request_updated_notification,
)


NO_PENDING_COMMAND = "None"
CLIENT_REBOOT_COMMAND = "Restart-Computer -Force"
CLIENT_CLEANUP_COMMAND = 'Remove-Item "$env:TEMP\\*" -Recurse -Force -ErrorAction SilentlyContinue'
PLACEHOLDER_CLIENT_NAMES = {"defaultclient", "default client", "unassigned", "unknown", "none"}


def _parse_processes(top_processes):
    process_list = []
    if not top_processes:
        return process_list

    for raw_line in top_processes.splitlines():
        line = raw_line.strip().replace("\r", "")
        if not line:
            continue

        parts = [part.strip() for part in line.split("|")]
        if len(parts) == 3:
            try:
                cpu_val = float(parts[1])
                mem_val = float(parts[2])
                process_list.append({"name": parts[0], "cpu": f"{cpu_val:.1f}%", "memory": f"{mem_val:.1f}MB"})
            except (ValueError, IndexError):
                process_list.append({"name": parts[0], "cpu": parts[1], "memory": parts[2]})
        elif len(parts) == 2:
            process_list.append({"name": parts[0], "cpu": "-", "memory": parts[1]})
        else:
            process_list.append({"name": line, "cpu": "-", "memory": "-"})
    return process_list


def _parse_last_boot_time(raw_value):
    if not raw_value:
        return None

    parsed_value = parse_datetime(raw_value)
    if not parsed_value:
        return None
    if timezone.is_naive(parsed_value):
        return timezone.make_aware(parsed_value, timezone.get_current_timezone())
    return parsed_value


def _get_request_data(request):
    try:
        return json.loads(request.body or b"{}"), None
    except json.JSONDecodeError:
        return None, JsonResponse({"error": "Invalid JSON payload"}, status=400)


def _get_client_access_or_none(user):
    if not user.is_authenticated:
        return None
    try:
        return user.client_access
    except ClientAccess.DoesNotExist:
        return None


def _client_machine_queryset(client):
    return (
        Machine.objects.filter(client=client)
        .select_related("client")
        .prefetch_related("logs")
        .order_by("-last_seen", "hostname")
    )


def _is_placeholder_client_name(name):
    return (name or "").strip().lower() in PLACEHOLDER_CLIENT_NAMES


def _client_request_queryset(client):
    return (
        ServiceRequest.objects.filter(client=client)
        .select_related("client", "machine", "requester")
        .order_by("status", "-updated_at", "-created_at")
    )


def _client_invitation_queryset(client):
    return (
        ClientInvitation.objects.filter(client=client)
        .select_related("client", "invited_by", "accepted_by")
        .order_by("status", "-created_at")
    )


def _client_team_member_queryset(client):
    return ClientAccess.objects.filter(client=client).select_related("user").order_by("user__first_name", "user__username")


def _available_client_user_queryset():
    return User.objects.filter(client_access__isnull=True).order_by("first_name", "username")


def _client_notification_queryset(access):
    return ClientNotification.objects.filter(client=access.client).filter(
        Q(user__isnull=True) | Q(user=access.user)
    )


def _unread_notification_count(access):
    return _client_notification_queryset(access).filter(is_read=False).count()


def _render_error_page(request, template_name, status_code, title, message, action_url="/dashboard/", action_label="Go to Dashboard", extra_context=None):
    context = {
        "error_code": status_code,
        "error_title": title,
        "error_message": message,
        "action_url": action_url,
        "action_label": action_label,
    }
    if extra_context:
        context.update(extra_context)
    return render(request, template_name, context, status=status_code)


def landing_page(request):
    from .models import SubscriptionPlan
    plans = SubscriptionPlan.objects.filter(is_active=True).order_by("monthly_price_cents")
    return render(request, "agents/landing.html", {"plans": plans})


def client_trial_signup(request):
    available_plans = SubscriptionPlan.objects.filter(is_active=True).order_by("monthly_price_cents")
    if not available_plans.exists():
        messages.error(request, "No trial plans are configured yet. Please contact support.")
        return redirect("landing_page")

    initial = {}
    requested_plan_slug = (request.GET.get("plan") or "").strip()
    if requested_plan_slug:
        preselected_plan = available_plans.filter(slug=requested_plan_slug).first()
        if preselected_plan:
            initial["plan"] = preselected_plan

    if request.method == "POST":
        form = TrialSignupForm(request.POST)
        if form.is_valid():
            cleaned = form.cleaned_data
            trial_end = timezone.now().date() + timedelta(days=30)

            with transaction.atomic():
                client = Client.objects.create(
                    name=cleaned["company_name"],
                    contact_email=cleaned["email"],
                )

                full_name_parts = cleaned["full_name"].split(maxsplit=1)
                first_name = full_name_parts[0]
                last_name = full_name_parts[1] if len(full_name_parts) > 1 else ""

                user = User.objects.create_user(
                    username=cleaned["username"],
                    email=cleaned["email"],
                    password=cleaned["password1"],
                    first_name=first_name,
                    last_name=last_name,
                )

                ClientAccess.objects.create(
                    user=user,
                    client=client,
                    role=ClientAccess.ROLE_OWNER,
                    can_restart_machines=True,
                )

                ClientSubscription.objects.create(
                    client=client,
                    plan=cleaned["plan"],
                    status=ClientSubscription.STATUS_TRIALING,
                    start_date=timezone.now().date(),
                    trial_end=trial_end,
                    current_period_end=trial_end,
                    billing_email=cleaned["email"],
                )

            auth_login(request, user)
            messages.success(
                request,
                "Your 30-day trial is active. Download the Windows agent and deploy it to your machines.",
            )
            return redirect("client_dashboard")
    else:
        form = TrialSignupForm(initial=initial)

    return render(
        request,
        "agents/client_trial_signup.html",
        {
            "signup_form": form,
            "available_plans": available_plans,
        },
    )


@login_required(login_url="/client/login/")
def client_download_agent(request):
    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    agent_path = Path(getattr(settings, "AGENT_EXE_PATH", settings.BASE_DIR / "media" / "deployments" / "tj-rmm-agent.exe"))
    if agent_path.exists() and agent_path.is_file():
        safe_client_slug = "".join(ch if ch.isalnum() else "-" for ch in access.client.name.lower()).strip("-") or "client"
        download_name = f"tj-rmm-agent-{safe_client_slug}.exe"
        response = FileResponse(agent_path.open("rb"), as_attachment=True, filename=download_name)
        return response

    script_path = Path(getattr(settings, "AGENT_SCRIPT_PATH", settings.BASE_DIR / "media" / "deployments" / "tj-rmm-agent.ps1"))
    if script_path.exists() and script_path.is_file():
        safe_client_slug = "".join(ch if ch.isalnum() else "-" for ch in access.client.name.lower()).strip("-") or "client"
        download_name = f"tj-rmm-agent-{safe_client_slug}.ps1"
        messages.warning(
            request,
            "Installer EXE is not published yet. Downloading PowerShell agent script instead.",
        )
        response = FileResponse(script_path.open("rb"), as_attachment=True, filename=download_name)
        return response

    messages.error(
        request,
        "Agent installer is not available yet. Ask your administrator to publish tj-rmm-agent.exe.",
    )
    return redirect("client_dashboard")


@login_required(login_url="/client/login/")
def client_agent_connection_test(request):
    if request.method != "POST":
        return redirect("client_dashboard")

    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    hub_url = (request.POST.get("hub_url") or "").strip()
    if not hub_url:
        base_url = (settings.CLIENT_PORTAL_BASE_URL or "http://127.0.0.1:8000").rstrip("/")
        hub_url = f"{base_url}/api/hub/"

    payload = {
        "hostname": "client-connectivity-test",
        "client": access.client.name,
        "connectivity_test": True,
    }

    try:
        request_data = json.dumps(payload).encode("utf-8")
        test_request = Request(
            hub_url,
            data=request_data,
            headers={
                "Content-Type": "application/json",
                "X-API-KEY": settings.AGENT_KEY,
            },
            method="POST",
        )
        with urlopen(test_request, timeout=8) as response:
            status_code = getattr(response, "status", 200)
            response_data = json.loads(response.read().decode("utf-8") or "{}")

        if status_code == 200 and response_data.get("status") == "ok":
            messages.success(request, f"Connection test passed for {hub_url}")
        else:
            messages.error(request, f"Connection test failed for {hub_url}")
    except HTTPError as exc:
        messages.error(request, f"Connection test failed: HTTP {exc.code} at {hub_url}")
    except URLError as exc:
        messages.error(request, f"Connection test failed: {exc.reason}")
    except Exception as exc:
        messages.error(request, "Connection test failed. Please verify the server URL and API key.")

    return redirect("client_dashboard")


def _latest_alerts(queryset, limit=8):
    return list(queryset.select_related("machine", "client").order_by("status", "-created_at")[:limit])


def _alert_filter_from_request(request):
    selected = request.GET.get("alert_status", "open").strip().lower()
    if selected not in {"open", "resolved", "all"}:
        selected = "open"
    return selected


def _apply_alert_filter(queryset, selected_filter):
    if selected_filter == "resolved":
        return queryset.filter(status=Alert.STATUS_RESOLVED)
    if selected_filter == "all":
        return queryset
    return queryset.filter(status__in=[Alert.STATUS_ACTIVE, Alert.STATUS_ACKNOWLEDGED])


def _open_alert(machine, category, severity, title, message):
    if not machine.client_id:
        return None

    alert = (
        Alert.objects.filter(
            machine=machine,
            client=machine.client,
            category=category,
            status__in=[Alert.STATUS_ACTIVE, Alert.STATUS_ACKNOWLEDGED],
        )
        .order_by("-created_at")
        .first()
    )
    if alert:
        alert.severity = severity
        alert.title = title
        alert.message = message
        alert.save(update_fields=["severity", "title", "message", "updated_at"])
        return alert

    return Alert.objects.create(
        machine=machine,
        client=machine.client,
        category=category,
        severity=severity,
        title=title,
        message=message,
    )


def _resolve_alerts(machine, category, actor_label):
    now = timezone.now()
    Alert.objects.filter(
        machine=machine,
        category=category,
        status__in=[Alert.STATUS_ACTIVE, Alert.STATUS_ACKNOWLEDGED],
    ).update(
        status=Alert.STATUS_RESOLVED,
        resolved_by=actor_label,
        resolved_at=now,
    )


def _sync_machine_alerts(machine):
    if machine.client_id:
        if machine.disk_usage_percent is not None and machine.disk_usage_percent > 90:
            _open_alert(
                machine,
                Alert.CATEGORY_DISK,
                Alert.SEVERITY_WARNING,
                f"Low disk space on {machine.hostname}",
                f"Disk usage is at {machine.disk_usage_percent}% on {machine.hostname}.",
            )
        else:
            _resolve_alerts(machine, Alert.CATEGORY_DISK, "System auto-resolve")

        if machine.is_online():
            _resolve_alerts(machine, Alert.CATEGORY_OFFLINE, "System auto-resolve")
        else:
            last_seen_text = timezone.localtime(machine.last_seen).strftime("%Y-%m-%d %H:%M") if machine.last_seen else "unknown"
            _open_alert(
                machine,
                Alert.CATEGORY_OFFLINE,
                Alert.SEVERITY_CRITICAL,
                f"{machine.hostname} is offline",
                f"No recent heartbeat has been seen from {machine.hostname}. Last seen: {last_seen_text}.",
            )


def _alert_actor_label(request):
    if request.user.is_authenticated:
        return request.user.username
    return "Technician dashboard"


def _request_actor_label(request):
    if request.user.is_authenticated:
        return request.user.username
    return "System"


def _initialize_stripe():
    if not settings.STRIPE_SECRET_KEY:
        raise RuntimeError("Stripe secret key is not configured.")
    stripe.api_key = settings.STRIPE_SECRET_KEY


def _create_checkout_session(access, plan, billing_email):
    _initialize_stripe()
    session = stripe.checkout.Session.create(
        payment_method_types=["card"],
        mode="subscription",
        line_items=[
            {
                "price_data": {
                    "currency": settings.STRIPE_CURRENCY,
                    "product_data": {
                        "name": plan.name,
                        "description": plan.description,
                    },
                    "recurring": {"interval": "month"},
                    "unit_amount": plan.monthly_price_cents,
                },
                "quantity": 1,
            }
        ],
        subscription_data={
            "metadata": {
                "client_id": str(access.client.id),
                "plan_id": str(plan.id),
            }
        },
        customer_email=billing_email,
        success_url=settings.STRIPE_SUCCESS_URL,
        cancel_url=settings.STRIPE_CANCEL_URL,
    )
    return session


@csrf_exempt
def stripe_webhook(request):
    payload = request.body
    sig_header = request.META.get("HTTP_STRIPE_SIGNATURE", "")
    try:
        _initialize_stripe()
        event = stripe.Webhook.construct_event(
            payload, sig_header, settings.STRIPE_WEBHOOK_SECRET
        )
    except ValueError:
        return HttpResponseForbidden("Invalid payload")
    except stripe.error.SignatureVerificationError:
        return HttpResponseForbidden("Invalid signature")

    event_type = event["type"]
    data = event["data"]["object"]

    if event_type == "checkout.session.completed":
        session = stripe.checkout.Session.retrieve(
            data["id"], expand=["subscription"]
        )
        client_id = session["metadata"].get("client_id")
        plan_id = session["metadata"].get("plan_id")
        billing_email = session.get("customer_email") or ""
        stripe_customer_id = session.get("customer") or ""
        stripe_subscription_id = session.get("subscription") or ""

        if client_id and plan_id and stripe_subscription_id:
            try:
                client = Client.objects.get(id=client_id)
                plan = SubscriptionPlan.objects.get(id=plan_id)
            except (Client.DoesNotExist, SubscriptionPlan.DoesNotExist):
                return JsonResponse({"status": "ignored"}, status=200)

            subscription_data = {
                "status": ClientSubscription.STATUS_ACTIVE,
                "billing_email": billing_email,
                "stripe_customer_id": stripe_customer_id,
                "stripe_subscription_id": stripe_subscription_id,
                "start_date": timezone.now().date(),
            }
            stripe_sub = stripe.Subscription.retrieve(stripe_subscription_id)
            if stripe_sub.get("current_period_end"):
                subscription_data["current_period_end"] = datetime.fromtimestamp(
                    stripe_sub["current_period_end"], timezone.utc
                ).date()

            ClientSubscription.objects.update_or_create(
                client=client,
                plan=plan,
                defaults=subscription_data,
            )

    elif event_type == "invoice.payment_failed":
        stripe_subscription_id = data.get("subscription")
        if stripe_subscription_id:
            ClientSubscription.objects.filter(
                stripe_subscription_id=stripe_subscription_id
            ).update(status=ClientSubscription.STATUS_PAST_DUE)

    return JsonResponse({"status": "success"}, status=200)


def _technician_request_queryset():
    return (
        ServiceRequest.objects.select_related("client", "machine", "requester", "assigned_to")
        .order_by("status", "-updated_at", "-created_at")
    )


def _staff_client_access(request):
    access = _get_client_access_or_none(request.user)
    if access and not request.user.is_superuser:
        return access
    return None


def _staff_scoped_machine_or_404(request, machine_id):
    machine_filters = {"id": machine_id}
    staff_client_access = _staff_client_access(request)
    if staff_client_access:
        machine_filters["client"] = staff_client_access.client
    return get_object_or_404(
        Machine.objects.select_related("client").prefetch_related("logs", "alerts", "service_requests"),
        **machine_filters,
    )


def _staff_scoped_request_or_404(request, request_id):
    request_filters = {"id": request_id}
    staff_client_access = _staff_client_access(request)
    if staff_client_access:
        request_filters["client"] = staff_client_access.client
    return get_object_or_404(
        ServiceRequest.objects.select_related("client", "machine", "requester", "assigned_to").prefetch_related("notes__author"),
        **request_filters,
    )


def _require_client_capability(access, capability_name, denied_message):
    capability = getattr(access, capability_name)
    if capability():
        return None
    return denied_message


def _can_manage_team_member(manager_access, target_access):
    if manager_access.client_id != target_access.client_id:
        return False
    if manager_access.user_id == target_access.user_id:
        return False
    if manager_access.role == ClientAccess.ROLE_OWNER:
        return True
    if manager_access.role == ClientAccess.ROLE_ADMIN:
        return target_access.role in {ClientAccess.ROLE_MEMBER, ClientAccess.ROLE_VIEWER}
    return False


def _team_member_management_form(access, member, data=None):
    return ClientAccessUpdateForm(
        data,
        instance=member,
        prefix=f"member-{member.id}",
        allow_owner=access.role == ClientAccess.ROLE_OWNER,
    )


def _build_client_team_context(request, access, invitation_form=None, team_member_forms=None, existing_user_form=None):
    team_members = list(_client_team_member_queryset(access.client))
    if team_member_forms is None:
        team_member_forms = {
            member.id: _team_member_management_form(access, member)
            for member in team_members
        }
    for member in team_members:
        member.management_form = team_member_forms.get(member.id)
    return {
        "client_access": access,
        "team_members": team_members,
        "pending_invitations": list(
            _client_invitation_queryset(access.client).filter(status=ClientInvitation.STATUS_PENDING)[:20]
        ),
        "invitation_form": invitation_form or ClientInvitationForm(),
        "existing_user_form": existing_user_form
        or ExistingUserAccessForm(
            user_queryset=_available_client_user_queryset(),
            allow_owner=access.role == ClientAccess.ROLE_OWNER,
        ),
        "base_url": request.build_absolute_uri("/").rstrip("/"),
        "notification_count": _unread_notification_count(access),
    }


def _build_client_settings_context(access, settings_form=None):
    return {
        "client_access": access,
        "settings_form": settings_form or ClientSettingsForm(instance=access.client),
        "subscription": access.client.current_subscription,
        "available_plans": SubscriptionPlan.objects.filter(is_active=True).order_by("monthly_price_cents"),
        "notification_count": _unread_notification_count(access),
    }


def _client_subscription_issue_redirect(request, access):
    subscription = access.client.current_subscription
    if not subscription or not subscription.is_active():
        messages.warning(
            request,
            "Your organization's subscription is not active. Please update billing to continue using the portal.",
        )
        return redirect("client_billing")
    return None


def _client_portal_access_or_redirect(request, allow_billing_redirect=True):
    access = _get_client_access_or_none(request.user)
    if access:
        if allow_billing_redirect:
            issue_redirect = _client_subscription_issue_redirect(request, access)
            if issue_redirect:
                return None, issue_redirect
        return access, None
    logout(request)
    messages.error(request, "This account is not linked to a client portal.")
    return None, redirect("client_login")


def _ensure_staff_dashboard_access(request):
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path(), "/admin/login/")
    if request.user.is_staff or request.user.is_superuser:
        return None

    access = _get_client_access_or_none(request.user)
    if access:
        messages.info(request, "Your account is limited to your client portal.")
        return redirect("client_dashboard")

    auth_logout(request)
    messages.error(request, "This account is not allowed to access the technician dashboard.")
    return redirect("client_login")


def dashboard(request):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    query = request.GET.get("search", "").strip()
    alert_filter = _alert_filter_from_request(request)
    staff_client_access = _staff_client_access(request)
    machines_qs = (
        Machine.objects.select_related("client")
        .prefetch_related("logs")
        .order_by("-last_seen")
    )
    if staff_client_access:
        machines_qs = machines_qs.filter(client=staff_client_access.client)
    if query:
        machines_qs = machines_qs.filter(
            Q(hostname__icontains=query)
            | Q(client__name__icontains=query)
            | Q(ip_address__icontains=query)
            | Q(os_info__icontains=query)
            | Q(manufacturer__icontains=query)
            | Q(model_name__icontains=query)
        )
    machines = list(machines_qs)
    total_online = sum(1 for machine in machines if machine.is_online())
    total_offline = len(machines) - total_online

    for machine in machines:
        _sync_machine_alerts(machine)
        machine.process_list = _parse_processes(machine.top_processes)

    alert_queryset = Alert.objects.select_related("machine", "client")
    if staff_client_access:
        alert_queryset = alert_queryset.filter(client=staff_client_access.client)
    filtered_alerts = _apply_alert_filter(alert_queryset, alert_filter).order_by("status", "-created_at")
    request_queryset = _technician_request_queryset()
    if staff_client_access:
        request_queryset = request_queryset.filter(client=staff_client_access.client)
    support_requests = list(request_queryset[:8])

    active_alerts_queryset = Alert.objects.filter(
        status__in=[Alert.STATUS_ACTIVE, Alert.STATUS_ACKNOWLEDGED]
    )
    open_requests_queryset = ServiceRequest.objects.filter(
        status__in=[ServiceRequest.STATUS_OPEN, ServiceRequest.STATUS_IN_PROGRESS]
    )
    if staff_client_access:
        active_alerts_queryset = active_alerts_queryset.filter(client=staff_client_access.client)
        open_requests_queryset = open_requests_queryset.filter(client=staff_client_access.client)

    context = {
        "staff_client_access": staff_client_access,
        "machines": machines,
        "total_online": total_online,
        "total_offline": total_offline,
        "total_alerts": active_alerts_queryset.count(),
        "total_requests": open_requests_queryset.count(),
        "open_alerts": list(filtered_alerts[:8]),
        "support_requests": support_requests,
        "alert_filter": alert_filter,
        "search_query": query,
    }
    return render(request, "agents/dashboard.html", context)


def technician_agent_installer(request):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    agent_path = Path(settings.AGENT_EXE_PATH)
    published_installer = None
    if agent_path.exists() and agent_path.is_file():
        stat_info = agent_path.stat()
        published_installer = {
            "name": agent_path.name,
            "size_mb": round(stat_info.st_size / (1024 * 1024), 2),
            "updated_at": datetime.fromtimestamp(stat_info.st_mtime, timezone.get_current_timezone()),
        }

    if request.method == "POST":
        form = AgentInstallerUploadForm(request.POST, request.FILES)
        if form.is_valid():
            installer = form.cleaned_data["installer"]
            agent_path.parent.mkdir(parents=True, exist_ok=True)
            with agent_path.open("wb+") as destination:
                for chunk in installer.chunks():
                    destination.write(chunk)

            messages.success(request, "Agent installer uploaded and published successfully.")
            return redirect("technician_agent_installer")
    else:
        form = AgentInstallerUploadForm()

    context = {
        "staff_client_access": _staff_client_access(request),
        "upload_form": form,
        "published_installer": published_installer,
        "agent_exe_path": str(agent_path),
    }
    return render(request, "agents/technician_agent_installer.html", context)


def technician_machines(request):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    query = request.GET.get("search", "").strip()
    staff_client_access = _staff_client_access(request)
    machines_qs = (
        Machine.objects.select_related("client")
        .prefetch_related("logs")
        .order_by("-last_seen", "hostname")
    )
    if staff_client_access:
        machines_qs = machines_qs.filter(client=staff_client_access.client)
    if query:
        machines_qs = machines_qs.filter(
            Q(hostname__icontains=query)
            | Q(client__name__icontains=query)
            | Q(ip_address__icontains=query)
            | Q(os_info__icontains=query)
            | Q(manufacturer__icontains=query)
            | Q(model_name__icontains=query)
        )
    machines = list(machines_qs[:50])
    for machine in machines:
        _sync_machine_alerts(machine)
        machine.process_list = _parse_processes(machine.top_processes)

    context = {
        "staff_client_access": staff_client_access,
        "machines": machines,
        "search_query": query,
        "total_online": sum(1 for machine in machines if machine.is_online()),
        "total_offline": sum(1 for machine in machines if not machine.is_online()),
    }
    return render(request, "agents/technician_machines.html", context)


def technician_machine_detail(request, machine_id):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    machine = _staff_scoped_machine_or_404(request, machine_id)
    _sync_machine_alerts(machine)
    machine.process_list = _parse_processes(machine.top_processes)

    context = {
        "staff_client_access": _staff_client_access(request),
        "machine": machine,
        "recent_alerts": list(machine.alerts.order_by("status", "-created_at")[:8]),
        "recent_requests": list(machine.service_requests.select_related("requester", "assigned_to").order_by("-updated_at")[:8]),
        "recent_logs": list(machine.logs.all()[:15]),
    }
    return render(request, "agents/technician_machine_detail.html", context)


def technician_machine_action(request, machine_id):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    if request.method != "POST":
        return redirect("technician_machine_detail", machine_id=machine_id)

    machine = _staff_scoped_machine_or_404(request, machine_id)
    action = request.POST.get("action")

    if action == "reboot":
        machine.pending_command = CLIENT_REBOOT_COMMAND
        machine.save(update_fields=["pending_command", "last_seen"])
        AuditLog.objects.create(
            machine=machine,
            action="Technician Reboot Queued",
            details=f"Queued by {request.user.username}",
        )
        messages.success(request, f"Queued a reboot for {machine.hostname}.")
    elif action == "cleanup":
        machine.pending_command = CLIENT_CLEANUP_COMMAND
        machine.save(update_fields=["pending_command", "last_seen"])
        AuditLog.objects.create(
            machine=machine,
            action="Technician Cleanup Queued",
            details=f"Queued by {request.user.username}",
        )
        messages.success(request, f"Queued cleanup for {machine.hostname}.")

    return redirect("technician_machine_detail", machine_id=machine.id)


def technician_alerts(request):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    query = request.GET.get("search", "").strip()
    alert_filter = _alert_filter_from_request(request)
    staff_client_access = _staff_client_access(request)
    alerts_qs = Alert.objects.select_related("machine", "client").order_by("status", "-created_at")
    if staff_client_access:
        alerts_qs = alerts_qs.filter(client=staff_client_access.client)
    if query:
        alerts_qs = alerts_qs.filter(
            Q(title__icontains=query)
            | Q(message__icontains=query)
            | Q(machine__hostname__icontains=query)
            | Q(client__name__icontains=query)
        )
    alerts = list(_apply_alert_filter(alerts_qs, alert_filter)[:30])

    context = {
        "staff_client_access": staff_client_access,
        "alerts": alerts,
        "alert_filter": alert_filter,
        "search_query": query,
        "total_alerts": alerts_qs.filter(
            status__in=[Alert.STATUS_ACTIVE, Alert.STATUS_ACKNOWLEDGED]
        ).count(),
    }
    return render(request, "agents/technician_alerts.html", context)


def technician_request_detail(request, request_id):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    service_request = _staff_scoped_request_or_404(request, request_id)
    context = {
        "staff_client_access": _staff_client_access(request),
        "service_request": service_request,
        "update_form": TechnicianServiceRequestUpdateForm(instance=service_request),
        "note_form": TechnicianServiceRequestNoteForm(),
        "public_update_form": TechnicianServiceRequestPublicUpdateForm(),
        "notes": list(service_request.notes.all()[:20]),
        "public_updates": list(service_request.public_updates.all()[:20]),
    }
    return render(request, "agents/technician_request_detail.html", context)


def technician_request_note_action(request, request_id):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    if request.method != "POST":
        return redirect("technician_request_detail", request_id=request_id)

    service_request = _staff_scoped_request_or_404(request, request_id)
    form = TechnicianServiceRequestNoteForm(request.POST)
    if form.is_valid():
        note = form.save(commit=False)
        note.service_request = service_request
        note.author = request.user
        note.save()
        messages.success(request, "Internal note added.")
    else:
        messages.error(request, "We could not save that internal note.")
    return redirect("technician_request_detail", request_id=service_request.id)


def technician_request_public_update_action(request, request_id):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    if request.method != "POST":
        return redirect("technician_request_detail", request_id=request_id)

    service_request = _staff_scoped_request_or_404(request, request_id)
    form = TechnicianServiceRequestPublicUpdateForm(request.POST)
    if form.is_valid():
        public_update = form.save(commit=False)
        public_update.service_request = service_request
        public_update.author = request.user
        public_update.save()
        send_support_request_public_update_notification(service_request, public_update)
        messages.success(request, "Client-facing progress update posted.")
    else:
        messages.error(request, "We could not save that client-facing update.")
    return redirect("technician_request_detail", request_id=service_request.id)


class ClientLoginView(LoginView):
    template_name = "agents/client_login.html"
    authentication_form = ClientLoginForm
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not _get_client_access_or_none(request.user):
            messages.error(request, "This account is not linked to a client portal.")
            logout(request)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        response = super().form_valid(form)
        if not _get_client_access_or_none(self.request.user):
            messages.error(self.request, "This account is not linked to a client portal.")
            logout(self.request)
            return redirect("client_login")
        return response

    def get_success_url(self):
        return self.get_redirect_url() or "/client/"


@login_required(login_url="/client/login/")
def client_dashboard(request):
    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    alert_filter = _alert_filter_from_request(request)
    machines = list(_client_machine_queryset(access.client))
    for machine in machines:
        _sync_machine_alerts(machine)
        machine.process_list = _parse_processes(machine.top_processes)

    client_alert_queryset = Alert.objects.filter(client=access.client).select_related("machine", "client")
    filtered_alerts = _apply_alert_filter(client_alert_queryset, alert_filter).order_by("status", "-created_at")

    context = {
        "client_access": access,
        "subscription": access.client.current_subscription,
        "default_agent_hub_url": ((settings.CLIENT_PORTAL_BASE_URL or "http://127.0.0.1:8000").rstrip("/") + "/api/hub/"),
        "machines": machines,
        "notification_count": _unread_notification_count(access),
        "total_online": sum(1 for machine in machines if machine.is_online()),
        "total_alerts": Alert.objects.filter(
            client=access.client,
            status__in=[Alert.STATUS_ACTIVE, Alert.STATUS_ACKNOWLEDGED],
        ).count(),
        "open_alerts": _latest_alerts(
            Alert.objects.filter(
                client=access.client,
                status__in=[Alert.STATUS_ACTIVE, Alert.STATUS_ACKNOWLEDGED],
            )
        ),
        "alert_history": list(filtered_alerts[:10]),
        "alert_filter": alert_filter,
    }
    return render(request, "agents/client_dashboard.html", context)


@login_required(login_url="/client/login/")
def client_service_requests(request):
    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    context = {
        "client_access": access,
        "machines": list(_client_machine_queryset(access.client)),
        "service_requests": list(_client_request_queryset(access.client)[:25]),
        "request_form": ServiceRequestForm(machine_queryset=_client_machine_queryset(access.client)),
        "notification_count": _unread_notification_count(access),
    }
    return render(request, "agents/client_service_requests.html", context)


@login_required(login_url="/client/login/")
def client_request_updates(request):
    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    context = {
        "client_access": access,
        "service_requests": list(
            _client_request_queryset(access.client).prefetch_related("public_updates__author")[:25]
        ),
        "notification_count": _unread_notification_count(access),
    }
    return render(request, "agents/client_request_updates.html", context)


@login_required(login_url="/client/login/")
def client_notifications(request):
    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    context = {
        "client_access": access,
        "notifications": list(_client_notification_queryset(access)[:30]),
        "notification_count": _unread_notification_count(access),
    }
    return render(request, "agents/client_notifications.html", context)


@login_required(login_url="/client/login/")
def client_notification_action(request, notification_id):
    if request.method != "POST":
        return redirect("client_notifications")

    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    notification = get_object_or_404(ClientNotification, id=notification_id, client=access.client)
    if notification.user_id and notification.user_id != request.user.id:
        return redirect("client_notifications")

    if request.POST.get("action") == "mark_read" and not notification.is_read:
        notification.is_read = True
        notification.read_at = timezone.now()
        notification.save(update_fields=["is_read", "read_at"])
    return redirect("client_notifications")


@login_required(login_url="/client/login/")
def client_notification_mark_all_read(request):
    if request.method != "POST":
        return redirect("client_notifications")

    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    _client_notification_queryset(access).filter(is_read=False).update(
        is_read=True,
        read_at=timezone.now(),
    )
    return redirect("client_notifications")


@login_required(login_url="/client/login/")
def client_settings(request):
    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    denied_message = _require_client_capability(
        access,
        "can_manage_team",
        "Only client owners and admins can manage company settings.",
    )
    if denied_message:
        messages.error(request, denied_message)
        return redirect("client_dashboard")

    if request.method == "POST":
        form = ClientSettingsForm(request.POST, request.FILES, instance=access.client)
        if form.is_valid():
            form.save()
            messages.success(request, "Company settings updated.")
            return redirect("client_settings")
        return render(request, "agents/client_settings.html", _build_client_settings_context(access, form))

    return render(request, "agents/client_settings.html", _build_client_settings_context(access))


def _build_client_billing_context(access, billing_form=None, stripe_enabled=None):
    subscription = access.client.current_subscription
    if stripe_enabled is None:
        stripe_enabled = bool(settings.STRIPE_SECRET_KEY)
    return {
        "client_access": access,
        "subscription": subscription,
        "available_plans": SubscriptionPlan.objects.filter(is_active=True).order_by("monthly_price_cents"),
        "billing_form": billing_form
        or ClientBillingForm(
            initial={
                "plan": subscription.plan if subscription else None,
                "billing_email": subscription.billing_email if subscription else access.client.contact_email,
            }
        ),
        "stripe_enabled": stripe_enabled,
        "notification_count": _unread_notification_count(access),
    }


@login_required(login_url="/client/login/")
def client_billing(request):
    access, redirect_response = _client_portal_access_or_redirect(request, allow_billing_redirect=False)
    if redirect_response:
        return redirect_response

    subscription = access.client.current_subscription
    stripe_enabled = bool(settings.STRIPE_SECRET_KEY)
    if request.method == "POST":
        form = ClientBillingForm(request.POST)
        if form.is_valid():
            plan = form.cleaned_data["plan"]
            billing_email = form.cleaned_data["billing_email"]
            if subscription and subscription.plan_id == plan.id:
                subscription.billing_email = billing_email
                subscription.status = ClientSubscription.STATUS_ACTIVE
                if not subscription.current_period_end:
                    subscription.current_period_end = timezone.now().date() + timedelta(days=30)
                subscription.save()
                messages.success(request, "Billing settings updated.")
                return redirect("client_billing")

            if not stripe_enabled:
                messages.error(
                    request,
                    "Stripe billing is not configured. Please contact your service administrator.",
                )
                return render(
                    request,
                    "agents/client_billing.html",
                    _build_client_billing_context(access, form, stripe_enabled=stripe_enabled),
                )

            try:
                session = _create_checkout_session(access, plan, billing_email)
                return redirect(session.url)
            except Exception as exc:
                messages.error(request, "Unable to initiate Stripe checkout. Please try again later.")
                return render(
                    request,
                    "agents/client_billing.html",
                    _build_client_billing_context(access, form, stripe_enabled=stripe_enabled),
                )

        return render(request, "agents/client_billing.html", _build_client_billing_context(access, form, stripe_enabled=stripe_enabled))

    if request.GET.get("session_id"):
        messages.success(
            request,
            "Your payment is complete. The subscription will be activated once Stripe confirms the transaction.",
        )

    return render(request, "agents/client_billing.html", _build_client_billing_context(access, stripe_enabled=stripe_enabled))


@login_required(login_url="/client/login/")
def client_submit_request(request):
    if request.method != "POST":
        return redirect("client_service_requests")

    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response
    denied_message = _require_client_capability(
        access,
        "can_submit_tickets",
        "Your role is view-only and cannot submit support requests.",
    )
    if denied_message:
        messages.error(request, denied_message)
        return redirect("client_service_requests")

    machine_queryset = _client_machine_queryset(access.client)
    form = ServiceRequestForm(
        request.POST,
        machine_queryset=machine_queryset,
    )
    if form.is_valid():
        service_request = form.save(commit=False)
        service_request.client = access.client
        service_request.requester = request.user
        service_request.save()
        if service_request.machine:
            AuditLog.objects.create(
                machine=service_request.machine,
                action="Client Support Request Submitted",
                details=f"{service_request.subject} ({service_request.priority}) by {request.user.username}",
            )
        send_support_request_created_notification(service_request)
        messages.success(request, "Your support request has been submitted.")
        return redirect("client_service_requests")

    context = {
        "client_access": access,
        "machines": list(machine_queryset),
        "service_requests": list(_client_request_queryset(access.client)[:10]),
        "request_form": form,
    }
    return render(request, "agents/client_service_requests.html", context)


@login_required(login_url="/client/login/")
def client_machine_detail(request, machine_id):
    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    machine = get_object_or_404(Machine, id=machine_id, client=access.client)
    _sync_machine_alerts(machine)
    machine.process_list = _parse_processes(machine.top_processes)

    context = {
        "client_access": access,
        "machine": machine,
        "can_manage": access.can_manage_devices(),
        "can_restart": access.can_restart(),
        "recent_alerts": list(machine.alerts.order_by("status", "-created_at")[:8]),
        "recent_requests": list(machine.service_requests.select_related("requester", "assigned_to").order_by("-updated_at")[:8]),
        "recent_logs": list(machine.logs.all()[:15]),
        "notification_count": _unread_notification_count(access),
    }
    return render(request, "agents/client_machine_detail.html", context)


@login_required(login_url="/client/login/")
def client_machine_action(request, machine_id):
    if request.method != "POST":
        return redirect("client_dashboard")

    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response
    denied_message = _require_client_capability(
        access,
        "can_manage_devices",
        "Your role does not allow machine management actions.",
    )
    if denied_message:
        messages.error(request, denied_message)
        return redirect("client_dashboard")

    machine = get_object_or_404(Machine, id=machine_id, client=access.client)
    action = request.POST.get("action")

    if action == "toggle_maintenance":
        machine.auto_maintenance = not machine.auto_maintenance
        machine.save(update_fields=["auto_maintenance"])
        AuditLog.objects.create(
            machine=machine,
            action="Client Maintenance Preference Updated",
            details=f"Auto maintenance set to {machine.auto_maintenance} by {request.user.username}",
        )
    elif action == "reboot":
        if not access.can_restart():
            messages.error(request, "Your client account cannot queue reboots.")
            return redirect("client_dashboard")
        machine.pending_command = CLIENT_REBOOT_COMMAND
        machine.save(update_fields=["pending_command", "last_seen"])
        AuditLog.objects.create(
            machine=machine,
            action="Client Reboot Queued",
            details=f"Queued by {request.user.username}",
        )
    elif action == "cleanup":
        machine.pending_command = CLIENT_CLEANUP_COMMAND
        machine.save(update_fields=["pending_command", "last_seen"])
        AuditLog.objects.create(
            machine=machine,
            action="Client Cleanup Queued",
            details=f"Queued by {request.user.username}",
        )

    return redirect("client_dashboard")


def client_logout(request):
    auth_logout(request)
    messages.success(request, "You have been signed out.")
    return redirect("client_login")


@login_required(login_url="/client/login/")
def client_alert_action(request, alert_id):
    if request.method != "POST":
        return redirect("client_dashboard")

    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    alert = get_object_or_404(Alert, id=alert_id, client=access.client)
    action = request.POST.get("action")
    now = timezone.now()

    if action == "acknowledge" and alert.status == Alert.STATUS_ACTIVE:
        alert.status = Alert.STATUS_ACKNOWLEDGED
        alert.acknowledged_by = request.user.username
        alert.acknowledged_at = now
        alert.save(update_fields=["status", "acknowledged_by", "acknowledged_at", "updated_at"])
    elif action == "resolve" and alert.status != Alert.STATUS_RESOLVED:
        alert.status = Alert.STATUS_RESOLVED
        alert.resolved_by = request.user.username
        alert.resolved_at = now
        alert.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])

    return redirect("client_dashboard")


@login_required(login_url="/client/login/")
def client_request_action(request, request_id):
    if request.method != "POST":
        return redirect("client_service_requests")

    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    service_request = get_object_or_404(ServiceRequest, id=request_id, client=access.client)
    action = request.POST.get("action")

    if action == "close" and service_request.status != ServiceRequest.STATUS_CLOSED:
        service_request.status = ServiceRequest.STATUS_CLOSED
        service_request.closed_by = _request_actor_label(request)
        service_request.closed_at = timezone.now()
        service_request.save(update_fields=["status", "closed_by", "closed_at", "updated_at"])
        messages.success(request, "Request closed.")
    elif action == "reopen" and service_request.status == ServiceRequest.STATUS_CLOSED:
        service_request.status = ServiceRequest.STATUS_OPEN
        service_request.closed_by = ""
        service_request.closed_at = None
        service_request.save(update_fields=["status", "closed_by", "closed_at", "updated_at"])
        messages.success(request, "Request reopened.")

    return redirect("client_service_requests")


def technician_service_requests(request):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    request_status = request.GET.get("request_status", "open").strip().lower()
    if request_status not in {"open", "resolved", "all"}:
        request_status = "open"
    ownership = request.GET.get("ownership", "all").strip().lower()
    if ownership not in {"all", "mine", "unassigned"}:
        ownership = "all"

    service_requests = _technician_request_queryset()
    staff_client_access = _staff_client_access(request)
    if staff_client_access:
        service_requests = service_requests.filter(client=staff_client_access.client)
    if request_status == "open":
        service_requests = service_requests.filter(
            status__in=[ServiceRequest.STATUS_OPEN, ServiceRequest.STATUS_IN_PROGRESS]
        )
    elif request_status == "resolved":
        service_requests = service_requests.filter(status=ServiceRequest.STATUS_CLOSED)
    if ownership == "mine":
        service_requests = service_requests.filter(assigned_to=request.user)
    elif ownership == "unassigned":
        service_requests = service_requests.filter(assigned_to__isnull=True)

    context = {
        "staff_client_access": staff_client_access,
        "request_status": request_status,
        "ownership": ownership,
        "service_requests": list(service_requests[:25]),
        "total_requests": service_requests.model.objects.filter(
            status__in=[ServiceRequest.STATUS_OPEN, ServiceRequest.STATUS_IN_PROGRESS],
            **({"client": staff_client_access.client} if staff_client_access else {}),
        ).count(),
        "my_requests": service_requests.model.objects.filter(
            status__in=[ServiceRequest.STATUS_OPEN, ServiceRequest.STATUS_IN_PROGRESS],
            assigned_to=request.user,
            **({"client": staff_client_access.client} if staff_client_access else {}),
        ).count(),
        "unassigned_requests": service_requests.model.objects.filter(
            status__in=[ServiceRequest.STATUS_OPEN, ServiceRequest.STATUS_IN_PROGRESS],
            assigned_to__isnull=True,
            **({"client": staff_client_access.client} if staff_client_access else {}),
        ).count(),
        "update_status_choices": ServiceRequest.STATUS_CHOICES,
    }
    return render(request, "agents/technician_service_requests.html", context)


@login_required(login_url="/client/login/")
def client_team(request):
    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    denied_message = _require_client_capability(
        access,
        "can_manage_team",
        "Only client owners and admins can manage teammate access.",
    )
    if denied_message:
        messages.error(request, denied_message)
        return redirect("client_dashboard")

    context = _build_client_team_context(request, access)
    return render(request, "agents/client_team.html", context)


@login_required(login_url="/client/login/")
def client_invite_teammate(request):
    if request.method != "POST":
        return redirect("client_team")

    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    denied_message = _require_client_capability(
        access,
        "can_manage_team",
        "Only client owners and admins can invite teammates.",
    )
    if denied_message:
        messages.error(request, denied_message)
        return redirect("client_dashboard")

    form = ClientInvitationForm(request.POST)
    if form.is_valid():
        invitation = form.save(commit=False)
        invitation.client = access.client
        invitation.invited_by = request.user
        invitation.token = secrets.token_urlsafe(24)
        invitation.save()
        accept_url = request.build_absolute_uri(
            redirect("accept_client_invitation", token=invitation.token).url
        )
        send_client_invitation_notification(invitation, accept_url)
        messages.success(request, f"Invite created for {invitation.email}. Share the signup link with your teammate.")
        return redirect("client_team")

    context = _build_client_team_context(request, access, invitation_form=form)
    return render(request, "agents/client_team.html", context)


@login_required(login_url="/client/login/")
def client_add_existing_user(request):
    if request.method != "POST":
        return redirect("client_team")

    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    denied_message = _require_client_capability(
        access,
        "can_manage_team",
        "Only client owners and admins can add teammates.",
    )
    if denied_message:
        messages.error(request, denied_message)
        return redirect("client_dashboard")

    form = ExistingUserAccessForm(
        request.POST,
        user_queryset=_available_client_user_queryset(),
        allow_owner=access.role == ClientAccess.ROLE_OWNER,
    )
    if form.is_valid():
        selected_user = form.cleaned_data["user"]
        ClientAccess.objects.create(
            user=selected_user,
            client=access.client,
            role=form.cleaned_data["role"],
            can_restart_machines=form.cleaned_data["can_restart_machines"],
        )
        messages.success(request, f"Added {selected_user.username} to {access.client.name}.")
        return redirect("client_team")

    context = _build_client_team_context(request, access, existing_user_form=form)
    return render(request, "agents/client_team.html", context)


@login_required(login_url="/client/login/")
def client_invitation_action(request, invitation_id):
    if request.method != "POST":
        return redirect("client_team")

    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    denied_message = _require_client_capability(
        access,
        "can_manage_team",
        "Only client owners and admins can manage invitations.",
    )
    if denied_message:
        messages.error(request, denied_message)
        return redirect("client_dashboard")

    invitation = get_object_or_404(ClientInvitation, id=invitation_id, client=access.client)
    action = request.POST.get("action")
    if action == "revoke" and invitation.status == ClientInvitation.STATUS_PENDING:
        invitation.status = ClientInvitation.STATUS_REVOKED
        invitation.save(update_fields=["status"])
        messages.success(request, f"Invite revoked for {invitation.email}.")

    return redirect("client_team")


@login_required(login_url="/client/login/")
def client_team_member_action(request, access_id):
    if request.method != "POST":
        return redirect("client_team")

    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    denied_message = _require_client_capability(
        access,
        "can_manage_team",
        "Only client owners and admins can manage teammates.",
    )
    if denied_message:
        messages.error(request, denied_message)
        return redirect("client_dashboard")

    target_access = get_object_or_404(ClientAccess, id=access_id, client=access.client)
    if not _can_manage_team_member(access, target_access):
        messages.error(request, "You do not have permission to manage that teammate.")
        return redirect("client_team")

    action = request.POST.get("action")
    if action == "remove":
        removed_name = target_access.user.get_full_name() or target_access.user.username
        target_access.delete()
        messages.success(request, f"Removed portal access for {removed_name}.")
        return redirect("client_team")

    form = _team_member_management_form(access, target_access, request.POST)
    if form.is_valid():
        updated_access = form.save()
        updated_name = updated_access.user.get_full_name() or updated_access.user.username
        messages.success(request, f"Updated teammate access for {updated_name}.")
        return redirect("client_team")

    team_member_forms = {
        member.id: _team_member_management_form(access, member)
        for member in _client_team_member_queryset(access.client)
    }
    team_member_forms[target_access.id] = form
    context = _build_client_team_context(request, access, team_member_forms=team_member_forms)
    return render(request, "agents/client_team.html", context)


@login_required(login_url="/client/login/")
def client_team_member_detail(request, access_id):
    access, redirect_response = _client_portal_access_or_redirect(request)
    if redirect_response:
        return redirect_response

    denied_message = _require_client_capability(
        access,
        "can_manage_team",
        "Only client owners and admins can manage teammates.",
    )
    if denied_message:
        messages.error(request, denied_message)
        return redirect("client_dashboard")

    target_access = get_object_or_404(ClientAccess, id=access_id, client=access.client)
    can_manage_target = _can_manage_team_member(access, target_access)
    if request.method == "POST":
        if not can_manage_target:
            messages.error(request, "You do not have permission to manage that teammate.")
            return redirect("client_team")

        action = request.POST.get("action")
        if action == "remove":
            removed_name = target_access.user.get_full_name() or target_access.user.username
            target_access.delete()
            messages.success(request, f"Removed portal access for {removed_name}.")
            return redirect("client_team")

        form = _team_member_management_form(access, target_access, request.POST)
        if form.is_valid():
            updated_access = form.save()
            updated_name = updated_access.user.get_full_name() or updated_access.user.username
            messages.success(request, f"Updated teammate access for {updated_name}.")
            return redirect("client_team_member_detail", access_id=updated_access.id)
    else:
        form = _team_member_management_form(access, target_access)

    context = {
        "client_access": access,
        "member_access": target_access,
        "member_form": form,
        "can_manage_target": can_manage_target,
        "notification_count": _unread_notification_count(access),
    }
    return render(request, "agents/client_team_member_detail.html", context)


def accept_client_invitation(request, token):
    invitation = get_object_or_404(ClientInvitation, token=token)
    if invitation.status != ClientInvitation.STATUS_PENDING:
        messages.info(request, "That invitation link is no longer active.")
        return redirect("client_login")

    form = ClientInvitationAcceptForm(invitation=invitation, data=request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = User.objects.create_user(
            username=form.cleaned_data["username"],
            email=invitation.email,
            password=form.cleaned_data["password1"],
            first_name=form.cleaned_data.get("first_name", ""),
            last_name=form.cleaned_data.get("last_name", ""),
        )
        ClientAccess.objects.create(
            user=user,
            client=invitation.client,
            role=invitation.role,
        )
        invitation.status = ClientInvitation.STATUS_ACCEPTED
        invitation.accepted_by = user
        invitation.accepted_at = timezone.now()
        invitation.save(update_fields=["status", "accepted_by", "accepted_at"])
        send_client_invitation_accepted_notification(invitation)
        auth_login(request, user)
        messages.success(request, f"Welcome to {invitation.client.name}. Your workspace is ready.")
        return redirect("client_dashboard")

    context = {
        "invitation": invitation,
        "form": form,
    }
    return render(request, "agents/accept_client_invitation.html", context)


def technician_alert_action(request, alert_id):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    if request.method != "POST":
        return redirect("dashboard")

    alert_filters = {"id": alert_id}
    staff_client_access = _staff_client_access(request)
    if staff_client_access:
        alert_filters["client"] = staff_client_access.client
    alert = get_object_or_404(Alert, **alert_filters)
    action = request.POST.get("action")
    now = timezone.now()
    actor_label = _alert_actor_label(request)

    if action == "acknowledge" and alert.status == Alert.STATUS_ACTIVE:
        alert.status = Alert.STATUS_ACKNOWLEDGED
        alert.acknowledged_by = actor_label
        alert.acknowledged_at = now
        alert.save(update_fields=["status", "acknowledged_by", "acknowledged_at", "updated_at"])
    elif action == "resolve" and alert.status != Alert.STATUS_RESOLVED:
        alert.status = Alert.STATUS_RESOLVED
        alert.resolved_by = actor_label
        alert.resolved_at = now
        alert.save(update_fields=["status", "resolved_by", "resolved_at", "updated_at"])

    return redirect("dashboard")


def technician_request_action(request, request_id):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    if request.method != "POST":
        return redirect("technician_service_requests")

    service_request = _staff_scoped_request_or_404(request, request_id)
    action = request.POST.get("action")
    if action == "assign_to_me":
        service_request.assigned_to = request.user
        service_request.save(update_fields=["assigned_to", "updated_at"])
        messages.success(request, f"Assigned '{service_request.subject}' to you.")
        return redirect("technician_request_detail", request_id=service_request.id)
    if action == "clear_assignee":
        service_request.assigned_to = None
        service_request.save(update_fields=["assigned_to", "updated_at"])
        messages.success(request, f"Removed the technician assignment from '{service_request.subject}'.")
        return redirect("technician_request_detail", request_id=service_request.id)

    form = TechnicianServiceRequestUpdateForm(request.POST, instance=service_request)
    if form.is_valid():
        updated_request = form.save(commit=False)
        if updated_request.status == ServiceRequest.STATUS_CLOSED:
            updated_request.closed_by = _request_actor_label(request)
            updated_request.closed_at = timezone.now()
        else:
            updated_request.closed_by = ""
            updated_request.closed_at = None
        updated_request.save()
        send_support_request_updated_notification(updated_request)
        messages.success(request, f"Updated ticket '{updated_request.subject}'.")
    else:
        messages.error(request, "We could not update that ticket. Please check the form values and try again.")

    return redirect("technician_request_detail", request_id=service_request.id)

def send_command(request, machine_id):
    access_response = _ensure_staff_dashboard_access(request)
    if access_response:
        return access_response

    if request.method != "POST":
        return redirect("dashboard")

    machine = _staff_scoped_machine_or_404(request, machine_id)
    cmd_type = request.POST.get("cmd_type")
    if cmd_type == "kill":
        proc_name = (request.POST.get("process_name") or "").strip()
        if proc_name:
            escaped_name = proc_name.replace('"', '`"')
            machine.pending_command = (
                f'Stop-Process -Name "{escaped_name}" -Force -ErrorAction SilentlyContinue'
            )
            machine.save(update_fields=["pending_command", "last_seen"])
            AuditLog.objects.create(
                machine=machine,
                action="Kill Command Queued",
                details=f"Target: {proc_name}",
            )
    return redirect("dashboard")

@csrf_exempt
def communication_hub(request):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    api_key = request.headers.get("X-API-KEY", "")
    if not compare_digest(api_key, settings.AGENT_KEY):
        return JsonResponse({"error": "Unauthorized"}, status=403)

    data, error_response = _get_request_data(request)
    if error_response:
        return error_response

    if data.get("connectivity_test"):
        return JsonResponse({"status": "ok"})

    hostname = (data.get("hostname") or "").strip()
    if not hostname:
        return JsonResponse({"error": "Hostname is required"}, status=400)

    machine, created = Machine.objects.get_or_create(hostname=hostname)
    if created:
        AuditLog.objects.create(machine=machine, action="Agent Registered", details="First check-in received")

    if data.get("client"):
        client_name = str(data["client"]).strip()
        if client_name:
            client, _ = Client.objects.get_or_create(name=client_name)
            should_assign_client = (
                machine.client_id is None
                or machine.client_id == client.id
                or (
                    machine.client is not None
                    and _is_placeholder_client_name(machine.client.name)
                    and not _is_placeholder_client_name(client.name)
                )
            )
            if should_assign_client and machine.client_id != client.id:
                machine.client = client

    output = data.get("output")
    if output:
        AuditLog.objects.create(machine=machine, action="Task Finished", details=output)
        machine.command_results = output
        machine.pending_command = NO_PENDING_COMMAND
        machine.save(update_fields=["client", "command_results", "pending_command", "last_seen"])
        if machine.client_id:
            if str(output).strip().lower().startswith("error"):
                _open_alert(
                    machine,
                    Alert.CATEGORY_COMMAND,
                    Alert.SEVERITY_WARNING,
                    f"Command failed on {machine.hostname}",
                    str(output),
                )
            else:
                _resolve_alerts(machine, Alert.CATEGORY_COMMAND, "System auto-resolve")
        return JsonResponse({"status": "Task Cleared"})

    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    remote_ip = forwarded_for.split(",")[0].strip() if forwarded_for else request.META.get("REMOTE_ADDR")

    machine.os_info = data.get("os")
    machine.cpu_model = data.get("cpu")
    machine.ram_gb = data.get("ram")
    machine.disk_usage_percent = data.get("disk_percent")
    machine.top_processes = data.get("processes")
    machine.ip_address = remote_ip
    machine.mac_address = data.get("mac")
    machine.manufacturer = data.get("brand")
    machine.model_name = data.get("model")
    machine.last_boot_time = _parse_last_boot_time(data.get("boot_time"))
    machine.remote_id = data.get("remote_id")
    machine.remote_password = data.get("remote_pass")
    machine.save()
    _sync_machine_alerts(machine)

    return JsonResponse(
        {
            "task": machine.pending_command,
            "download_url": "",
            "run_maintenance": machine.auto_maintenance,
        }
    )


def csrf_failure(request, reason="", template_name="errors/403_csrf.html"):
    return _render_error_page(
        request,
        template_name,
        403,
        "Your session needs a refresh",
        "The security token for this form is no longer valid. This usually happens after signing in on another tab, using the back button, or submitting an older page.",
        action_url=request.path or "/dashboard/",
        action_label="Reload This Page",
        extra_context={"csrf_reason": reason},
    )


def bad_request(request, exception):
    return _render_error_page(
        request,
        "errors/400.html",
        400,
        "That request could not be completed",
        "The server could not understand the request that was sent. Please go back, refresh the page, and try again.",
    )


def permission_denied(request, exception):
    return _render_error_page(
        request,
        "errors/403.html",
        403,
        "You do not have access to this page",
        "Your account does not have permission to perform that action or view this resource.",
        action_url="/client/login/" if request.path.startswith("/client/") else "/dashboard/",
        action_label="Return to Safety",
    )


def page_not_found(request, exception):
    return _render_error_page(
        request,
        "errors/404.html",
        404,
        "We could not find that page",
        "The address may be outdated, the page may have moved, or the link may be incomplete.",
    )


def server_error(request):
    return _render_error_page(
        request,
        "errors/500.html",
        500,
        "Something went wrong on our side",
        "The application hit an unexpected problem. Refresh the page in a moment, and if it keeps happening we can trace it from the server logs.",
    )
