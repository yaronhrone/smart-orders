"""
Runs the REAL smart-orders pricing engine (apps.orders.services.suggest_order)
against temporary data, instead of reimplementing the assignment logic — so
the "smart" cost below is exactly what the live app would have charged,
including real minimum-order-per-supplier enforcement (_force_minimum_switch).

Safety: bootstraps Django against whatever database DJANGO_SETTINGS_MODULE
points to (the dev DB when run inside the app container), creates temporary
Supplier/SupplierProduct/User rows inside ONE transaction, and always rolls
that transaction back at the end — nothing is left in the database, win or
crash. Existing catalog Products are reused (get_or_create) rather than
duplicated. Not part of the Django app: never imported by it, and excluded
from the Docker build via .dockerignore.

Usage (from inside the app container, where DB env vars are already set):
  docker-compose exec app python simulations/annual_savings/simulate_real_engine.py
"""
import os
import random
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402
django.setup()

import json  # noqa: E402
from django.contrib.auth import get_user_model  # noqa: E402
from django.db import transaction  # noqa: E402

from apps.catalog.models import Product, Region, Supplier, SupplierProduct, Unit  # noqa: E402
from apps.orders.services import suggest_order  # noqa: E402

random.seed(42)

OUTPUT_PATH = Path(__file__).parent / "results_real_engine.json"
REGION = Region.CENTER

SUPPLIERS = [
    # name, phone-suffix, minimum_order (₪) — realistic wholesale minimums
    ("סים - שוק ירקות המרכז", "0001", Decimal("600")),
    ("סים - ירקות השרון",     "0002", Decimal("450")),
    ("סים - פירות וירקות ת״א", "0003", Decimal("800")),
    ("סים - אספקת ירקות דן",   "0004", Decimal("350")),
]

# (product name, unit, market base price, sunday_qty_range, topup_qty_range)
# Institutional-scale quantities (hospital/industrial catering kitchens),
# roughly 5-8x a single restaurant's basket.
PRODUCTS = [
    ("עגבנייה",       "kg", 5.50, (60, 100), (10, 30)),
    ("מלפפון",        "kg", 4.20, (50, 90),  (10, 25)),
    ("בצל יבש",       "kg", 3.80, (40, 70),  (8, 20)),
    ("גזר",           "kg", 3.50, (35, 60),  (6, 18)),
    ("תפוח אדמה לבן", "kg", 3.20, (80, 130), (15, 40)),
    ("פלפל אדום",     "kg", 8.90, (25, 45),  (5, 15)),
    ("חסה אייסברג",   "unit", 4.50, (50, 80), (10, 25)),
    ("פטרוזיליה",     "bundle", 2.50, (30, 50), (5, 15)),
    ("לימון",         "kg", 6.50, (20, 35),  (4, 12)),
    ("תפוז",          "kg", 4.80, (25, 45),  (5, 15)),
    ("חציל",          "kg", 5.20, (20, 40),  (4, 12)),
    ("קישוא",         "kg", 4.60, (25, 45),  (5, 15)),
]

WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]  # closed Fri/Sat
WEEKS_PER_YEAR = 52
EVENT_DAY_CHANCE = 1 / 20  # ~1 "big event" day per month, institutional catering spikes

KITCHENS = [
    {"name": "מטבח מרכזי - בית חולים", "regular_supplier": "סים - שוק ירקות המרכז", "scale": 1.0},
    {"name": "מטבח תעשייתי - קייטרינג גדול", "regular_supplier": "סים - ירקות השרון", "scale": 1.7},
]


class _RollbackSimulation(Exception):
    """Raised to force the outer transaction to roll back, never to commit."""


def setup_data():
    """Creates (inside the caller's open transaction) temp suppliers/prices and
    reuses/creates catalog products. Returns (products_by_name, supplier_prices, user)."""
    User = get_user_model()
    user = User.objects.create_user(email="sim-user@example.invalid", password="sim-only")

    products_by_name = {}
    for name, unit, base, _, _ in PRODUCTS:
        product, _ = Product.objects.get_or_create(name=name, defaults={"unit": unit})
        products_by_name[name] = product

    suppliers = []
    for name, phone_suffix, minimum in SUPPLIERS:
        supplier = Supplier.objects.create(
            name=name,
            phone="+97250000" + phone_suffix,
            whatsapp_number="+97250000" + phone_suffix,
            region=REGION,
            minimum_order=minimum,
        )
        suppliers.append(supplier)

    supplier_prices = {}  # {product_name: {supplier_name: price}}
    for name, unit, base, _, _ in PRODUCTS:
        supplier_prices[name] = {}
        for supplier in suppliers:
            price = round(Decimal(base) * Decimal(random.uniform(0.80, 1.25)), 2)
            SupplierProduct.objects.create(
                supplier=supplier, product=products_by_name[name], price_per_unit=price,
            )
            supplier_prices[name][supplier.name] = price

    return products_by_name, supplier_prices, user


def simulate_day(kitchen, is_sunday, is_event_day, products_by_name):
    """Returns the day's basket as [{"product": Product, "quantity": Decimal}]."""
    if is_sunday:
        chosen = PRODUCTS
    else:
        k = random.randint(3, 8)
        chosen = random.sample(PRODUCTS, k)

    spike = 1.7 if is_event_day else 1.0
    basket = []
    for name, unit, base, sun_range, topup_range in chosen:
        qty_range = sun_range if is_sunday else topup_range
        qty = round(random.uniform(*qty_range) * kitchen["scale"] * spike, 1)
        basket.append({"product": products_by_name[name], "quantity": Decimal(str(qty))})
    return basket


def run():
    products_by_name, supplier_prices, user = setup_data()

    all_results = {"kitchens": [], "combined": {}}
    weekly_totals = {}  # week -> {"baseline": x, "smart": x}
    minimum_issue_days = 0
    total_days = 0
    scenario_counts = {"cheapest": 0, "fewest_suppliers": 0}

    for kitchen in KITCHENS:
        daily_rows = []
        for week in range(WEEKS_PER_YEAR):
            for day_idx, day_name in enumerate(WEEKDAYS):
                is_sunday = day_idx == 0
                is_event_day = (not is_sunday) and random.random() < EVENT_DAY_CHANCE
                basket = simulate_day(kitchen, is_sunday, is_event_day, products_by_name)

                # Baseline: everything priced at the kitchen's one regular supplier.
                baseline_cost = sum(
                    item["quantity"] * supplier_prices[item["product"].name][kitchen["regular_supplier"]]
                    for item in basket
                )

                # Smart: the real engine computes both scenarios with real
                # minimum-order-per-supplier enforcement already applied. A
                # real customer (via the WhatsApp flow) sees both and picks
                # whichever actually clears every supplier's minimum — so we
                # do the same: prefer cheapest, fall back to fewest-suppliers
                # (which consolidates and clears minimums more easily on
                # small baskets), and only count a day as a genuine
                # "unresolved" case if NEITHER scenario clears.
                result = suggest_order(user=user, region=REGION, products=basket)
                cheapest_ok = not result["minimum_issues"]["cheapest"]
                fewest_ok = not result["minimum_issues"]["fewest_suppliers"]

                if cheapest_ok:
                    scenario_used = "cheapest"
                elif fewest_ok:
                    scenario_used = "fewest_suppliers"
                else:
                    scenario_used = "cheapest"  # best-effort fallback, still flagged below

                smart_cost = result[scenario_used]["total_price"]
                unresolved = not (cheapest_ok or fewest_ok)

                total_days += 1
                scenario_counts[scenario_used] += 1
                if unresolved:
                    minimum_issue_days += 1

                daily_rows.append({
                    "week": week, "day": day_name, "event_day": is_event_day,
                    "baseline": float(baseline_cost), "smart": float(smart_cost),
                    "scenario_used": scenario_used,
                })
                w = weekly_totals.setdefault(week, {"baseline": 0.0, "smart": 0.0})
                w["baseline"] += float(baseline_cost)
                w["smart"] += float(smart_cost)

        total_baseline = sum(r["baseline"] for r in daily_rows)
        total_smart = sum(r["smart"] for r in daily_rows)
        savings = total_baseline - total_smart

        all_results["kitchens"].append({
            "name": kitchen["name"],
            "regular_supplier": kitchen["regular_supplier"],
            "order_days": len(daily_rows),
            "total_baseline": round(total_baseline, 2),
            "total_smart": round(total_smart, 2),
            "total_savings": round(savings, 2),
            "savings_pct": round(100 * savings / total_baseline, 1),
            "avg_savings_per_order_day": round(savings / len(daily_rows), 2),
        })

    cumulative = []
    running = 0.0
    for week in range(WEEKS_PER_YEAR):
        week_savings = weekly_totals[week]["baseline"] - weekly_totals[week]["smart"]
        running += week_savings
        cumulative.append({"week": week + 1, "week_savings": round(week_savings, 2),
                            "cumulative_savings": round(running, 2)})
    all_results["cumulative_weekly"] = cumulative

    grand_baseline = sum(k["total_baseline"] for k in all_results["kitchens"])
    grand_smart = sum(k["total_smart"] for k in all_results["kitchens"])
    all_results["combined"] = {
        "total_baseline": round(grand_baseline, 2),
        "total_smart": round(grand_smart, 2),
        "total_savings": round(grand_baseline - grand_smart, 2),
        "savings_pct": round(100 * (grand_baseline - grand_smart) / grand_baseline, 1),
    }
    all_results["minimum_order_stats"] = {
        "total_order_days": total_days,
        "days_with_unresolved_minimum_issue": minimum_issue_days,
        "pct_fully_resolved_automatically": round(100 * (1 - minimum_issue_days / total_days), 1),
        "scenario_used_counts": scenario_counts,
    }
    return all_results


def print_report(results):
    print("=" * 68)
    print("סימולציית חיסכון שנתי — מנוע ההזמנות האמיתי (apps.orders.services)")
    print("=" * 68)
    for k in results["kitchens"]:
        print(f"\n{k['name']}")
        print(f"  ספק קבוע (baseline): {k['regular_supplier']}")
        print(f"  ימי הזמנה בשנה: {k['order_days']}")
        print(f"  עלות ללא המערכת: {k['total_baseline']:,.0f} ₪")
        print(f"  עלות עם המערכת (כולל אכיפת מינימום אמיתית): {k['total_smart']:,.0f} ₪")
        print(f"  חיסכון: {k['total_savings']:,.0f} ₪ ({k['savings_pct']}%)")
        print(f"  חיסכון ממוצע ליום הזמנה: {k['avg_savings_per_order_day']:.1f} ₪")

    c = results["combined"]
    m = results["minimum_order_stats"]
    print("\n" + "-" * 68)
    print(f"סה\"כ שני המטבחים יחד: {c['total_savings']:,.0f} ₪ חיסכון בשנה ({c['savings_pct']}%)")
    print(f"  (מתוך {c['total_baseline']:,.0f} ₪ הוצאה שנתית ללא המערכת)")
    print(f"מינימום הזמנה: מתוך {m['total_order_days']} ימי הזמנה, המערכת פתרה אוטומטית "
          f"{m['pct_fully_resolved_automatically']}% מהם ללא בעיית מינימום שנשארה פתוחה")
    print(f"  (על ידי בחירה בין שתי האפשרויות שהיא כבר מציגה ללקוח: הכי זול / הכי פחות ספקים —")
    sc = m["scenario_used_counts"]
    print(f"   'הכי זול' נבחר ב-{sc['cheapest']} ימים, 'הכי פחות ספקים' נבחר ב-{sc['fewest_suppliers']} ימים)")
    print("-" * 68)


if __name__ == "__main__":
    try:
        with transaction.atomic():
            results = run()
            print_report(results)
            OUTPUT_PATH.write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\nנשמר: {OUTPUT_PATH}")
            print("(כל הנתונים הזמניים בדאטהבייס יבוטלו עכשיו — rollback)")
            raise _RollbackSimulation()
    except _RollbackSimulation:
        pass
