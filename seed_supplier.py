from django.contrib.auth import get_user_model
from apps.catalog.models import Supplier, Product, SupplierProduct

User = get_user_model()

# Get superuser as owner
owner = User.objects.filter(is_superuser=True).first()
if not owner:
    owner = User.objects.first()

print(f"Using owner: {owner}")

# Create supplier (like a WhatsApp contact)
supplier, created = Supplier.objects.get_or_create(
    phone="0521234567",
    defaults={
        "name": "אבי ירקות - שוק הכרמל",
        "whatsapp_number": "+972521234567",
        "region": "center",
        "minimum_order": 500,
        "owner": None,  # global supplier
    }
)
print(f"Supplier {'created' if created else 'exists'}: {supplier.name}")

# Products as if sent via WhatsApp message
prices = [
    {"product_name": "עגבניה",           "price_per_unit": "4.90",  "unit": "KG"},
    {"product_name": "עגבניות שרי",      "price_per_unit": "11.50", "unit": "KG"},
    {"product_name": "מלפפון",           "price_per_unit": "3.80",  "unit": "KG"},
    {"product_name": "מלפפון שרי",       "price_per_unit": "8.50",  "unit": "KG"},
    {"product_name": "פלפל אדום",        "price_per_unit": "9.20",  "unit": "KG"},
    {"product_name": "פלפל ירוק",        "price_per_unit": "5.50",  "unit": "KG"},
    {"product_name": "פלפל צהוב",        "price_per_unit": "10.00", "unit": "KG"},
    {"product_name": "בצל",              "price_per_unit": "3.20",  "unit": "KG"},
    {"product_name": "שום",              "price_per_unit": "17.00", "unit": "KG"},
    {"product_name": "גזר",              "price_per_unit": "3.50",  "unit": "KG"},
    {"product_name": "תפוח אדמה",        "price_per_unit": "4.20",  "unit": "KG"},
    {"product_name": "בטטה",             "price_per_unit": "7.50",  "unit": "KG"},
    {"product_name": "חציל",             "price_per_unit": "6.80",  "unit": "KG"},
    {"product_name": "קישוא",            "price_per_unit": "5.90",  "unit": "KG"},
    {"product_name": "ברוקולי",          "price_per_unit": "11.00", "unit": "KG"},
    {"product_name": "כרובית",           "price_per_unit": "8.80",  "unit": "KG"},
    {"product_name": "כרוב לבן",         "price_per_unit": "4.50",  "unit": "KG"},
    {"product_name": "תרד",              "price_per_unit": "13.50", "unit": "KG"},
    {"product_name": "חסה",              "price_per_unit": "4.00",  "unit": "UNIT"},
    {"product_name": "עלי רוקט",         "price_per_unit": "21.00", "unit": "KG"},
    {"product_name": "פטרוזיליה",        "price_per_unit": "14.00", "unit": "KG"},
    {"product_name": "כוסברה",           "price_per_unit": "15.00", "unit": "KG"},
    {"product_name": "נענע",             "price_per_unit": "17.00", "unit": "KG"},
    {"product_name": "לימון",            "price_per_unit": "7.00",  "unit": "KG"},
    {"product_name": "אבוקדו",           "price_per_unit": "16.00", "unit": "KG"},
]

updated = []
for item in prices:
    product_name = item["product_name"].strip()
    unit = item["unit"]

    product, _ = Product.objects.get_or_create(
        name=product_name,
        defaults={"unit": unit}
    )

    sp, created_sp = SupplierProduct.objects.update_or_create(
        supplier=supplier,
        product=product,
        defaults={"price_per_unit": item["price_per_unit"]}
    )

    updated.append(f"  {'✓ NEW' if created_sp else '↺ UPD'} {product_name} — {item['price_per_unit']} ₪/{unit}")

print(f"\nAdded {len(updated)} products to {supplier.name}:")
for line in updated:
    print(line)

print(f"\nDone! Supplier ID={supplier.id}")
