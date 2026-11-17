from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0007_signuprequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="must_change_password",
            field=models.BooleanField(default=False),
        ),
    ]
