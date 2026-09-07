"""
Seeds a complete, demo-ready dataset: catalog products, suppliers with prices,
a demo customer, and (optionally) back-dated order history for the dashboard.

Idempotent — running it twice leaves the same rows. Prices are generated from a
fixed seed, so the numbers you rehearse on are the numbers the client sees.

Usage:
    python manage.py seed_demo
    python manage.py seed_demo --reset                  # wipe demo rows first
    python manage.py seed_demo --with-orders            # + 8 weeks of history
    python manage.py seed_demo --customer-phone 0501234567 \
                               --supplier-whatsapp +972521234567
"""
import json
import random
import re
from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.catalog.models import Product, Region, Supplier, SupplierProduct

DATA_DIR = Path(settings.BASE_DIR) / "data"
CATALOG_PATH = DATA_DIR / "products_catalog.json"
BASE_PRICES_PATH = DATA_DIR / "demo_base_prices.json"

PRICE_SEED = 20260906

# name, region, minimum_order, phone, whatsapp, price bias, catalog coverage
# All five sit in the demo customer's region, because _get_available_suppliers()
# filters strictly by region — a supplier outside it would silently never appear
# in the demo. The two with coverage 1.00 guarantee every catalog product is
# quotable; the engine raises if a requested product has no supplier at all.
# No supplier ships for free: a wholesaler sends a truck to an institutional
# kitchen only above a real floor, and the spread between those floors is half
# the reason the assignment engine exists. Set for the target customer — a
# kitchen or hotel ordering ~₪1,000 a week clears the ₪300–600 tier easily and
# reaches ₪800 only by consolidating, which is exactly the trade-off to show.
DEMO_SUPPLIERS = [
    ("ירקות השרון", Region.CENTER, 400, "0392200110", "+972521100110", 0.96, 1.00),
    ("משק דגן סיטונאות", Region.CENTER, 600, "0392200120", "+972521100120", 0.92, 0.80),
    ("אחים בוזגלו תוצרת חקלאית", Region.CENTER, 350, "0392200130", "+972521100130", 1.05, 0.90),
    ("גרין ליין ירקות", Region.CENTER, 800, "0392200140", "+972521100140", 0.88, 0.60),
    ("שוק העיר הפצה", Region.CENTER, 300, "0392200150", "+972521100150", 1.10, 1.00),
]

# Every supplier is genuinely cheap on part of the catalog (what they grow,
# import, or move volume on) and merely average on the rest. Without this the
# per-item noise averages out over a basket and the best single supplier ends
# up within ~3% of the optimal split — which is not how produce buying works,
# and understates what the engine is for.
SPECIALTY_SHARE = 0.30
SPECIALTY_DISCOUNT = 0.78

DEMO_EMAIL = "demo@smart-orders.co.il"
DEMO_PASSWORD = "Demo!2026"
DEMO_COMPANY = "מטבח מרכזי — קרן חינוך"

# Products a kitchen actually orders weekly, with a plausible quantity range.
# Used for --with-orders history and printed as a ready-made demo basket.
DEMO_BASKET = [
    ("עגבנייה", 40, 80),
    ("מלפפון", 30, 60),
    ("בצל יבש", 40, 70),
    ("תפוח אדמה לבן", 60, 120),
    ("גזר", 25, 50),
    ("פלפל אדום", 15, 30),
    ("חסה אייסברג", 20, 40),
    ("כרוב לבן", 20, 40),
    ("לימון", 10, 20),
    ("פטרוזיליה", 15, 30),
]


class Command(BaseCommand):
    help = "Seed demo data: products, suppliers, prices, a demo customer, and order history."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset", action="store_true",
            help="Delete the demo suppliers, their prices and the demo customer's orders first.",
        )
        parser.add_argument(
            "--wipe-suppliers", action="store_true",
            help="DESTRUCTIVE. Delete EVERY supplier in the database, not just the demo ones, "
                 "so the client sees exactly the demo suppliers and nothing else. Cascades to "
                 "their prices and to any order line referencing them. Never run this on production.",
        )
        parser.add_argument(
            "--with-orders", action="store_true",
            help="Also create ~8 weeks of back-dated orders so the dashboard has history.",
        )
        parser.add_argument(
            "--customer-phone", default="",
            help="Phone the demo customer messages from, local format (e.g. 0501234567). "
                 "Must be a real WhatsApp number for the live WhatsApp demo.",
        )
        parser.add_argument(
            "--customer-email", default=DEMO_EMAIL,
            help=f"Demo customer login email (default: {DEMO_EMAIL}).",
        )
        parser.add_argument(
            "--supplier-whatsapp", default="",
            help="Real WhatsApp number to assign to the FIRST demo supplier, so you can "
                 "play the supplier live from a second phone. E.164 (e.g. +972521234567).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if options["wipe_suppliers"]:
            self._wipe_suppliers()
        if options["reset"]:
            self._reset(options["customer_email"])

        products = self._seed_products()
        suppliers = self._seed_suppliers(options["supplier_whatsapp"])
        price_count = self._seed_prices(products, suppliers)
        user, profile = self._seed_customer(
            options["customer_email"], options["customer_phone"]
        )

        order_count = 0
        if options["with_orders"]:
            order_count = self._seed_orders(user, profile)

        self._report(products, suppliers, price_count, user, profile, order_count)

    # ── products ──────────────────────────────────────────────────────

    def _seed_products(self):
        if not CATALOG_PATH.exists():
            raise CommandError(f"לא נמצא קובץ הקטלוג: {CATALOG_PATH}")
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))

        products = {}
        for entry in catalog["products"]:
            product, _ = Product.objects.get_or_create(
                name=entry["name"], defaults={"unit": entry["unit"]},
            )
            products[product.name] = product
        return products

    # ── suppliers ─────────────────────────────────────────────────────

    def _seed_suppliers(self, supplier_whatsapp):
        if supplier_whatsapp:
            supplier_whatsapp = "+" + re.sub(r"\D", "", supplier_whatsapp)

        suppliers = []
        for index, (name, region, minimum, phone, whatsapp, bias, coverage) in enumerate(
            DEMO_SUPPLIERS
        ):
            if index == 0 and supplier_whatsapp:
                whatsapp = supplier_whatsapp
            supplier, _ = Supplier.objects.update_or_create(
                name=name,
                defaults={
                    "phone": re.sub(r"\D", "", phone),
                    "whatsapp_number": whatsapp,
                    "region": region,
                    "minimum_order": Decimal(minimum),
                },
            )
            suppliers.append((supplier, bias, coverage))
        return suppliers

    # ── prices ────────────────────────────────────────────────────────

    def _seed_prices(self, products, suppliers):
        base_prices, unit_fallback = self._load_base_prices()
        rng = random.Random(PRICE_SEED)

        unknown = sorted(set(base_prices) - set(products))
        if unknown:
            self.stdout.write(self.style.WARNING(
                f"  שמות בקובץ המחירים שאין להם מוצר בקטלוג ({len(unknown)}): {', '.join(unknown)}"
            ))

        rows = []
        for supplier, bias, coverage in suppliers:
            SupplierProduct.objects.filter(supplier=supplier).delete()
            for name, product in products.items():
                if rng.random() > coverage:
                    continue
                base = base_prices.get(name) or unit_fallback.get(product.unit, 8.0)
                # Per-supplier bias (who is generally cheap), a specialty block
                # they undercut everyone on, plus per-item noise — that spread is
                # what makes the split-across-suppliers recommendation pay off.
                specialty = SPECIALTY_DISCOUNT if rng.random() < SPECIALTY_SHARE else 1.0
                price = Decimal(str(base * bias * specialty * rng.uniform(0.90, 1.10)))
                rows.append(SupplierProduct(
                    supplier=supplier,
                    product=product,
                    price_per_unit=max(
                        Decimal("0.10"),
                        price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                    ),
                ))
        SupplierProduct.objects.bulk_create(rows, batch_size=500)
        return len(rows)

    def _load_base_prices(self):
        """
        Merges the file's price blocks in its declared order, first block wins,
        so a surveyed wholesale price is never overwritten by an estimate.
        """
        if not BASE_PRICES_PATH.exists():
            raise CommandError(f"לא נמצא קובץ מחירי הבסיס: {BASE_PRICES_PATH}")
        payload = json.loads(BASE_PRICES_PATH.read_text(encoding="utf-8"))

        prices, provenance = {}, {}
        for block in payload["_merge_order"]:
            for name, price in payload[block].items():
                if name not in prices:
                    prices[name] = price
                    provenance[name] = block
        self._provenance = provenance
        return prices, payload["unit_fallback"]

    # ── customer ──────────────────────────────────────────────────────

    def _seed_customer(self, email, phone):
        from apps.users.models import Profile

        User = get_user_model()
        user, created = User.objects.get_or_create(
            email=email, defaults={"first_name": "דמו", "last_name": "לקוח"},
        )
        if created:
            user.set_password(DEMO_PASSWORD)
            user.save(update_fields=["password"])

        defaults = {
            "company_name": DEMO_COMPANY,
            "company_address": "התעשייה 12, ראשון לציון",
            "position": "מנהל רכש",
            "region": Region.CENTER,
            "is_active": True,
        }
        if phone:
            defaults["phone"] = re.sub(r"\D", "", phone)
        profile, _ = Profile.objects.update_or_create(user=user, defaults=defaults)
        return user, profile

    # ── order history ─────────────────────────────────────────────────

    def _seed_orders(self, user, profile):
        from apps.orders.models import OrderRequest
        from apps.orders.services import build_order

        existing = OrderRequest.objects.filter(user=user).count()
        if existing:
            # Re-running must not stack another 8 weeks on top of the history
            # that is already there; --reset is the way to rebuild it.
            self.stdout.write(self.style.WARNING(
                f"  ל-{user.email} כבר יש {existing} הזמנות — היסטוריה לא נוצרה שוב "
                f"(הרץ עם --reset כדי לבנות מחדש)"
            ))
            return 0

        rng = random.Random(PRICE_SEED + 1)
        now = timezone.now()
        created = 0

        for weeks_ago in range(8, 0, -1):
            basket = []
            for name, low, high in DEMO_BASKET:
                product = Product.objects.filter(name=name).first()
                if not product:
                    continue
                basket.append({
                    "product": product,
                    "quantity": Decimal(str(round(rng.uniform(low, high), 1))),
                })
            if not basket:
                break

            scenario = "cheapest" if weeks_ago % 3 else "fewest_suppliers"
            order, _ = build_order(user, profile.region, basket, scenario=scenario)
            OrderRequest.objects.filter(pk=order.pk).update(
                status=OrderRequest.Status.DELIVERED,
                created_at=now - timedelta(weeks=weeks_ago, hours=rng.randint(0, 10)),
            )
            created += 1
        return created

    # ── reset ─────────────────────────────────────────────────────────

    def _wipe_suppliers(self):
        """Leaves the catalog and users alone; clears the supplier side entirely."""
        from apps.orders.models import OrderRequestProduct

        lines, _ = OrderRequestProduct.objects.all().delete()
        prices, _ = SupplierProduct.objects.all().delete()
        suppliers, _ = Supplier.objects.all().delete()
        self.stdout.write(self.style.WARNING(
            f"  ניקוי מלא: נמחקו {suppliers} ספקים, {prices} רשומות מחיר, {lines} שורות הזמנה"
        ))

    def _reset(self, email):
        from apps.orders.models import OrderRequest
        from apps.users.models import Profile

        User = get_user_model()
        demo_names = [row[0] for row in DEMO_SUPPLIERS]

        user = User.objects.filter(email=email).first()
        if user:
            deleted, _ = OrderRequest.objects.filter(user=user).delete()
            self.stdout.write(f"  נמחקו {deleted} רשומות הזמנה של {email}")
            Profile.objects.filter(user=user).delete()

        prices_deleted, _ = SupplierProduct.objects.filter(
            supplier__name__in=demo_names
        ).delete()
        suppliers_deleted, _ = Supplier.objects.filter(name__in=demo_names).delete()
        self.stdout.write(
            f"  נמחקו {suppliers_deleted} ספקי דמו ו-{prices_deleted} רשומות מחיר"
        )

    # ── report ────────────────────────────────────────────────────────

    def _report(self, products, suppliers, price_count, user, profile, order_count):
        w = self.stdout.write
        w(self.style.SUCCESS("\n══ נתוני דמו נטענו ══\n"))
        w(f"  מוצרים בקטלוג:      {len(products)}")
        w(f"  ספקים:              {len(suppliers)}")
        w(f"  רשומות מחיר:        {price_count}")

        by_source = {}
        for name in products:
            source = self._provenance.get(name, "unit_fallback")
            by_source[source] = by_source.get(source, 0) + 1
        w("  מקור מחיר הבסיס:")
        for source, count in sorted(by_source.items(), key=lambda kv: -kv[1]):
            w(f"    {source}: {count} מוצרים")
        if order_count:
            w(f"  הזמנות היסטוריות:   {order_count}")
        w("")
        w(self.style.HTTP_INFO("  ספקים:"))
        for supplier, bias, _ in suppliers:
            count = supplier.products.count()
            w(f"    • {supplier.name} — {supplier.get_region_display()} — "
              f"מינימום ₪{supplier.minimum_order:.0f} — {count} מוצרים — {supplier.whatsapp_number}")
        w("")
        w(self.style.HTTP_INFO("  לקוח הדמו:"))
        w(f"    התחברות: {user.email} / {DEMO_PASSWORD}")
        w(f"    חברה:    {profile.company_name} ({profile.get_region_display()})")
        w(f"    טלפון:   {profile.phone or '(לא הוגדר — הרץ עם --customer-phone לדמו WhatsApp)'}")
        w("")
        w(self.style.HTTP_INFO("  סל מוכן להדגמה (הדבק ב'הזמנה חדשה'):"))
        w("    " + ", ".join(f"{name} {low}" for name, low, _ in DEMO_BASKET))
        w("")
