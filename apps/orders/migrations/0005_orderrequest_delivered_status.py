from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0004_shoppinglist_is_primary_supplierconfirmation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="orderrequest",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "ממתין לאישור"),
                    ("approved", "אושר"),
                    ("sent", "נשלח לספקים"),
                    ("delivered", "נמסר"),
                    ("cancelled", "בוטל"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
