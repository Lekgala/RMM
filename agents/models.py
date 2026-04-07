from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from datetime import timedelta

class Client(models.Model):
    name = models.CharField(max_length=100, unique=True)
    contact_email = models.EmailField(null=True, blank=True)
    logo = models.ImageField(upload_to='client_logos/', null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

    @property
    def current_subscription(self):
        return self.subscriptions.order_by("-start_date").first()

    def has_active_subscription(self):
        subscription = self.current_subscription
        return bool(subscription and subscription.is_active())


class SubscriptionPlan(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    monthly_price_cents = models.PositiveIntegerField(default=0)
    max_machines = models.PositiveIntegerField(null=True, blank=True)
    max_user_seats = models.PositiveIntegerField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["monthly_price_cents", "name"]

    def __str__(self):
        return self.name

    def price_display(self):
        return f"${self.monthly_price_cents / 100:.2f} / month"


class ClientSubscription(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_TRIALING = "trialing"
    STATUS_PAST_DUE = "past_due"
    STATUS_UNPAID = "unpaid"
    STATUS_CANCELED = "canceled"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_TRIALING, "Trial"),
        (STATUS_PAST_DUE, "Past Due"),
        (STATUS_UNPAID, "Unpaid"),
        (STATUS_CANCELED, "Canceled"),
        (STATUS_EXPIRED, "Expired"),
    ]

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="subscriptions")
    plan = models.ForeignKey(SubscriptionPlan, on_delete=models.PROTECT, related_name="subscriptions")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_TRIALING)
    start_date = models.DateField(auto_now_add=True)
    trial_end = models.DateField(null=True, blank=True)
    current_period_end = models.DateField(null=True, blank=True)
    stripe_subscription_id = models.CharField(max_length=255, blank=True)
    stripe_customer_id = models.CharField(max_length=255, blank=True)
    billing_email = models.EmailField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]

    def __str__(self):
        return f"{self.client.name} - {self.plan.name} ({self.get_status_display()})"

    def is_active(self):
        if self.status not in {self.STATUS_ACTIVE, self.STATUS_TRIALING}:
            return False
        if self.current_period_end and self.current_period_end < timezone.now().date():
            return False
        if self.status == self.STATUS_TRIALING and self.trial_end and self.trial_end < timezone.now().date():
            return False
        return True

    def status_badge(self):
        return self.get_status_display()


class ClientAccess(models.Model):
    ROLE_OWNER = "owner"
    ROLE_ADMIN = "admin"
    ROLE_MEMBER = "member"
    ROLE_VIEWER = "viewer"
    ROLE_CHOICES = [
        (ROLE_OWNER, "Owner"),
        (ROLE_ADMIN, "Admin"),
        (ROLE_MEMBER, "Member"),
        (ROLE_VIEWER, "Viewer"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='client_access')
    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name='access_users')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_ADMIN)
    can_restart_machines = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['client__name', 'user__username']
        verbose_name = 'Client Access'
        verbose_name_plural = 'Client Access'

    def __str__(self):
        return f"{self.user.username} -> {self.client.name}"

    def can_manage_devices(self):
        return self.role in {self.ROLE_OWNER, self.ROLE_ADMIN, self.ROLE_MEMBER}

    def can_submit_tickets(self):
        return self.role in {self.ROLE_OWNER, self.ROLE_ADMIN, self.ROLE_MEMBER}

    def can_view_only(self):
        return self.role == self.ROLE_VIEWER

    def can_restart(self):
        return self.role in {self.ROLE_OWNER, self.ROLE_ADMIN} and self.can_restart_machines

    def can_manage_team(self):
        return self.role in {self.ROLE_OWNER, self.ROLE_ADMIN}


class ClientInvitation(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_REVOKED = "revoked"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REVOKED, "Revoked"),
    ]

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="invitations")
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=ClientAccess.ROLE_CHOICES, default=ClientAccess.ROLE_MEMBER)
    invited_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sent_client_invitations")
    token = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    accepted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_client_invitations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["status", "-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["client", "email"],
                condition=models.Q(status="pending"),
                name="unique_pending_invitation_per_client_email",
            )
        ]

    def __str__(self):
        return f"{self.client.name}: {self.email}"


class ClientNotification(models.Model):
    CATEGORY_INFO = "info"
    CATEGORY_TICKET = "ticket"
    CATEGORY_TEAM = "team"
    CATEGORY_CHOICES = [
        (CATEGORY_INFO, "Info"),
        (CATEGORY_TICKET, "Ticket"),
        (CATEGORY_TEAM, "Team"),
    ]

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="notifications")
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="client_notifications",
    )
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, default=CATEGORY_INFO)
    title = models.CharField(max_length=255)
    message = models.TextField()
    link = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["is_read", "-created_at"]

    def __str__(self):
        return f"{self.client.name}: {self.title}"


class Machine(models.Model):
    client = models.ForeignKey(Client, on_delete=models.SET_NULL, null=True, blank=True, related_name='machines')
    hostname = models.CharField(max_length=100, unique=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    mac_address = models.CharField(max_length=50, null=True, blank=True)
    manufacturer = models.CharField(max_length=100, null=True, blank=True)
    model_name = models.CharField(max_length=100, null=True, blank=True)
    os_info = models.CharField(max_length=255, null=True, blank=True)
    last_boot_time = models.DateTimeField(null=True, blank=True)
    last_seen = models.DateTimeField(auto_now=True)
    
    # Monitoring
    cpu_model = models.CharField(max_length=255, null=True, blank=True)
    ram_gb = models.IntegerField(null=True, blank=True)
    disk_usage_percent = models.IntegerField(null=True, blank=True)
    top_processes = models.TextField(null=True, blank=True)
    
    # Remote Access
    remote_id = models.CharField(max_length=50, null=True, blank=True)
    remote_password = models.CharField(max_length=50, null=True, blank=True)
    
    # Management
    auto_maintenance = models.BooleanField(default=False)
    pending_command = models.TextField(default="None")
    command_results = models.TextField(null=True, blank=True)
    queued_file = models.FileField(upload_to='deployments/', null=True, blank=True)

    def is_online(self):
        return timezone.now() - self.last_seen < timedelta(seconds=45) if self.last_seen else False

    def __str__(self): return f"{self.hostname} ({self.client})"


class Alert(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_ACKNOWLEDGED = "acknowledged"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_ACKNOWLEDGED, "Acknowledged"),
        (STATUS_RESOLVED, "Resolved"),
    ]

    SEVERITY_INFO = "info"
    SEVERITY_WARNING = "warning"
    SEVERITY_CRITICAL = "critical"
    SEVERITY_CHOICES = [
        (SEVERITY_INFO, "Info"),
        (SEVERITY_WARNING, "Warning"),
        (SEVERITY_CRITICAL, "Critical"),
    ]

    CATEGORY_DISK = "disk_space"
    CATEGORY_OFFLINE = "offline"
    CATEGORY_COMMAND = "command_failure"
    CATEGORY_CHOICES = [
        (CATEGORY_DISK, "Disk Space"),
        (CATEGORY_OFFLINE, "Offline"),
        (CATEGORY_COMMAND, "Command Failure"),
    ]

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="alerts")
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name="alerts")
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    severity = models.CharField(max_length=20, choices=SEVERITY_CHOICES, default=SEVERITY_WARNING)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    title = models.CharField(max_length=255)
    message = models.TextField()
    acknowledged_by = models.CharField(max_length=150, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.CharField(max_length=150, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["status", "-created_at"]

    def __str__(self):
        return f"{self.machine.hostname}: {self.title}"


class ServiceRequest(models.Model):
    PRIORITY_LOW = "low"
    PRIORITY_NORMAL = "normal"
    PRIORITY_HIGH = "high"
    PRIORITY_URGENT = "urgent"
    PRIORITY_CHOICES = [
        (PRIORITY_LOW, "Low"),
        (PRIORITY_NORMAL, "Normal"),
        (PRIORITY_HIGH, "High"),
        (PRIORITY_URGENT, "Urgent"),
    ]

    STATUS_OPEN = "open"
    STATUS_IN_PROGRESS = "in_progress"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_IN_PROGRESS, "In Progress"),
        (STATUS_CLOSED, "Closed"),
    ]

    client = models.ForeignKey(Client, on_delete=models.CASCADE, related_name="service_requests")
    machine = models.ForeignKey(
        Machine,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="service_requests",
    )
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name="service_requests")
    assigned_to = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_service_requests",
    )
    subject = models.CharField(max_length=150)
    description = models.TextField()
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default=PRIORITY_NORMAL)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    closed_by = models.CharField(max_length=150, blank=True)
    resolution_summary = models.TextField(blank=True)

    class Meta:
        ordering = ["status", "-updated_at", "-created_at"]

    def __str__(self):
        return f"{self.client.name}: {self.subject}"


class ServiceRequestNote(models.Model):
    service_request = models.ForeignKey(ServiceRequest, on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="service_request_notes")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Note on {self.service_request_id} by {self.author.username}"


class ServiceRequestPublicUpdate(models.Model):
    service_request = models.ForeignKey(ServiceRequest, on_delete=models.CASCADE, related_name="public_updates")
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="service_request_public_updates")
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Public update on {self.service_request_id} by {self.author.username}"


class AuditLog(models.Model):
    machine = models.ForeignKey(Machine, on_delete=models.CASCADE, related_name='logs')
    action = models.CharField(max_length=255)
    details = models.TextField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-timestamp']
