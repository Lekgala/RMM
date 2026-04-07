from django.contrib import admin
from django.contrib.auth.admin import GroupAdmin as DjangoGroupAdmin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.models import Group, User
from django.db.models import Q
from django.utils.html import format_html

from .models import (
    Alert,
    AuditLog,
    Client,
    ClientAccess,
    ClientInvitation,
    Machine,
    ServiceRequest,
    SubscriptionPlan,
    ClientSubscription,
)


admin.site.site_header = "TJ RMM Admin"
admin.site.site_title = "TJ RMM Admin"
admin.site.index_title = "Operations Console"
admin.site.unregister(User)
admin.site.unregister(Group)


def _client_access_for_user(user):
    if not getattr(user, "is_authenticated", False) or getattr(user, "is_superuser", False):
        return None
    try:
        return user.client_access
    except ClientAccess.DoesNotExist:
        return None


class ClientScopedAdminMixin:
    client_lookup = None

    def _client_access(self, request):
        return _client_access_for_user(request.user)

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        access = self._client_access(request)
        if not access or not self.client_lookup:
            return queryset
        return queryset.filter(**{self.client_lookup: access.client})

    def has_module_permission(self, request):
        if request.user.is_superuser:
            return super().has_module_permission(request)
        access = self._client_access(request)
        if access and self.client_lookup:
            return True
        return super().has_module_permission(request)

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        access = self._client_access(request)
        if access and not request.user.is_superuser:
            if db_field.name == "client":
                kwargs["queryset"] = Client.objects.filter(id=access.client_id)
            elif db_field.name == "machine":
                kwargs["queryset"] = Machine.objects.filter(client=access.client)
        return super().formfield_for_foreignkey(db_field, request, **kwargs)


class ClientScopedReadOnlyConfigMixin(ClientScopedAdminMixin):
    def has_add_permission(self, request):
        if self._client_access(request):
            return False
        return super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        if self._client_access(request):
            return False
        return super().has_delete_permission(request, obj=obj)


class ClientPortalHiddenAdminMixin(ClientScopedAdminMixin):
    def has_module_permission(self, request):
        if self._client_access(request):
            return False
        return super().has_module_permission(request)

    def has_view_permission(self, request, obj=None):
        if self._client_access(request):
            return False
        return super().has_view_permission(request, obj=obj)


@admin.register(User)
class ScopedUserAdmin(ClientScopedAdminMixin, DjangoUserAdmin):
    client_lookup = "client_access__client"

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        access = self._client_access(request)
        if not access:
            return queryset
        return queryset.filter(client_access__client=access.client)

    def has_module_permission(self, request):
        if _client_access_for_user(request.user):
            return False
        return super().has_module_permission(request)


@admin.register(Group)
class ScopedGroupAdmin(DjangoGroupAdmin):
    def has_module_permission(self, request):
        if _client_access_for_user(request.user):
            return False
        return super().has_module_permission(request)


@admin.register(AuditLog)
class AuditLogAdmin(ClientScopedAdminMixin, admin.ModelAdmin):
    client_lookup = "machine__client"
    list_display = ("timestamp", "machine", "action", "short_details")
    list_filter = ("action", "machine__client")
    search_fields = ("machine__hostname", "machine__client__name", "action", "details")
    readonly_fields = ("timestamp", "machine", "action", "details")
    list_select_related = ("machine", "machine__client")
    date_hierarchy = "timestamp"

    def short_details(self, obj):
        if not obj.details:
            return "-"
        return obj.details[:80] + ("..." if len(obj.details) > 80 else "")

    short_details.short_description = "Details"


@admin.register(Alert)
class AlertAdmin(ClientScopedAdminMixin, admin.ModelAdmin):
    client_lookup = "client"
    list_display = ("title", "client", "machine", "severity", "status", "created_at")
    list_filter = ("severity", "status", "category", "client")
    search_fields = ("title", "message", "machine__hostname", "client__name")
    list_select_related = ("client", "machine")
    readonly_fields = (
        "client",
        "machine",
        "category",
        "severity",
        "title",
        "message",
        "acknowledged_by",
        "acknowledged_at",
        "resolved_by",
        "resolved_at",
        "created_at",
        "updated_at",
    )


@admin.register(ServiceRequest)
class ServiceRequestAdmin(ClientScopedAdminMixin, admin.ModelAdmin):
    client_lookup = "client"
    list_display = ("subject", "client", "machine", "requester", "assigned_to", "priority", "status", "updated_at")
    list_filter = ("priority", "status", "client", "assigned_to")
    search_fields = ("subject", "description", "client__name", "machine__hostname", "requester__username", "assigned_to__username")
    list_select_related = ("client", "machine", "requester", "assigned_to")
    readonly_fields = ("client", "machine", "requester", "created_at", "updated_at", "closed_at", "closed_by")

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        access = self._client_access(request)
        if not access:
            return queryset
        return queryset.filter(Q(requester=request.user) | Q(assigned_to=request.user))


@admin.register(Client)
class ClientAdmin(ClientPortalHiddenAdminMixin, ClientScopedReadOnlyConfigMixin, admin.ModelAdmin):
    client_lookup = "id"
    list_display = ("name", "contact_email", "machine_count", "created_at")
    search_fields = ("name", "contact_email")
    ordering = ("name",)

    def machine_count(self, obj):
        return obj.machines.count()

    machine_count.short_description = "Machines"


@admin.register(ClientAccess)
class ClientAccessAdmin(ClientPortalHiddenAdminMixin, ClientScopedReadOnlyConfigMixin, admin.ModelAdmin):
    client_lookup = "client"
    list_display = ("user", "client", "role", "can_restart_machines", "created_at")
    list_filter = ("client", "role", "can_restart_machines")
    search_fields = ("user__username", "user__email", "client__name")
    autocomplete_fields = ("user", "client")


@admin.register(ClientSubscription)
class ClientSubscriptionAdmin(ClientScopedAdminMixin, admin.ModelAdmin):
    client_lookup = "client"
    list_display = (
        "client",
        "plan",
        "status",
        "start_date",
        "current_period_end",
        "billing_email",
    )
    list_filter = ("status", "plan", "client")
    search_fields = ("client__name", "plan__name", "billing_email", "stripe_customer_id")
    autocomplete_fields = ("client", "plan")
    readonly_fields = ("created_at", "updated_at")


@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "price_display", "max_machines", "max_user_seats", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "description")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(ClientInvitation)
class ClientInvitationAdmin(ClientPortalHiddenAdminMixin, ClientScopedReadOnlyConfigMixin, admin.ModelAdmin):
    client_lookup = "client"
    list_display = ("email", "client", "role", "status", "invited_by", "created_at")
    list_filter = ("client", "role", "status")
    search_fields = ("email", "client__name", "invited_by__username")
    readonly_fields = ("client", "email", "role", "invited_by", "token", "status", "accepted_by", "created_at", "accepted_at")


@admin.register(Machine)
class MachineAdmin(ClientScopedAdminMixin, admin.ModelAdmin):
    client_lookup = "client"
    list_display = (
        "hostname",
        "client",
        "display_status",
        "ip_address",
        "disk_health",
        "remote_control",
        "auto_maintenance",
        "last_seen",
    )
    list_filter = ("client", "auto_maintenance", "manufacturer", "os_info")
    search_fields = (
        "hostname",
        "client__name",
        "ip_address",
        "manufacturer",
        "model_name",
        "os_info",
        "remote_id",
    )
    list_select_related = ("client",)
    ordering = ("-last_seen", "hostname")
    fieldsets = (
        (
            "Assignment",
            {
                "fields": ("client", "hostname", "last_seen", "last_boot_time"),
            },
        ),
        (
            "System Info",
            {
                "fields": (
                    "ip_address",
                    "mac_address",
                    "manufacturer",
                    "model_name",
                    "os_info",
                    "cpu_model",
                    "ram_gb",
                    "disk_usage_percent",
                )
            },
        ),
        (
            "Remote Access",
            {
                "fields": ("remote_id", "remote_password"),
            },
        ),
        (
            "Live Monitoring",
            {
                "fields": ("top_processes",),
            },
        ),
        (
            "Management",
            {
                "fields": ("auto_maintenance", "pending_command", "command_results", "queued_file"),
            },
        ),
    )
    readonly_fields = (
        "hostname",
        "last_seen",
        "last_boot_time",
        "ip_address",
        "mac_address",
        "manufacturer",
        "model_name",
        "os_info",
        "cpu_model",
        "ram_gb",
        "disk_usage_percent",
        "top_processes",
        "remote_id",
        "remote_password",
    )
    actions = ["trigger_cleanup", "remote_reboot", "launch_quick_assist"]

    def display_status(self, obj):
        if not obj.is_online():
            return format_html('<span class="admin-status status-offline">{}</span>', "Offline")
        if obj.disk_usage_percent and obj.disk_usage_percent > 90:
            return format_html(
                '<span class="admin-status status-warning">Disk Warning ({}%)</span>',
                obj.disk_usage_percent,
            )
        return format_html('<span class="admin-status status-online">{}</span>', "Online")

    def disk_health(self, obj):
        if obj.disk_usage_percent is None:
            return "-"
        tone = "status-warning" if obj.disk_usage_percent > 90 else "status-online"
        return format_html(
            '<span class="admin-status {}">{}%</span>',
            tone,
            obj.disk_usage_percent,
        )

    def remote_control(self, obj):
        if obj.remote_id:
            return format_html(
                '<a class="admin-connect-link" href="rustdesk://{}">Connect Live</a>',
                obj.remote_id,
            )
        return format_html('<span class="admin-waiting">{}</span>', "Waiting for Agent")

    display_status.short_description = "Status"
    disk_health.short_description = "Disk"
    remote_control.short_description = "Remote Access"

    @admin.action(description="Run: Temp File Cleanup")
    def trigger_cleanup(self, request, queryset):
        queryset.update(
            pending_command='Remove-Item "$env:TEMP\\*" -Recurse -Force -ErrorAction SilentlyContinue'
        )

    @admin.action(description="Emergency: Remote Reboot")
    def remote_reboot(self, request, queryset):
        queryset.update(pending_command="Restart-Computer -Force")

    @admin.action(description="Support: Launch Quick Assist")
    def launch_quick_assist(self, request, queryset):
        queryset.update(pending_command="start quickassist")


_original_admin_each_context = admin.site.each_context


def _tenant_filtered_each_context(self, request):
    context = _original_admin_each_context(request)
    if not _client_access_for_user(request.user):
        return context

    allowed_models = {"machine", "servicerequest", "alert", "auditlog"}
    filtered_apps = []
    for app in context.get("available_apps", []):
        if app.get("app_label") != "agents":
            continue
        app_copy = app.copy()
        app_copy["models"] = [
            model for model in app.get("models", [])
            if model.get("object_name", "").lower() in allowed_models
        ]
        if app_copy["models"]:
            filtered_apps.append(app_copy)
    context["available_apps"] = filtered_apps
    return context


admin.site.each_context = _tenant_filtered_each_context.__get__(admin.site, type(admin.site))
