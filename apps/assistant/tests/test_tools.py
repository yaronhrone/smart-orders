import datetime as dt
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.assistant.tools import build_context, build_tool_schemas, execute_tool
from apps.catalog.models import Product, ProductAlias, Region, Supplier, SupplierProduct, Unit
from apps.orders.models import OrderRequest, OrderRequestProduct
from apps.orders.tests.factories import make_order
from apps.users.models import Profile

User = get_user_model()
IL = ZoneInfo("Asia/Jerusalem")
SENT = OrderRequest.Status.SENT
_phone = iter(range(10**8, 10**9))


def make_user(email, region=Region.CENTER, company=None, staff=False):
    user = User.objects.create_user(email=email, password="pass1234", is_staff=staff)
    Profile.objects.create(user=user, company_name=company or email, region=region)
    return user


def make_supplier(name, region=Region.CENTER, minimum=0, blocked_until=None):
    phone = f"05{next(_phone)}"
    return Supplier.objects.create(
        name=name, phone=phone, whatsapp_number=phone, region=region,
        minimum_order=minimum, blocked_until=blocked_until,
    )


def add_order(user, supplier, product, quantity, price, when, status=SENT):
    order = make_order(user, supplier, status=status, total_price=0)
    OrderRequestProduct.objects.create(
        order_request=order, product=product, supplier=supplier, quantity=quantity, unit_price=price,
    )
    OrderRequest.objects.filter(pk=order.pk).update(created_at=when)
    return order


def il(year, month, day, hour=12, minute=0):
    return dt.datetime(year, month, day, hour, minute, tzinfo=IL)


class ToolTestBase(TestCase):
    def setUp(self):
        self.alice = make_user("alice@test.com", company="חברת אליס")
        self.bob = make_user("bob@test.com", company="חברת בוב")
        self.admin = make_user("admin@test.com", staff=True)
        self.tomato = Product.objects.create(name="עגבנייה", unit=Unit.KG)
        self.cucumber = Product.objects.create(name="מלפפון", unit=Unit.KG)
        self.lettuce = Product.objects.create(name="חסה", unit=Unit.UNIT)
        self.sup_a = make_supplier("ספק א")
        self.sup_b = make_supplier("ספק ב")

    def run_tool(self, user, name, **args):
        return execute_tool(build_context(user), name, args)


class QuerySpendingTests(ToolTestBase):
    def test_sums_only_the_callers_own_orders(self):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))
        add_order(self.bob, self.sup_a, self.tomato, "100", "5.00", il(2026, 9, 10))

        result = self.run_tool(self.alice, "query_spending")

        self.assertEqual(result["total_spend"], "50.00")
        self.assertEqual(result["order_count"], 1)

    def test_admin_sees_every_customer(self):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))
        add_order(self.bob, self.sup_a, self.tomato, "100", "5.00", il(2026, 9, 10))

        result = self.run_tool(self.admin, "query_spending")

        self.assertEqual(result["total_spend"], "550.00")

    def test_customer_cannot_filter_or_group_by_customer(self):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))
        add_order(self.bob, self.sup_a, self.tomato, "100", "5.00", il(2026, 9, 10))

        filtered = self.run_tool(self.alice, "query_spending", customer="bob@test.com")
        grouped = self.run_tool(self.alice, "query_spending", group_by="customer")

        self.assertEqual(filtered["total_spend"], "50.00")  # filter silently ignored, still only Alice
        self.assertIn("error", grouped)

    def test_cancelled_and_pending_orders_do_not_count(self):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10), OrderRequest.Status.CANCELLED)
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10), OrderRequest.Status.PENDING)

        result = self.run_tool(self.alice, "query_spending")

        self.assertEqual(result["total_spend"], "50.00")
        self.assertEqual(result["order_count"], 1)

    def test_date_range_is_inclusive_in_israel_time(self):
        # 23:30 Israel time on 10 Sept is 20:30 UTC - same day either way, but
        # 00:30 on 11 Sept Israel time (21:30 UTC on the 10th) must NOT be in range.
        add_order(self.alice, self.sup_a, self.tomato, "1", "10.00", il(2026, 9, 7, 0, 0))
        add_order(self.alice, self.sup_a, self.tomato, "1", "20.00", il(2026, 9, 10, 23, 30))
        add_order(self.alice, self.sup_a, self.tomato, "1", "40.00", il(2026, 9, 11, 0, 30))
        add_order(self.alice, self.sup_a, self.tomato, "1", "80.00", il(2026, 9, 6, 23, 59))

        result = self.run_tool(self.alice, "query_spending", date_from="2026-09-07", date_to="2026-09-10")

        self.assertEqual(result["total_spend"], "30.00")

    def test_filters_by_product_with_alias_and_reports_quantity_and_unit(self):
        ProductAlias.objects.create(product=self.tomato, alias="עגבניות")
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))
        add_order(self.alice, self.sup_a, self.cucumber, "10", "3.00", il(2026, 9, 10))

        result = self.run_tool(self.alice, "query_spending", products=["עגבניות"])

        self.assertEqual(result["total_spend"], "50.00")
        self.assertEqual(result["total_quantity"], "10.00")
        self.assertEqual(result["unit"], 'ק"ג')

    def test_unknown_product_returns_suggestions_not_zero(self):
        result = self.run_tool(self.alice, "query_spending", products=["עגבנין"])

        self.assertIn("עגבנייה", result["products_not_found"]["עגבנין"])
        self.assertNotIn("total_spend", result)

    def test_ambiguous_family_asks_instead_of_guessing(self):
        Product.objects.create(name="תפוח אדמה אדום", unit=Unit.KG)
        Product.objects.create(name="תפוח אדמה לבן", unit=Unit.KG)

        result = self.run_tool(self.alice, "query_spending", products=["תפוח אדמה"])

        self.assertEqual(
            sorted(result["ambiguous_products"]["תפוח אדמה"]), ["תפוח אדמה אדום", "תפוח אדמה לבן"],
        )

    def test_group_by_product_gives_weighted_average_price(self):
        add_order(self.alice, self.sup_a, self.tomato, "10", "4.00", il(2026, 9, 10))
        add_order(self.alice, self.sup_b, self.tomato, "30", "8.00", il(2026, 9, 11))
        add_order(self.alice, self.sup_a, self.lettuce, "5", "6.00", il(2026, 9, 11))

        result = self.run_tool(self.alice, "query_spending", group_by="product")

        by_product = {g["product"]: g for g in result["groups"]}
        self.assertEqual(by_product["עגבנייה"]["total_spend"], "280.00")
        self.assertEqual(by_product["עגבנייה"]["avg_unit_price"], "7.00")  # 280 / 40, not (4+8)/2
        self.assertEqual(by_product["עגבנייה"]["min_unit_price"], "4.00")
        self.assertEqual(by_product["עגבנייה"]["max_unit_price"], "8.00")
        self.assertEqual(by_product["חסה"]["unit"], "יחידה")
        self.assertEqual(result["total_spend"], "310.00")

    def test_quantities_of_different_products_are_not_added_together(self):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))
        add_order(self.alice, self.sup_a, self.lettuce, "5", "6.00", il(2026, 9, 10))

        result = self.run_tool(self.alice, "query_spending")

        self.assertNotIn("total_quantity", result)

    def test_group_by_supplier_and_month(self):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 8, 20))
        add_order(self.alice, self.sup_b, self.tomato, "10", "6.00", il(2026, 9, 10))

        by_supplier = self.run_tool(self.alice, "query_spending", group_by="supplier")
        by_month = self.run_tool(self.alice, "query_spending", group_by="month")

        self.assertEqual([g["supplier"] for g in by_supplier["groups"]], ["ספק ב", "ספק א"])
        self.assertEqual([g["period"] for g in by_month["groups"]], ["2026-08", "2026-09"])

    def test_month_grouping_uses_israel_time(self):
        # 00:30 on 1 Sept Israel time is still 31 Aug in UTC.
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 1, 0, 30))

        by_month = self.run_tool(self.alice, "query_spending", group_by="month")

        self.assertEqual(by_month["groups"][0]["period"], "2026-09")

    def test_admin_group_by_customer(self):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))
        add_order(self.bob, self.sup_a, self.tomato, "100", "5.00", il(2026, 9, 10))

        result = self.run_tool(self.admin, "query_spending", group_by="customer")

        self.assertEqual(result["groups"][0]["customer"], "חברת בוב")
        self.assertEqual(result["groups"][0]["total_spend"], "500.00")

    def test_bad_dates_and_ranges_are_reported_not_raised(self):
        self.assertIn("error", self.run_tool(self.alice, "query_spending", date_from="10.7.2026"))
        self.assertIn("error", self.run_tool(
            self.alice, "query_spending", date_from="2026-09-10", date_to="2026-09-01"))
        self.assertIn("error", self.run_tool(
            self.alice, "query_spending", date_from="2020-01-01", date_to="2026-09-01"))

    def test_empty_result_reveals_when_the_account_has_orders(self):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 8, 20))
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 5))
        add_order(self.bob, self.sup_a, self.tomato, "10", "5.00", il(2020, 1, 1))

        wrong_year = self.run_tool(self.alice, "query_spending", date_from="2023-07-10", date_to="2023-09-10")
        has_data = self.run_tool(self.alice, "query_spending", date_from="2026-08-01", date_to="2026-08-31")

        self.assertEqual(wrong_year["data_range"], {"first_order": "2026-08-20", "last_order": "2026-09-05"})
        self.assertIn("note", wrong_year)
        self.assertNotIn("data_range", has_data)

    def test_no_spend_in_period_is_an_explicit_zero(self):
        result = self.run_tool(self.alice, "query_spending", date_from="2026-01-01", date_to="2026-01-31")

        self.assertEqual(result["total_spend"], "0.00")
        self.assertEqual(result["order_count"], 0)


class OrderToolTests(ToolTestBase):
    def test_list_orders_totals_come_from_lines_not_total_price(self):
        order = add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))
        self.assertEqual(order.total_price, 0)

        result = self.run_tool(self.alice, "list_orders")

        self.assertEqual(result["orders"][0]["total"], "50.00")
        self.assertEqual(result["orders"][0]["status_label"], "נשלח לספקים")

    def test_list_orders_only_returns_own_orders_and_filters_by_status(self):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 11), OrderRequest.Status.CANCELLED)
        add_order(self.bob, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 12))

        everything = self.run_tool(self.alice, "list_orders")
        cancelled = self.run_tool(self.alice, "list_orders", status="cancelled")

        self.assertEqual(everything["returned"], 2)
        self.assertEqual(cancelled["returned"], 1)
        self.assertIn("error", self.run_tool(self.alice, "list_orders", status="bogus"))

    def test_empty_order_list_reveals_when_the_account_has_orders(self):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 5))

        result = self.run_tool(self.alice, "list_orders", date_from="2023-01-01", date_to="2023-12-31")

        self.assertEqual(result["returned"], 0)
        self.assertEqual(result["data_range"], {"first_order": "2026-09-05", "last_order": "2026-09-05"})

    def test_get_order_is_scoped_to_the_owner(self):
        mine = add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))
        theirs = add_order(self.bob, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))

        own = self.run_tool(self.alice, "get_order", order_id=mine.id)
        other = self.run_tool(self.alice, "get_order", order_id=theirs.id)
        as_admin = self.run_tool(self.admin, "get_order", order_id=theirs.id)

        self.assertEqual(own["lines"][0]["product"], "עגבנייה")
        self.assertEqual(other, {"error": "order not found"})
        self.assertEqual(as_admin["customer"], "חברת בוב")

    def test_customer_names_are_hidden_from_customers(self):
        add_order(self.alice, self.sup_a, self.tomato, "10", "5.00", il(2026, 9, 10))

        orders = self.run_tool(self.alice, "list_orders")

        self.assertNotIn("customer", orders["orders"][0])


class CatalogToolTests(ToolTestBase):
    def setUp(self):
        super().setUp()
        self.north = make_supplier("ספק צפוני", region=Region.NORTH)
        self.blocked = make_supplier("ספק חסום", blocked_until=timezone.now() + dt.timedelta(days=5))
        for supplier, price in ((self.sup_a, "7.00"), (self.sup_b, "5.00"), (self.north, "1.00"), (self.blocked, "2.00")):
            SupplierProduct.objects.create(supplier=supplier, product=self.tomato, price_per_unit=Decimal(price))

    def test_customer_prices_are_regional_cheapest_first_and_skip_blocked(self):
        result = self.run_tool(self.alice, "get_current_prices", products=["עגבנייה"])

        offers = result["prices"][0]["offers"]
        self.assertEqual([o["supplier"] for o in offers], ["ספק ב", "ספק א"])
        self.assertNotIn("blocked", offers[0])

    def test_customer_cannot_pick_another_region(self):
        result = self.run_tool(self.alice, "get_current_prices", products=["עגבנייה"], region="north")

        self.assertNotIn("ספק צפוני", [o["supplier"] for o in result["prices"][0]["offers"]])

    def test_admin_sees_all_regions_and_blocked_flag(self):
        result = self.run_tool(self.admin, "get_current_prices", products=["עגבנייה"])

        offers = {o["supplier"]: o for o in result["prices"][0]["offers"]}
        self.assertEqual(len(offers), 4)
        self.assertTrue(offers["ספק חסום"]["blocked"])
        self.assertFalse(offers["ספק א"]["blocked"])

    def test_suppliers_hide_contact_details_and_blocked_from_customers(self):
        customer_view = self.run_tool(self.alice, "get_suppliers")
        admin_view = self.run_tool(self.admin, "get_suppliers")

        self.assertEqual(sorted(s["name"] for s in customer_view["suppliers"]), ["ספק א", "ספק ב"])
        self.assertNotIn("phone", customer_view["suppliers"][0])
        self.assertIn("phone", admin_view["suppliers"][0])

    def test_list_products_matches_alias(self):
        ProductAlias.objects.create(product=self.tomato, alias="עגבניות שרי")

        result = self.run_tool(self.alice, "list_products", search="שרי")

        self.assertEqual([p["name"] for p in result["products"]], ["עגבנייה"])

    def test_list_customers_is_admin_only(self):
        self.assertEqual(self.run_tool(self.alice, "list_customers"), {"error": "unknown tool list_customers"})
        companies = [c["company"] for c in self.run_tool(self.admin, "list_customers")["customers"]]
        self.assertIn("חברת בוב", companies)


class SchemaAndDispatchTests(ToolTestBase):
    def test_customer_schemas_hide_admin_tools_and_params(self):
        schemas = build_tool_schemas(is_admin=False)
        names = [s["function"]["name"] for s in schemas]
        spending = next(s for s in schemas if s["function"]["name"] == "query_spending")["function"]["parameters"]

        self.assertNotIn("list_customers", names)
        self.assertNotIn("customer", spending["properties"])
        self.assertNotIn("customer", spending["properties"]["group_by"]["enum"])

    def test_admin_schemas_keep_everything(self):
        schemas = build_tool_schemas(is_admin=True)
        names = [s["function"]["name"] for s in schemas]
        spending = next(s for s in schemas if s["function"]["name"] == "query_spending")["function"]["parameters"]

        self.assertIn("list_customers", names)
        self.assertIn("customer", spending["properties"]["group_by"]["enum"])

    def test_building_customer_schemas_does_not_mutate_the_admin_ones(self):
        build_tool_schemas(is_admin=False)
        spending = next(
            s for s in build_tool_schemas(is_admin=True) if s["function"]["name"] == "query_spending"
        )["function"]["parameters"]

        self.assertIn("customer", spending["properties"])

    def test_bad_calls_come_back_as_errors(self):
        ctx = build_context(self.alice)

        self.assertIn("error", execute_tool(ctx, "drop_database", "{}"))
        self.assertIn("error", execute_tool(ctx, "query_spending", "not json"))
        self.assertIn("error", execute_tool(ctx, "query_spending", "[1, 2]"))
        self.assertIn("error", execute_tool(ctx, "query_spending", '{"surprise": 1}'))
        self.assertIn("error", execute_tool(ctx, "get_order", '{"order_id": "abc"}'))

    def test_context_today_is_israel_date(self):
        # 22:30 UTC on 9 Sept is already 10 Sept in Israel (UTC+3).
        now = dt.datetime(2026, 9, 9, 22, 30, tzinfo=dt.timezone.utc)

        self.assertEqual(build_context(self.alice, now=now).today, dt.date(2026, 9, 10))
