import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("catalog", "0015_supplier_blocked_until"),
        ("orders", "0003_delete_existing_orders"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderBatch",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="order_batches",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        # The table is empty after 0003, so no default is needed for these
        # non-null columns — preserve_default=False keeps the placeholder out
        # of the model state.
        migrations.AddField(
            model_name="orderrequest",
            name="batch",
            field=models.ForeignKey(
                default=None,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="orders",
                to="orders.orderbatch",
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="orderrequest",
            name="supplier",
            field=models.ForeignKey(
                default=None,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="orders",
                to="catalog.supplier",
            ),
            preserve_default=False,
        ),
    ]
