from django.conf import settings
from django.core.mail import send_mail

from .models import ClientNotification


def _unique_emails(*email_groups):
    emails = []
    seen = set()
    for group in email_groups:
        for email in group:
            cleaned = (email or "").strip().lower()
            if cleaned and cleaned not in seen:
                seen.add(cleaned)
                emails.append(cleaned)
    return emails


def _send_notification(subject, message, recipients):
    recipient_list = _unique_emails(recipients)
    if not recipient_list:
        return 0
    return send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipient_list,
        fail_silently=True,
    )


def create_client_notification(client, title, message, category=ClientNotification.CATEGORY_INFO, user=None, link=""):
    return ClientNotification.objects.create(
        client=client,
        user=user,
        category=category,
        title=title,
        message=message,
        link=link,
    )


def send_support_request_created_notification(service_request):
    create_client_notification(
        client=service_request.client,
        user=service_request.requester,
        category=ClientNotification.CATEGORY_TICKET,
        title=f"Support request submitted: {service_request.subject}",
        message=f"Your request has been logged with {service_request.get_priority_display()} priority.",
        link="/client/requests/",
    )
    recipients = _unique_emails(
        settings.SUPPORT_NOTIFICATION_EMAILS,
        [service_request.client.contact_email],
    )
    if not recipients:
        return 0

    machine_name = service_request.machine.hostname if service_request.machine else "General request"
    message = (
        f"A new support request was submitted for {service_request.client.name}.\n\n"
        f"Subject: {service_request.subject}\n"
        f"Priority: {service_request.get_priority_display()}\n"
        f"Requester: {service_request.requester.username}\n"
        f"Machine: {machine_name}\n\n"
        f"Description:\n{service_request.description}\n"
    )
    return _send_notification(
        subject=f"TJ RMM: New support request from {service_request.client.name}",
        message=message,
        recipients=recipients,
    )


def send_support_request_updated_notification(service_request):
    create_client_notification(
        client=service_request.client,
        user=service_request.requester,
        category=ClientNotification.CATEGORY_TICKET,
        title=f"Ticket updated: {service_request.subject}",
        message=f"Status is now {service_request.get_status_display()}.",
        link="/client/requests/",
    )
    recipients = _unique_emails(
        [service_request.requester.email],
        [service_request.client.contact_email],
    )
    if not recipients:
        return 0

    resolution = service_request.resolution_summary.strip() or "No resolution note has been added yet."
    message = (
        f"Your support request for {service_request.client.name} was updated.\n\n"
        f"Subject: {service_request.subject}\n"
        f"Status: {service_request.get_status_display()}\n"
        f"Priority: {service_request.get_priority_display()}\n\n"
        f"Resolution / Notes:\n{resolution}\n"
    )
    return _send_notification(
        subject=f"TJ RMM: Ticket update for {service_request.subject}",
        message=message,
        recipients=recipients,
    )


def send_support_request_public_update_notification(service_request, public_update):
    create_client_notification(
        client=service_request.client,
        category=ClientNotification.CATEGORY_TICKET,
        title=f"Progress update: {service_request.subject}",
        message=public_update.body[:160],
        link="/client/requests/",
    )
    recipients = _unique_emails(
        [service_request.requester.email],
        [service_request.client.contact_email],
    )
    if not recipients:
        return 0

    message = (
        f"A new progress update was posted for {service_request.client.name}.\n\n"
        f"Subject: {service_request.subject}\n"
        f"Status: {service_request.get_status_display()}\n\n"
        f"Update:\n{public_update.body}\n"
    )
    return _send_notification(
        subject=f"TJ RMM: Progress update for {service_request.subject}",
        message=message,
        recipients=recipients,
    )


def send_client_invitation_notification(invitation, accept_url):
    create_client_notification(
        client=invitation.client,
        category=ClientNotification.CATEGORY_TEAM,
        title=f"Invitation created for {invitation.email}",
        message=f"{invitation.invited_by.username} invited a new {invitation.get_role_display().lower()} to the workspace.",
        link="/client/team/",
    )
    recipients = _unique_emails([invitation.email])
    if not recipients:
        return 0

    message = (
        f"You have been invited to join the {invitation.client.name} workspace in TJ RMM.\n\n"
        f"Role: {invitation.get_role_display()}\n"
        f"Invited by: {invitation.invited_by.username}\n\n"
        f"Create your account here:\n{accept_url}\n"
    )
    return _send_notification(
        subject=f"TJ RMM: Invitation to join {invitation.client.name}",
        message=message,
        recipients=recipients,
    )


def send_client_invitation_accepted_notification(invitation):
    if not invitation.accepted_by:
        return None
    return create_client_notification(
        client=invitation.client,
        category=ClientNotification.CATEGORY_TEAM,
        title=f"{invitation.accepted_by.username} joined the workspace",
        message=f"The invitation for {invitation.email} was accepted.",
        link="/client/team/",
    )
