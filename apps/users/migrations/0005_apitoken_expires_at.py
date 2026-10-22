from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0004_apitoken"),
    ]

    operations = [
        migrations.AddField(
            model_name="apitoken",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
