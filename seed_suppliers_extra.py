from django.contrib.auth import get_user_model
from apps.catalog.models import Supplier, Product, SupplierProduct

User = get_user_model()
owner = User.objects.filter(is_superuser=True).first() or User.objects.first()

suppliers_data = [
    {
        "name": "משה ופלפלים - שוק מחנה יהודה",
        "phone": "0534567890",
        "whatsapp_number": "0534567890",
        "region": "center",
        "minimum_order": 400,
        "prices": [
            {"product_name": "עגבניה",       "price_per_unit": "4.50",  "unit": "KG"},
            {"product_name": "עגבניות שרי",  "price_per_unit": "12.00", "unit": "KG"},
            {"product_name": "מלפפון",       "price_per_unit": "4.20",  "unit": "KG"},
            {"product_name": "מלפפון שרי",   "price_per_unit": "7.80",  "unit": "KG"},
            {"product_name": "פלפל אדום",    "price_per_unit": "8.50",  "unit": "KG"},
            {"product_name": "פלפל ירוק",    "price_per_unit": "5.00",  "unit": "KG"},
            {"product_name": "פלפל צהוב",    "price_per_unit": "11.50", "unit": "KG"},
            {"product_name": "בצל",          "price_per_unit": "3.50",  "unit": "KG"},
            {"product_name": "שום",          "price_per_unit": "15.50", "unit": "KG"},
            {"product_name": "גזר",          "price_per_unit": "3.20",  "unit": "KG"},
            {"product_name": "תפוח אדמה",    "price_per_unit": "4.80",  "unit": "KG"},
            {"product_name": "בטטה",         "price_per_unit": "7.00",  "unit": "KG"},
            {"product_name": "חציל",         "price_per_unit": "7.50",  "unit": "KG"},
            {"product_name": "קישוא",        "price_per_unit": "5.50",  "unit": "KG"},
            {"product_name": "ברוקולי",      "price_per_unit": "10.50", "unit": "KG"},
            {"product_name": "כרובית",       "price_per_unit": "9.50",  "unit": "KG"},
            {"product_name": "כרוב לבן",     "price_per_unit": "4.20",  "unit": "KG"},
            {"product_name": "תרד",          "price_per_unit": "14.50", "unit": "KG"},
            {"product_name": "חסה",          "price_per_unit": "3.80",  "unit": "UNIT"},
            {"product_name": "עלי רוקט",     "price_per_unit": "22.00", "unit": "KG"},
            {"product_name": "פטרוזיליה",    "price_per_unit": "13.00", "unit": "KG"},
            {"product_name": "כוסברה",       "price_per_unit": "16.50", "unit": "KG"},
            {"product_name": "נענע",         "price_per_unit": "16.00", "unit": "KG"},
            {"product_name": "לימון",        "price_per_unit": "6.50",  "unit": "KG"},
            {"product_name": "אבוקדו",       "price_per_unit": "17.50", "unit": "KG"},
        ]
    },
    {
        "name": "דוד הירוק - אגרות הצפון",
        "phone": "0509876543",
        "whatsapp_number": "0509876543",
        "region": "north",
        "minimum_order": 600,
        "prices": [
            {"product_name": "עגבניה",       "price_per_unit": "5.20",  "unit": "KG"},
            {"product_name": "עגבניות שרי",  "price_per_unit": "10.80", "unit": "KG"},
            {"product_name": "מלפפון",       "price_per_unit": "3.60",  "unit": "KG"},
            {"product_name": "מלפפון שרי",   "price_per_unit": "9.00",  "unit": "KG"},
            {"product_name": "פלפל אדום",    "price_per_unit": "9.80",  "unit": "KG"},
            {"product_name": "פלפל ירוק",    "price_per_unit": "6.20",  "unit": "KG"},
            {"product_name": "פלפל צהוב",    "price_per_unit": "9.50",  "unit": "KG"},
            {"product_name": "בצל",          "price_per_unit": "2.90",  "unit": "KG"},
            {"product_name": "שום",          "price_per_unit": "19.00", "unit": "KG"},
            {"product_name": "גזר",          "price_per_unit": "4.00",  "unit": "KG"},
            {"product_name": "תפוח אדמה",    "price_per_unit": "3.90",  "unit": "KG"},
            {"product_name": "בטטה",         "price_per_unit": "8.20",  "unit": "KG"},
            {"product_name": "חציל",         "price_per_unit": "6.50",  "unit": "KG"},
            {"product_name": "קישוא",        "price_per_unit": "6.30",  "unit": "KG"},
            {"product_name": "ברוקולי",      "price_per_unit": "12.00", "unit": "KG"},
            {"product_name": "כרובית",       "price_per_unit": "8.50",  "unit": "KG"},
            {"product_name": "כרוב לבן",     "price_per_unit": "5.20",  "unit": "KG"},
            {"product_name": "תרד",          "price_per_unit": "12.50", "unit": "KG"},
            {"product_name": "חסה",          "price_per_unit": "4.50",  "unit": "UNIT"},
            {"product_name": "עלי רוקט",     "price_per_unit": "19.50", "unit": "KG"},
            {"product_name": "פטרוזיליה",    "price_per_unit": "15.50", "unit": "KG"},
            {"product_name": "כוסברה",       "price_per_unit": "14.50", "unit": "KG"},
            {"product_name": "נענע",         "price_per_unit": "18.50", "unit": "KG"},
            {"product_name": "לימון",        "price_per_unit": "7.80",  "unit": "KG"},
            {"product_name": "אבוקדו",       "price_per_unit": "15.50", "unit": "KG"},
        ]
    },
    {
        "name": "יוסי פירות - שוק הדרום",
        "phone": "0526543210",
        "whatsapp_number": "0526543210",
        "region": "south",
        "minimum_order": 350,
        "prices": [
            {"product_name": "עגבניה",       "price_per_unit": "5.50",  "unit": "KG"},
            {"product_name": "עגבניות שרי",  "price_per_unit": "11.00", "unit": "KG"},
            {"product_name": "מלפפון",       "price_per_unit": "3.90",  "unit": "KG"},
            {"product_name": "מלפפון שרי",   "price_per_unit": "8.20",  "unit": "KG"},
            {"product_name": "פלפל אדום",    "price_per_unit": "7.90",  "unit": "KG"},
            {"product_name": "פלפל ירוק",    "price_per_unit": "5.80",  "unit": "KG"},
            {"product_name": "פלפל צהוב",    "price_per_unit": "10.50", "unit": "KG"},
            {"product_name": "בצל",          "price_per_unit": "3.10",  "unit": "KG"},
            {"product_name": "שום",          "price_per_unit": "16.50", "unit": "KG"},
            {"product_name": "גזר",          "price_per_unit": "3.80",  "unit": "KG"},
            {"product_name": "תפוח אדמה",    "price_per_unit": "4.50",  "unit": "KG"},
            {"product_name": "בטטה",         "price_per_unit": "6.80",  "unit": "KG"},
            {"product_name": "חציל",         "price_per_unit": "7.00",  "unit": "KG"},
            {"product_name": "קישוא",        "price_per_unit": "6.00",  "unit": "KG"},
            {"product_name": "ברוקולי",      "price_per_unit": "11.50", "unit": "KG"},
            {"product_name": "כרובית",       "price_per_unit": "9.00",  "unit": "KG"},
            {"product_name": "כרוב לבן",     "price_per_unit": "4.80",  "unit": "KG"},
            {"product_name": "תרד",          "price_per_unit": "13.00", "unit": "KG"},
            {"product_name": "חסה",          "price_per_unit": "4.20",  "unit": "UNIT"},
            {"product_name": "עלי רוקט",     "price_per_unit": "20.50", "unit": "KG"},
            {"product_name": "פטרוזיליה",    "price_per_unit": "14.50", "unit": "KG"},
            {"product_name": "כוסברה",       "price_per_unit": "15.50", "unit": "KG"},
            {"product_name": "נענע",         "price_per_unit": "17.50", "unit": "KG"},
            {"product_name": "לימון",        "price_per_unit": "6.80",  "unit": "KG"},
            {"product_name": "אבוקדו",       "price_per_unit": "14.50", "unit": "KG"},
        ]
    },
]

for s_data in suppliers_data:
    prices = s_data.pop("prices")
    supplier, created = Supplier.objects.get_or_create(
        phone=s_data["phone"],
        defaults={**s_data, "owner": owner}
    )
    print(f"\nSupplier {'created' if created else 'exists'}: {supplier.name}")

    for item in prices:
        product, _ = Product.objects.get_or_create(
            name=item["product_name"],
            defaults={"unit": item["unit"]}
        )
        sp, created_sp = SupplierProduct.objects.update_or_create(
            supplier=supplier,
            product=product,
            defaults={"price_per_unit": item["price_per_unit"]}
        )
        print(f"  {'✓' if created_sp else '↺'} {item['product_name']} — {item['price_per_unit']} ₪")

print("\nDone! 3 suppliers added.")
