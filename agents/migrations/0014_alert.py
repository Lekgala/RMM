from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0013_clientaccess"),
    ]

    operations = [
        migrations.CreateModel(
            name="Alert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("category", models.CharField(choices=[("disk_space", "Disk Space"), ("offline", "Offline"), ("command_failure", "Command Failure")], max_length=50)),
                ("severity", models.CharField(choices=[("info", "Info"), ("warning", "Warning"), ("critical", "Critical")], default="warning", max_length=20)),
                ("status", models.CharField(choices=[("active", "Active"), ("acknowledged", "Acknowledged"), ("resolved", "Resolved")], default="active", max_length=20)),
                ("title", models.CharField(max_length=255)),
                ("message", models.TextField()),
                ("acknowledged_by", models.CharField(blank=True, max_length=150)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("resolved_by", models.CharField(blank=True, max_length=150)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="alerts", to="agents.client")),
                ("machine", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="alerts", to="agents.machine")),
            ],
            options={
                "ordering": ["status", "-created_at"],
            },
        ),
    ]
