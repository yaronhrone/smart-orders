from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0005_apitoken_expires_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="apitoken",
            name="via_oauth",
            field=models.BooleanField(default=False),
        ),
    ]
