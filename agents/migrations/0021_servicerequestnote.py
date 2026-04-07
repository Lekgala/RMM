from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0020_servicerequest_assigned_to"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="ServiceRequestNote",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("body", models.TextField()),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "author",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="service_request_notes", to=settings.AUTH_USER_MODEL),
                ),
                (
                    "service_request",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="notes", to="agents.servicerequest"),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
