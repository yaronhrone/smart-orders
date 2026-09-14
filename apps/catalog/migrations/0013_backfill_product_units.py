"""
Products created before "bundle"/"pack" existed (0010) — or by any
get_or_create that never revisits an existing row, like seed_demo — were
stuck on the default "kg". Every WhatsApp message reads the unit off the
product, so כוסברה showed as ק"ג to customers and suppliers alike.

Only rows still on the default "kg" are corrected from the catalog file; a
unit someone set deliberately to anything else is left alone.
"""
import json
from pathlib import Path

from django.db import migrations

CATALOG_PATH = Path(__file__).resolve().parents[3] / "data" / "products_catalog.json"


def backfill_units(apps, schema_editor):
    Product = apps.get_model("catalog", "Product")
    if not CATALOG_PATH.exists():
        return
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    for entry in catalog["products"]:
        if entry["unit"] == "kg":
            continue
        Product.objects.filter(name=entry["name"], unit="kg").update(unit=entry["unit"])


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0012_normalize_supplier_phone"),
    ]

    operations = [
        migrations.RunPython(backfill_units, migrations.RunPython.noop),
    ]
