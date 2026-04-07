from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("agents", "0015_servicerequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="servicerequest",
            name="resolution_summary",
            field=models.TextField(blank=True),
        ),
    ]
