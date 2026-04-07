from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand
from django.utils import timezone

from agents.models import Machine


class Command(BaseCommand):
    help = "Scans for offline machines and sends a summary email alert"

    def handle(self, *args, **kwargs):
        threshold = timezone.now() - timedelta(minutes=30)
        offline_machines = Machine.objects.filter(last_seen__lt=threshold).select_related("client")

        if not offline_machines.exists():
            self.stdout.write(self.style.SUCCESS("All TJ RMM Agents are online. No alerts sent."))
            return

        report_text = "--- TJ RMM HEALTH ALERT ---\n\n"
        report_text += (
            f"The following machines have stopped checking in as of "
            f"{timezone.now().strftime('%Y-%m-%d %H:%M')}:\n\n"
        )

        for machine in offline_machines:
            client_name = machine.client.name if machine.client else "Unassigned"
            last_time = machine.last_seen.strftime("%H:%M on %d %b")
            report_text += (
                f"- {machine.hostname}\n"
                f"  Client: {client_name}\n"
                f"  Last Seen: {last_time}\n\n"
            )

        report_text += "Please check the TJ RMM Dashboard for more details."

        recipients = settings.SUPPORT_NOTIFICATION_EMAILS or [settings.DEFAULT_FROM_EMAIL]
        send_mail(
            subject="TJ RMM: Daily Health Alert",
            message=report_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Health report generated for {offline_machines.count()} offline machines."
            )
        )
