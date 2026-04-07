from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0016_servicerequest_resolution_summary"),
    ]

    operations = [
        migrations.AddField(
            model_name="clientaccess",
            name="role",
            field=models.CharField(
                choices=[
                    ("owner", "Owner"),
                    ("admin", "Admin"),
                    ("member", "Member"),
                    ("viewer", "Viewer"),
                ],
                default="admin",
                max_length=20,
            ),
        ),
    ]
