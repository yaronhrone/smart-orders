"""
Standalone ROI simulation — NOT part of the Django app, has no dependency on it
and is never imported by it. Excluded from the Docker build via .dockerignore.

Question this answers: if a restaurant kitchen always ordered from one regular
supplier (today's reality for most small kitchens) instead of automatically
picking the cheapest supplier per product (what smart-orders does), how much
does that cost them over a year?

Simulates two fictional kitchens ordering from four fictional suppliers, every
day Sun-Thu (no Fri/Sat) for 52 weeks: one big stock-up order on Sunday, then
smaller top-up orders Mon-Thu for a random subset of products. For each day we
compute the same basket's cost two ways:
  - baseline: every item priced at the kitchen's single regular supplier
  - smart:    every item priced at whichever supplier is cheapest for it
(Minimum-order-per-supplier enforcement, which the real app also applies and
which would trim some savings, is intentionally left out here to keep this
script self-contained and its logic easy to audit line by line.)

Usage: python simulate.py
Writes results.json next to this file and prints a text report to stdout.
"""
import json
import random
import statistics
from pathlib import Path

random.seed(42)  # reproducible: re-running gives identical numbers

OUTPUT_PATH = Path(__file__).parent / "results.json"

WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday"]  # kitchens are closed Fri/Sat
WEEKS_PER_YEAR = 52

SUPPLIERS = ["שוק ירקות המרכז", "ירקות השרון", "פירות וירקות תל אביב", "אספקת ירקות דן"]

# (product name, unit, market base price, sunday_qty_range, topup_qty_range)
PRODUCTS = [
    ("עגבנייה",       "kg", 5.50, (12, 20), (2, 6)),
    ("מלפפון",        "kg", 4.20, (10, 18), (2, 5)),
    ("בצל יבש",       "kg", 3.80, (8, 14),  (1, 4)),
    ("גזר",           "kg", 3.50, (6, 12),  (1, 3)),
    ("תפוח אדמה לבן", "kg", 3.20, (15, 25), (3, 8)),
    ("פלפל אדום",     "kg", 8.90, (5, 10),  (1, 3)),
    ("חסה אייסברג",   "unit", 4.50, (10, 16), (2, 5)),
    ("פטרוזיליה",     "bundle", 2.50, (6, 10), (1, 3)),
    ("לימון",         "kg", 6.50, (4, 8),   (1, 2)),
    ("תפוז",          "kg", 4.80, (5, 9),   (1, 3)),
    ("חציל",          "kg", 5.20, (4, 8),   (1, 3)),
    ("קישוא",         "kg", 4.60, (5, 9),   (1, 3)),
]

KITCHENS = [
    {"name": "מסעדת הגינה (מטבח בינוני)", "regular_supplier": "שוק ירקות המרכז", "scale": 1.0},
    {"name": "ביסטרו טעם העיר (מטבח גדול)", "regular_supplier": "ירקות השרון", "scale": 1.6},
]


def build_supplier_prices():
    """{product_name: {supplier: price}} — each supplier priced at a random
    -20%..+25% offset from the product's market base price."""
    prices = {}
    for name, unit, base, _, _ in PRODUCTS:
        prices[name] = {
            supplier: round(base * random.uniform(0.80, 1.25), 2)
            for supplier in SUPPLIERS
        }
    return prices


def cheapest_supplier_price(product_name, prices):
    return min(prices[product_name].values())


def simulate_day(kitchen, is_sunday, prices):
    """Returns (baseline_cost, smart_cost, basket) for one order day."""
    basket = []
    if is_sunday:
        chosen = PRODUCTS
    else:
        # Mon-Thu: a random subset — running low on some things, not others.
        k = random.randint(3, 7)
        chosen = random.sample(PRODUCTS, k)

    for name, unit, base, sun_range, topup_range in chosen:
        qty_range = sun_range if is_sunday else topup_range
        qty = round(random.uniform(*qty_range) * kitchen["scale"], 1)
        basket.append((name, qty))

    baseline_cost = sum(qty * prices[name][kitchen["regular_supplier"]] for name, qty in basket)
    smart_cost = sum(qty * cheapest_supplier_price(name, prices) for name, qty in basket)
    return baseline_cost, smart_cost


def run():
    prices = build_supplier_prices()
    results = {"kitchens": [], "monthly_totals": None}

    # 52 weeks * 5 weekdays, grouped into 12 "months" of ~4.33 weeks for reporting
    all_kitchens_daily = []  # list of (week_idx, weekday_idx, baseline, smart) across all kitchens combined

    for kitchen in KITCHENS:
        daily_rows = []
        for week in range(WEEKS_PER_YEAR):
            for day_idx, day_name in enumerate(WEEKDAYS):
                is_sunday = day_idx == 0
                baseline, smart = simulate_day(kitchen, is_sunday, prices)
                daily_rows.append({
                    "week": week, "day": day_name,
                    "baseline": round(baseline, 2), "smart": round(smart, 2),
                })
                all_kitchens_daily.append((week, baseline, smart))

        total_baseline = sum(r["baseline"] for r in daily_rows)
        total_smart = sum(r["smart"] for r in daily_rows)
        savings = total_baseline - total_smart

        results["kitchens"].append({
            "name": kitchen["name"],
            "regular_supplier": kitchen["regular_supplier"],
            "order_days": len(daily_rows),
            "total_baseline": round(total_baseline, 2),
            "total_smart": round(total_smart, 2),
            "total_savings": round(savings, 2),
            "savings_pct": round(100 * savings / total_baseline, 1),
            "avg_savings_per_order_day": round(savings / len(daily_rows), 2),
            "daily": daily_rows,
        })

    # Combined weekly totals across both kitchens, for the cumulative chart
    weekly = {}
    for week, baseline, smart in all_kitchens_daily:
        w = weekly.setdefault(week, {"baseline": 0.0, "smart": 0.0})
        w["baseline"] += baseline
        w["smart"] += smart

    cumulative = []
    running_savings = 0.0
    for week in range(WEEKS_PER_YEAR):
        week_savings = weekly[week]["baseline"] - weekly[week]["smart"]
        running_savings += week_savings
        cumulative.append({
            "week": week + 1,
            "week_savings": round(week_savings, 2),
            "cumulative_savings": round(running_savings, 2),
        })
    results["cumulative_weekly"] = cumulative
    results["supplier_prices_sample"] = prices

    grand_baseline = sum(k["total_baseline"] for k in results["kitchens"])
    grand_smart = sum(k["total_smart"] for k in results["kitchens"])
    results["combined"] = {
        "total_baseline": round(grand_baseline, 2),
        "total_smart": round(grand_smart, 2),
        "total_savings": round(grand_baseline - grand_smart, 2),
        "savings_pct": round(100 * (grand_baseline - grand_smart) / grand_baseline, 1),
    }

    return results


def print_report(results):
    print("=" * 64)
    print("סימולציית חיסכון שנתי — smart-orders (נתונים פיקטיביים)")
    print("=" * 64)
    for k in results["kitchens"]:
        print(f"\n{k['name']}")
        print(f"  ספק קבוע (baseline): {k['regular_supplier']}")
        print(f"  ימי הזמנה בשנה: {k['order_days']}")
        print(f"  עלות ללא המערכת: {k['total_baseline']:,.0f} ₪")
        print(f"  עלות עם המערכת:  {k['total_smart']:,.0f} ₪")
        print(f"  חיסכון: {k['total_savings']:,.0f} ₪ ({k['savings_pct']}%)")
        print(f"  חיסכון ממוצע ליום הזמנה: {k['avg_savings_per_order_day']:.1f} ₪")

    c = results["combined"]
    print("\n" + "-" * 64)
    print(f"סה\"כ שני המטבחים יחד: {c['total_savings']:,.0f} ₪ חיסכון בשנה ({c['savings_pct']}%)")
    print(f"  (מתוך {c['total_baseline']:,.0f} ₪ הוצאה שנתית ללא המערכת)")
    print("-" * 64)


if __name__ == "__main__":
    results = run()
    print_report(results)
    OUTPUT_PATH.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nנשמר: {OUTPUT_PATH}")
