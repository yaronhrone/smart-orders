from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.contrib.auth import get_user_model


from apps.catalog.models import Product, Supplier, SupplierProduct, Region, Unit
from apps.orders.models import OrderRequest
from apps.orders.services import build_order, suggest_order

User = get_user_model()


def make_user(email="user@test.com"):
    return User.objects.create_user(email=email, password="pass1234")


def make_product(name):
    return Product.objects.create(name=name, unit=Unit.KG)


_supplier_counter = 0


def make_supplier(name, minimum_order=0, region=Region.CENTER):
    global _supplier_counter
    _supplier_counter += 1
    phone = f"05{_supplier_counter:08d}"
    return Supplier.objects.create(
        name=name,
        phone=phone,
        whatsapp_number=phone,
        region=region,
        minimum_order=minimum_order,
    )


def set_price(supplier, product, price):
    return SupplierProduct.objects.create(supplier=supplier, product=product, price_per_unit=price)


class BuildOrderServiceTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.tomato = make_product("tomato")
        self.cucumber = make_product("cucumber")
        self.carrot = make_product("carrot")

    def test_single_supplier_single_product(self):
        """Order created with correct supplier and price"""
        supplier = make_supplier("supplier A")
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
        cheap = make_supplier("cheap")
        expensive = make_supplier("expensive")
        set_price(cheap, self.tomato, "3.00")
        set_price(expensive, self.tomato, "6.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
        ])

        self.assertEqual(order.products.first().supplier, cheap)

    def test_minimum_order_forces_switch_single_product(self):
        """Single product: supplier below minimum → switch to next cheapest."""
        cheap = make_supplier("cheap", minimum_order=200)
        pricey = make_supplier("expensive", minimum_order=0)

        set_price(cheap, self.tomato, "3.00")
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
        cheap = make_supplier("cheap", minimum_order=200)
        alt = make_supplier("alt", minimum_order=50)

        set_price(cheap, self.tomato, "3.00")
        set_price(cheap, self.cucumber, "2.00")

        set_price(alt, self.tomato, "4.00")
        set_price(alt, self.cucumber, "1.50")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
            {"product": self.cucumber, "quantity": Decimal("10")},
        ])
        suppliers_used = {item.supplier  for item in order.products.all()}
        self.assertEqual(suppliers_used, {alt})

    def test_minimum_order_alt_also_below_minimum_stays_original(self):
        """If the only alt also won't meet its minimum, keep original assignment."""
        cheap = make_supplier("cheap", minimum_order=500)
        alt = make_supplier("alt", minimum_order=500)

        set_price(cheap, self.tomato, "3.00")
        set_price(alt, self.tomato, "4.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
        ])

        self.assertEqual(order.products.first().supplier, cheap)

    def test_minimum_order_picks_second_cheapest_when_cheapest_alt_fails(self):
        """
        Alt A (second cheapest) doesn't meet its minimum.
        Alt B (third cheapest) does → switch to B.
        """
        cheap = make_supplier("cheap", minimum_order=300)
        alt_a = make_supplier("supplier A", minimum_order=200)
        alt_b = make_supplier("alt B", minimum_order=0)

        set_price(cheap, self.tomato, "3.00")
        set_price(alt_a, self.tomato, "4.00")
        set_price(alt_b, self.tomato, "5.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
        ])

        self.assertEqual(order.products.first().supplier, alt_b)

    def test_minimum_order_not_triggered_when_met(self):
        """Supplier meets minimum → no switch."""
        supplier = make_supplier("supplier", minimum_order=30)
        other = make_supplier("other",minimum_order=0)
        set_price(supplier, self.tomato, "3.00")
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

    def test_total_price_is_correct(self):
        """total_price on order equals sum of all products"""
        supplier = make_supplier("supplier")
        set_price(supplier, self.tomato, "5.00")
        set_price(supplier, self.cucumber, "3.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
            {"product": self.cucumber, "quantity": Decimal("20")},
        ])

        self.assertEqual(float(order.total_price), 110.0)

    def test_force_switch_picks_cheapest_alternative_by_total_cost(self):
        """
        When the under-minimum supplier has multiple valid alternatives, pick
        the one with the LOWEST total cost on the group — not the one cheapest
        on items[0]. Earlier `_find_next_valid_supplier` sorted by items[0]
        price only, which could return a suboptimal alternative.
        """
        cheap = make_supplier("cheap", minimum_order=500)
        alt_a = make_supplier("alt_a", minimum_order=0)
        alt_b = make_supplier("alt_b", minimum_order=0)

        set_price(cheap, self.tomato, "1.00")
        set_price(cheap, self.cucumber, "1.00")

        # alt_a cheaper on tomato (items[0]) but very expensive on cucumber → total 120
        set_price(alt_a, self.tomato, "2.00")
        set_price(alt_a, self.cucumber, "10.00")

        # alt_b more expensive on tomato but cheaper overall → total 80
        set_price(alt_b, self.tomato, "3.00")
        set_price(alt_b, self.cucumber, "5.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("10")},
            {"product": self.cucumber, "quantity": Decimal("10")},
        ])
        suppliers_used = {item.supplier for item in order.products.all()}
        self.assertEqual(suppliers_used, {alt_b})


class FewestSuppliersTests(TestCase):
    """Tests for the `fewest_suppliers` set-cover scenario."""

    def setUp(self):
        self.user = make_user()
        self.tomato = make_product("tomato")
        self.cucumber = make_product("cucumber")
        self.carrot = make_product("carrot")

    def test_picks_minimum_supplier_count(self):
        """One supplier carrying all 3 beats 3 separate cheap suppliers."""
        one_stop = make_supplier("one_stop", minimum_order=0)
        cheap_t = make_supplier("cheap_t", minimum_order=0)
        cheap_c = make_supplier("cheap_c", minimum_order=0)
        cheap_cr = make_supplier("cheap_cr", minimum_order=0)

        # one_stop is more expensive on each product but covers all
        set_price(one_stop, self.tomato, "10.00")
        set_price(one_stop, self.cucumber, "10.00")
        set_price(one_stop, self.carrot, "10.00")

        set_price(cheap_t, self.tomato, "1.00")
        set_price(cheap_c, self.cucumber, "1.00")
        set_price(cheap_cr, self.carrot, "1.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("5")},
            {"product": self.cucumber, "quantity": Decimal("5")},
            {"product": self.carrot, "quantity": Decimal("5")},
        ], scenario="fewest_suppliers")

        suppliers_used = {item.supplier for item in order.products.all()}
        self.assertEqual(suppliers_used, {one_stop})

    def test_breaks_tie_by_cost(self):
        """
        When two set-cover solutions need the same number of suppliers,
        prefer the one with lower total cost.
        """
        # Two ways to cover {tomato, cucumber} with 1 supplier:
        # supplier_expensive covers both at 10 each — total 100
        # supplier_cheap covers both at 3 each — total 30
        supplier_expensive = make_supplier("expensive", minimum_order=0)
        supplier_cheap = make_supplier("cheap", minimum_order=0)

        set_price(supplier_expensive, self.tomato, "10.00")
        set_price(supplier_expensive, self.cucumber, "10.00")
        set_price(supplier_cheap, self.tomato, "3.00")
        set_price(supplier_cheap, self.cucumber, "3.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("5")},
            {"product": self.cucumber, "quantity": Decimal("5")},
        ], scenario="fewest_suppliers")

        suppliers_used = {item.supplier for item in order.products.all()}
        self.assertEqual(suppliers_used, {supplier_cheap})

    def test_respects_minimum_order(self):
        """
        Greedy set-cover picks one_stop (covers everything, cheapest). Its
        minimum is violated, so _force_minimum_switch hands the whole group
        off to one_stop_alt — another all-covering supplier that meets
        minimum.

        Note: the algorithm doesn't split a group across multiple suppliers;
        it only swaps the whole group to a single alternative. If no single
        alternative can absorb the group, the violation is left in place and
        surfaced via minimum_issues.
        """
        one_stop = make_supplier("one_stop", minimum_order=1000)
        one_stop_alt = make_supplier("one_stop_alt", minimum_order=0)

        set_price(one_stop, self.tomato, "1.00")
        set_price(one_stop, self.cucumber, "1.00")
        set_price(one_stop_alt, self.tomato, "3.00")
        set_price(one_stop_alt, self.cucumber, "3.00")

        order, _ = build_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("5")},
            {"product": self.cucumber, "quantity": Decimal("5")},
        ], scenario="fewest_suppliers")

        suppliers_used = {item.supplier for item in order.products.all()}
        self.assertEqual(suppliers_used, {one_stop_alt})

    def test_raises_when_no_supplier_for_product(self):
        """fewest_suppliers must also raise when a product has no supplier."""
        # tomato has a supplier, cucumber has none
        s = make_supplier("s", minimum_order=0)
        set_price(s, self.tomato, "1.00")

        with self.assertRaises(ValueError):
            build_order(self.user, Region.CENTER, [
                {"product": self.tomato, "quantity": Decimal("5")},
                {"product": self.cucumber, "quantity": Decimal("5")},
            ], scenario="fewest_suppliers")


class SuggestOrderShapeTests(TestCase):
    """Tests for the suggest_order response structure."""

    def setUp(self):
        self.user = make_user()
        self.tomato = make_product("tomato")

    def test_minimum_issues_returned_for_both_scenarios(self):
        """`minimum_issues` is a dict keyed by scenario name."""
        s = make_supplier("s", minimum_order=0)
        set_price(s, self.tomato, "1.00")

        result = suggest_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("5")},
        ])

        self.assertIn("minimum_issues", result)
        self.assertIsInstance(result["minimum_issues"], dict)
        self.assertIn("cheapest", result["minimum_issues"])
        self.assertIn("fewest_suppliers", result["minimum_issues"])
        # No minimum violations with this setup
        self.assertEqual(result["minimum_issues"]["cheapest"], [])
        self.assertEqual(result["minimum_issues"]["fewest_suppliers"], [])

    def test_minimum_issues_reports_violations_per_scenario(self):
        """When a scenario can't reach a supplier's minimum, it's reported."""
        # Single supplier with high minimum — switch impossible (no alternatives)
        only = make_supplier("only", minimum_order=1000)
        set_price(only, self.tomato, "1.00")

        result = suggest_order(self.user, Region.CENTER, [
            {"product": self.tomato, "quantity": Decimal("5")},
        ])
        self.assertEqual(len(result["minimum_issues"]["cheapest"]), 1)
        self.assertEqual(
            result["minimum_issues"]["cheapest"][0]["supplier_id"], only.id
        )
        self.assertEqual(len(result["minimum_issues"]["fewest_suppliers"]), 1)


class ModelValidatorTests(TestCase):
    """Tests for model-level MinValueValidator constraints."""

    def setUp(self):
        self.user = make_user()
        self.tomato = make_product("tomato")
        self.supplier = make_supplier("s", minimum_order=0)

    def test_supplier_product_rejects_negative_price(self):
        sp = SupplierProduct(
            supplier=self.supplier,
            product=self.tomato,
            price_per_unit=Decimal("-1.00"),
        )
        with self.assertRaises(ValidationError):
            sp.full_clean()

    def test_supplier_product_rejects_zero_price(self):
        sp = SupplierProduct(
            supplier=self.supplier,
            product=self.tomato,
            price_per_unit=Decimal("0"),
        )
        with self.assertRaises(ValidationError):
            sp.full_clean()

    def test_supplier_minimum_order_rejects_negative(self):
        s = Supplier(
            name="bad",
            phone="0511111111",
            whatsapp_number="0511111111",
            region=Region.CENTER,
            minimum_order=Decimal("-1"),
        )
        with self.assertRaises(ValidationError):
            s.full_clean()
