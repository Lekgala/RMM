from django.conf import settings
from django.contrib import admin
from django.conf.urls.static import static
from django.urls import path

from agents.views import (
    ClientLoginView,
    accept_client_invitation,
    bad_request,
    client_dashboard,
    client_alert_action,
    client_add_existing_user,
    client_invitation_action,
    client_invite_teammate,
    client_logout,
    client_machine_action,
    client_notification_action,
    client_notification_mark_all_read,
    client_notifications,
    client_request_updates,
    client_request_action,
    client_service_requests,
    client_settings,
    client_billing,
    client_submit_request,
    client_team_member_detail,
    client_team_member_action,
    client_team,
    communication_hub,
    dashboard,
    landing_page,
    page_not_found,
    permission_denied,
    send_command,
    server_error,
    stripe_webhook,
    technician_alert_action,
    technician_alerts,
    technician_machine_action,
    technician_machine_detail,
    technician_machines,
    technician_request_detail,
    technician_request_note_action,
    technician_request_public_update_action,
    technician_service_requests,
    technician_request_action,
)

urlpatterns = [
    path('', landing_page, name='landing_page'),
    path('admin/', admin.site.urls),
    path('dashboard/', dashboard, name='dashboard'),
    path('operations/machines/', technician_machines, name='technician_machines'),
    path('operations/machines/<int:machine_id>/', technician_machine_detail, name='technician_machine_detail'),
    path('operations/machines/<int:machine_id>/action/', technician_machine_action, name='technician_machine_action'),
    path('operations/alerts/', technician_alerts, name='technician_alerts'),
    path('support/requests/', technician_service_requests, name='technician_service_requests'),
    path('support/requests/<int:request_id>/', technician_request_detail, name='technician_request_detail'),
    path('support/requests/<int:request_id>/notes/', technician_request_note_action, name='technician_request_note_action'),
    path('support/requests/<int:request_id>/public-updates/', technician_request_public_update_action, name='technician_request_public_update_action'),
    path('command/<int:machine_id>/', send_command, name='send_command'), # NEW
    path('client/login/', ClientLoginView.as_view(), name='client_login'),
    path('client/logout/', client_logout, name='client_logout'),
    path('client/', client_dashboard, name='client_dashboard'),
    path('client/notifications/', client_notifications, name='client_notifications'),
    path('client/requests/', client_request_updates, name='client_request_updates'),
    path('client/settings/', client_settings, name='client_settings'),
    path('client/billing/', client_billing, name='client_billing'),
    path('client/support/', client_service_requests, name='client_service_requests'),
    path('client/team/', client_team, name='client_team'),
    path('client/team/<int:access_id>/', client_team_member_detail, name='client_team_member_detail'),
    path('client/machine/<int:machine_id>/action/', client_machine_action, name='client_machine_action'),
    path('client/requests/new/', client_submit_request, name='client_submit_request'),
    path('client/requests/<int:request_id>/action/', client_request_action, name='client_request_action'),
    path('client/team/add-existing-user/', client_add_existing_user, name='client_add_existing_user'),
    path('client/invitations/new/', client_invite_teammate, name='client_invite_teammate'),
    path('client/invitations/<int:invitation_id>/action/', client_invitation_action, name='client_invitation_action'),
    path('client/team/<int:access_id>/action/', client_team_member_action, name='client_team_member_action'),
    path('client/notifications/<int:notification_id>/action/', client_notification_action, name='client_notification_action'),
    path('client/notifications/mark-all-read/', client_notification_mark_all_read, name='client_notification_mark_all_read'),
    path('client/alerts/<int:alert_id>/action/', client_alert_action, name='client_alert_action'),
    path('client/invite/<str:token>/', accept_client_invitation, name='accept_client_invitation'),
    path('stripe/webhook/', stripe_webhook, name='stripe_webhook'),
    path('alerts/<int:alert_id>/action/', technician_alert_action, name='technician_alert_action'),
    path('requests/<int:request_id>/action/', technician_request_action, name='technician_request_action'),
    path('api/hub/', communication_hub),
]

handler400 = bad_request
handler403 = permission_denied
handler404 = page_not_found
handler500 = server_error

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
