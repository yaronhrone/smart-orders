from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_alter_supplier_owner"),
    ]

    operations = [
        migrations.CreateModel(
            name="MarketPrice",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("price_per_unit", models.DecimalField(decimal_places=2, max_digits=10)),
                ("source", models.CharField(default="רשות חקלאית", max_length=255)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "product",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="market_price",
                        to="catalog.product",
                    ),
                ),
            ],
        ),
    ]
