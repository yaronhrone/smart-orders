from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0004_orderbatch_order_supplier"),
    ]

    operations = [
        migrations.AddField(
            model_name="orderrequestproduct",
            name="approved_quantity",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="orderrequest",
            name="removed_lines",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
