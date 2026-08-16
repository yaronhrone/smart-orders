"""
Normalizes Supplier.phone to digits-only, matching what SupplierSerializer's
validate_phone already enforces for new writes. Older suppliers (entered
before that validator existed, or seeded directly) can still have dashes
(e.g. "03-5551111"), which breaks SupplierPriceUpdateView's phone lookup
(it strips the incoming phone to digits before querying).
"""
import re

from django.db import migrations


def normalize_phone(apps, schema_editor):
    Supplier = apps.get_model("catalog", "Supplier")
    for supplier in Supplier.objects.all():
        digits_only = re.sub(r"\D", "", supplier.phone)
        if digits_only != supplier.phone:
            supplier.phone = digits_only
            supplier.save(update_fields=["phone"])


def noop_reverse(apps, schema_editor):
    pass  # normalization isn't meaningfully reversible — nothing to undo


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0011_delete_marketprice"),
    ]

    operations = [
        migrations.RunPython(normalize_phone, noop_reverse),
    ]
