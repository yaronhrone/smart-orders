from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("users", "0006_apitoken_via_oauth"),
    ]

    operations = [
        migrations.CreateModel(
            name="SignupRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("email", models.EmailField(max_length=254)),
                ("first_name", models.CharField(max_length=100)),
                ("last_name", models.CharField(max_length=100)),
                ("phone", models.CharField(max_length=20)),
                ("company_name", models.CharField(max_length=150)),
                ("company_address", models.CharField(blank=True, max_length=250)),
                ("region", models.CharField(choices=[("tel_aviv", "תל אביב"), ("jerusalem", "ירושלים"), ("haifa", "חיפה"), ("south", "דרום"), ("north", "צפון"), ("center", "מרכז")], max_length=50)),
                ("suppliers", models.JSONField(blank=True, default=list)),
                ("status", models.CharField(choices=[("pending", "ממתין"), ("approved", "אושר"), ("rejected", "נדחה")], default="pending", max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("reviewed_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
