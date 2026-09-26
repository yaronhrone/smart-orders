from django.db import migrations


def delete_all_orders(apps, schema_editor):
    # Orders #1-#41 were test data under the old one-order-many-suppliers
    # model. Decided 2026-09-26 to start clean rather than split them into
    # per-supplier orders (see 0004). Cascades to items and confirmations.
    OrderRequest = apps.get_model("orders", "OrderRequest")
    OrderRequest.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("orders", "0002_alter_orderrequest_status"),
    ]

    operations = [
        migrations.RunPython(delete_all_orders, migrations.RunPython.noop),
    ]
