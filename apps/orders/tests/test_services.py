from decimal import Decimal
from django.test import TestCase
from django.contrib.auth import get_user_model

from apps.catalog.models import Product, Supplier, SupplierProduct, Region, Unit
from apps.orders.models import OrderRequest
from apps.orders.services import build_order

User = get_user_model()


def make_user(email="user@test.com"):
    return User.objects.create_user(email=email, password="pass1234")


def make_product(name):
    return Product.objects.create(name=name, unit=Unit.KG)


_supplier_counter = 0


def make_supplier(name, minimum_order=0, region=Region.CENTER, owner=None):
    global _supplier_counter
    _supplier_counter += 1
    phone = f"05{_supplier_counter:08d}"
    return Supplier.objects.create(
        name=name,
        phone=phone,
        whatsapp_number=phone,
        region=region,
        minimum_order=minimum_order,
        owner=owner,
    )


def set_price(supplier, product, price):
    return SupplierProduct.objects.create(supplier=supplier, product=product, price_per_unit=price)


class BuildOrderServiceTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.tomato = make_product("עגבנייה")
        self.cucumber = make_product("מלפפון")
        self.carrot = make_product("גזר")

    def test_single_supplier_single_product(self):
        """Order created with correct supplier and price"""
        supplier = make_supplier("ספק א")
        set_price(supplier, self.tomato, "5.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
        ])

        self.assertEqual(order.status, OrderRequest.Status.PENDING)
        self.assertEqual(order.products.count(), 1)
        product = order.products.first()
        self.assertEqual(product.supplier, supplier)
        self.assertEqual(float(product.unit_price), 5.00)
        self.assertEqual(float(order.total_price), 50.00)

    def test_picks_cheapest_supplier(self):
        """Algorithm picks the cheapest supplier"""
        cheap = make_supplier("זול")
        expensive = make_supplier("יקר")
        set_price(cheap, self.tomato, "3.00")
        set_price(expensive, self.tomato, "6.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
        ])

        self.assertEqual(order.products.first().supplier, cheap)

    def test_no_split_when_difference_under_10_percent(self):
        """Stays with one supplier when price diff <= 10%"""
        supplier_a = make_supplier("ספק א")
        supplier_b = make_supplier("ספק ב")
        set_price(supplier_a, self.tomato, "5.00")
        set_price(supplier_b, self.tomato, "5.40")  # 7.4% difference — no split

        set_price(supplier_a, self.cucumber, "3.00")
        set_price(supplier_b, self.cucumber, "3.20")  # 6.25% difference — no split

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("5")},
            {"product": self.cucumber, "quantity": Decimal("5")},
        ])

        suppliers_used = {item.supplier for item in order.products.all()}
        self.assertEqual(len(suppliers_used), 1)

    def test_splits_when_difference_over_10_percent(self):
        """Splits between suppliers when one product has >10% cheaper option"""
        supplier_a = make_supplier("ספק א")
        supplier_b = make_supplier("ספק ב")

        # tomato: supplier_a is 20% cheaper → pick supplier_a
        set_price(supplier_a, self.tomato, "4.00")
        set_price(supplier_b, self.tomato, "5.00")

        # cucumber: supplier_b is 20% cheaper → pick supplier_b
        set_price(supplier_a, self.cucumber, "5.00")
        set_price(supplier_b, self.cucumber, "4.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
            {"product": self.cucumber, "quantity": Decimal("10")},
        ])

        suppliers_used = {item.supplier for item in order.products.all()}
        self.assertEqual(len(suppliers_used), 2)

    def test_minimum_order_forces_switch_single_product(self):
        """Single product: supplier below minimum → switch to next cheapest."""
        cheap = make_supplier("זול", minimum_order=200)
        pricey = make_supplier("יקר", minimum_order=0)

        set_price(cheap, self.tomato, "3.00")   # 10 * 3 = 30 < 200 → switch
        set_price(pricey, self.tomato, "4.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
        ])

        self.assertEqual(order.products.first().supplier, pricey)

    def test_minimum_order_multiple_products_all_switch_together(self):
        """
        All products from a below-minimum supplier switch to the alt together,
        even when no single product alone would meet the alt's minimum.
        Bug case: alt.minimum=50, tomato=30, cucumber=20 → neither alone passes,
        but together (50 >= 50) they do.
        """
        cheap = make_supplier("זול", minimum_order=200)
        alt = make_supplier("חלופי", minimum_order=50)

        set_price(cheap, self.tomato, "3.00")    # 10 * 3 = 30
        set_price(cheap, self.cucumber, "2.00")  # 10 * 2 = 20
        # cheap total = 50 < 200 → must switch

        set_price(alt, self.tomato, "4.00")    # 10 * 4 = 40
        set_price(alt, self.cucumber, "1.50")  # 10 * 1.5 = 15
        # alt total if both move = 55 >= 50 ✓

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
            {"product": self.cucumber, "quantity": Decimal("10")},
        ])

        suppliers_used = {item.supplier for item in order.products.all()}
        print(suppliers_used)
        self.assertEqual(suppliers_used, {alt})

    def test_minimum_order_alt_also_below_minimum_stays_original(self):
        """If the only alt also won't meet its minimum, keep original assignment."""
        cheap = make_supplier("זול", minimum_order=500)
        alt = make_supplier("חלופי", minimum_order=500)

        set_price(cheap, self.tomato, "3.00")  # 10 * 3 = 30 < 500
        set_price(alt, self.tomato, "4.00")    # 10 * 4 = 40 < 500 → alt also fails

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
        ])

        # Neither meets minimum — stays with cheapest (best-effort)
        self.assertEqual(order.products.first().supplier, cheap)

    def test_minimum_order_picks_second_cheapest_when_cheapest_alt_fails(self):
        """
        Alt A (second cheapest) doesn't meet its minimum.
        Alt B (third cheapest) does → switch to B.
        """
        cheap = make_supplier("זול", minimum_order=300)
        alt_a = make_supplier("חלופי א", minimum_order=200)   # 10*4=40 < 200 → skip
        alt_b = make_supplier("חלופי ב", minimum_order=0)     # no minimum → use

        set_price(cheap, self.tomato, "3.00")
        set_price(alt_a, self.tomato, "4.00")
        set_price(alt_b, self.tomato, "5.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
        ])

        self.assertEqual(order.products.first().supplier, alt_b)

    def test_minimum_order_not_triggered_when_met(self):
        """Supplier meets minimum → no switch."""
        supplier = make_supplier("ספק", minimum_order=30)
        other = make_supplier("אחר", minimum_order=0)

        set_price(supplier, self.tomato, "3.00")   # 10 * 3 = 30 >= 30 ✓
        set_price(other, self.tomato, "4.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
        ])

        self.assertEqual(order.products.first().supplier, supplier)

    def test_raises_error_when_no_supplier_for_product(self):
        """Raises ValueError if no supplier carries the product"""
        with self.assertRaises(ValueError):
            build_order(self.user, Region.CENTER, [
                {"product": self.tomato, "quantity": Decimal("5")},
            ])

    def test_private_supplier_visible_to_owner(self):
        """User's private supplier is included in search"""
        private = make_supplier("פרטי", owner=self.user, region=Region.NORTH)
        set_price(private, self.tomato, "2.00")

        order, _ = build_order(self.user, Region.NORTH, [
            {"product": self.tomato, "quantity": Decimal("5")},
        ])

        self.assertEqual(order.products.first().supplier, private)

    def test_private_supplier_not_visible_to_other_user(self):
        """Another user cannot see someone else's private supplier"""
        other_user = make_user("other@test.com")
        private = make_supplier("פרטי של אחר", owner=other_user)
        set_price(private, self.tomato, "2.00")

        with self.assertRaises(ValueError):
            build_order(self.user, Region.CENTER, [
                {"product": self.tomato, "quantity": Decimal("5")},
            ])

    def test_total_price_is_correct(self):
        """total_price on order equals sum of all products"""
        supplier = make_supplier("ספק")
        set_price(supplier, self.tomato, "5.00")
        set_price(supplier, self.cucumber, "3.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},   # 50
            {"product": self.cucumber, "quantity": Decimal("20")},  # 60
        ])

        self.assertEqual(float(order.total_price), 110.0)
