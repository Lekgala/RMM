from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0014_alert"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ServiceRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("subject", models.CharField(max_length=150)),
                ("description", models.TextField()),
                ("priority", models.CharField(choices=[("low", "Low"), ("normal", "Normal"), ("high", "High"), ("urgent", "Urgent")], default="normal", max_length=20)),
                ("status", models.CharField(choices=[("open", "Open"), ("in_progress", "In Progress"), ("closed", "Closed")], default="open", max_length=20)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("closed_at", models.DateTimeField(blank=True, null=True)),
                ("closed_by", models.CharField(blank=True, max_length=150)),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="service_requests", to="agents.client")),
                ("machine", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="service_requests", to="agents.machine")),
                ("requester", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="service_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "ordering": ["status", "-updated_at", "-created_at"],
            },
        ),
    ]
