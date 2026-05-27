from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0003_add_min_value_validators"),
    ]

    operations = [
        migrations.AddField(
            model_name="shoppinglist",
            name="is_primary",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="SupplierConfirmation",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "confirmed_quantity",
                    models.DecimalField(decimal_places=2, max_digits=10),
                ),
                ("confirmed_at", models.DateTimeField(auto_now_add=True)),
                ("notes", models.TextField(blank=True)),
                (
                    "order_request_product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="confirmations",
                        to="orders.orderrequestproduct",
                    ),
                ),
            ],
        ),
    ]
