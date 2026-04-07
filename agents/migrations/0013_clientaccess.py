from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0012_alter_auditlog_options"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ClientAccess",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("can_restart_machines", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("client", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="access_users", to="agents.client")),
                ("user", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="client_access", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "verbose_name": "Client Access",
                "verbose_name_plural": "Client Access",
                "ordering": ["client__name", "user__username"],
            },
        ),
    ]
