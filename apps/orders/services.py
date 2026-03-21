from decimal import Decimal
from collections import defaultdict
from urllib.parse import quote

from apps.catalog.models import SupplierProduct, Supplier, MarketPrice
from apps.orders.models import OrderRequest, OrderRequestItem

SPLIT_THRESHOLD = Decimal("0.10")  # split only if price difference > 10%


def _enforce_minimum_orders(assignments):
    """
    For each supplier whose assigned total is below their minimum_order,
    moves ALL their items together to the cheapest viable alternative supplier.

    "Viable" means the alt supplier:
      1. Carries every product in the group.
      2. Would meet its own minimum_order after receiving all those items.

    If no viable alt exists the original assignment is kept (best-effort).
    Items are always moved as a group so that the alt's minimum can be reached
    even when no single item alone would cover it.
    """
    supplier_totals = defaultdict(Decimal)
    supplier_obj = {}
    for a in assignments:
        supplier_totals[a["supplier"].id] += a["quantity"] * a["unit_price"]
        supplier_obj[a["supplier"].id] = a["supplier"]

    # Group assignments by supplier
    by_supplier = defaultdict(list)
    for a in assignments:
        by_supplier[a["supplier"].id].append(a)

    for sid in list(by_supplier.keys()):
        supplier = supplier_obj[sid]
        if supplier_totals[sid] >= supplier.minimum_order:
            continue

        items = by_supplier[sid]
        if not items:
            continue

        # Find alt suppliers that carry EVERY product in this group
        candidate_ids = None
        alt_supplier_map = {}
        for item in items:
            item_alt_ids = set()
            for s, _ in item["all_prices"]:
                if s.id != sid:
                    item_alt_ids.add(s.id)
                    alt_supplier_map[s.id] = s
            candidate_ids = item_alt_ids if candidate_ids is None else candidate_ids & item_alt_ids

        if not candidate_ids:
            continue  # No single supplier covers all products in this group

        # Pick the cheapest alt whose total (existing + group) meets its minimum
        best_alt_id = None
        best_cost = None
        for alt_id in candidate_ids:
            price_map = {s.id: p for item in items for s, p in item["all_prices"]}
            cost = sum(item["quantity"] * next(p for s, p in item["all_prices"] if s.id == alt_id)
                       for item in items)
            new_total = supplier_totals[alt_id] + cost
            if new_total >= alt_supplier_map[alt_id].minimum_order:
                if best_cost is None or cost < best_cost:
                    best_alt_id = alt_id
                    best_cost = cost

        if best_alt_id is None:
            continue  # No alt meets its own minimum even after receiving all items

        alt_supplier = alt_supplier_map[best_alt_id]
        for item in items:
            alt_price = next(p for s, p in item["all_prices"] if s.id == best_alt_id)
            supplier_totals[sid] -= item["quantity"] * item["unit_price"]
            supplier_totals[best_alt_id] += item["quantity"] * alt_price
            item["supplier"] = alt_supplier
            item["unit_price"] = alt_price

    return assignments


def _get_available_suppliers(user, region):
    """Returns all suppliers available to a user: global ones in their region + their private ones."""
    return Supplier.objects.filter(
        region=region,
        owner__isnull=True,
    ) | Supplier.objects.filter(owner=user)


def _prices_for_product(product, suppliers):
    """Returns list of (supplier, price) sorted cheapest first for a given product."""
    prices = (
        SupplierProduct.objects.filter(product=product, supplier__in=suppliers)
        .select_related("supplier")
        .order_by("price_per_unit")
    )
    return [(sp.supplier, sp.price_per_unit) for sp in prices]


def _assign_suppliers(items, user, region):
    """
    Cheapest scenario algorithm.
    items: list of {"product": Product, "quantity": Decimal}
    Returns: list of {"product", "quantity", "supplier", "unit_price", "all_prices"}
    """
    suppliers = _get_available_suppliers(user, region)

    assignments = []
    for item in items:
        prices = _prices_for_product(item["product"], suppliers)
        if not prices:
            raise ValueError(f"אין ספק זמין למוצר: {item['product'].name}")

        cheapest_supplier, cheapest_price = prices[0]

        if len(prices) >= 2:
            second_price = prices[1][1]
            saving_pct = (second_price - cheapest_price) / second_price
            if saving_pct <= SPLIT_THRESHOLD:
                cheapest_supplier, cheapest_price = prices[0]

        assignments.append({
            "product": item["product"],
            "quantity": item["quantity"],
            "supplier": cheapest_supplier,
            "unit_price": cheapest_price,
            "all_prices": prices,
        })

    return _enforce_minimum_orders(assignments)


def _assign_fewest_suppliers(items, user, region):
    """
    Fewest suppliers scenario — greedy set-cover.

    Each iteration picks the supplier that covers the most uncovered products.
    Ties are broken by lowest total cost for the covered products.

    items: list of {"product": Product, "quantity": Decimal}
    Returns: list of {"product", "quantity", "supplier", "unit_price", "all_prices"}
    """
    suppliers = list(_get_available_suppliers(user, region))

    # product_id → {prices, product, quantity, price_map}
    product_data = {}
    for item in items:
        prices = _prices_for_product(item["product"], suppliers)
        if not prices:
            raise ValueError(f"אין ספק זמין למוצר: {item['product'].name}")
        product_data[item["product"].id] = {
            "product": item["product"],
            "quantity": item["quantity"],
            "prices": prices,
            "price_map": {s.id: p for s, p in prices},
        }

    # supplier_id → set of product_ids that supplier can cover
    supplier_coverage = defaultdict(set)
    supplier_obj = {}
    for pid, data in product_data.items():
        for supplier, _ in data["prices"]:
            supplier_coverage[supplier.id].add(pid)
            supplier_obj[supplier.id] = supplier

    uncovered = set(product_data.keys())
    chosen = {}  # product_id → chosen supplier_id

    while uncovered:
        best_sid = None
        best_covered = set()
        best_cost = Decimal("Infinity")

        for sid, covered_pids in supplier_coverage.items():
            covered = covered_pids & uncovered
            if not covered:
                continue
            cost = sum(
                product_data[pid]["quantity"] * product_data[pid]["price_map"][sid]
                for pid in covered
                if sid in product_data[pid]["price_map"]
            )
            if len(covered) > len(best_covered) or (
                len(covered) == len(best_covered) and cost < best_cost
            ):
                best_sid = sid
                best_covered = covered
                best_cost = cost

        if best_sid is None:
            raise ValueError("Cannot cover all products with available suppliers")

        for pid in best_covered:
            chosen[pid] = best_sid
        uncovered -= best_covered

    # build assignments from chosen supplier per product
    assignments = []
    for pid, sid in chosen.items():
        data = product_data[pid]
        unit_price = data["price_map"][sid]
        assignments.append({
            "product": data["product"],
            "quantity": data["quantity"],
            "supplier": supplier_obj[sid],
            "unit_price": unit_price,
            "all_prices": data["prices"],
        })

    return _enforce_minimum_orders(assignments)


def _assignments_to_scenario(assignments, scenario_name):
    """Converts a raw assignments list into a structured dict for the serializer."""
    total = sum(a["quantity"] * a["unit_price"] for a in assignments)
    supplier_ids = {a["supplier"].id for a in assignments}

    return {
        "scenario": scenario_name,
        "total_price": total,
        "supplier_count": len(supplier_ids),
        "items": [
            {
                "product_id": a["product"].id,
                "product_name": a["product"].name,
                "quantity": a["quantity"],
                "unit_price": a["unit_price"],
                "subtotal": a["quantity"] * a["unit_price"],
                "supplier_id": a["supplier"].id,
                "supplier_name": a["supplier"].name,
            }
            for a in assignments
        ],
    }


def _market_comparison(assignments):
    """
    Compares order prices against Agricultural Authority reference prices.

    savings: positive = our price is cheaper than market; negative = market is cheaper.
    """
    product_ids = [a["product"].id for a in assignments]
    market_prices = {
        mp.product_id: mp.price_per_unit
        for mp in MarketPrice.objects.filter(product_id__in=product_ids)
    }

    items = []
    our_total = Decimal("0")
    market_total = Decimal("0")
    has_all = True

    for a in assignments:
        our_sub = a["unit_price"] * a["quantity"]
        our_total += our_sub

        market_unit = market_prices.get(a["product"].id)
        if market_unit is not None:
            market_sub = market_unit * a["quantity"]
            market_total += market_sub
            savings = market_sub - our_sub
        else:
            market_sub = None
            savings = None
            has_all = False

        items.append({
            "product_id": a["product"].id,
            "product_name": a["product"].name,
            "quantity": a["quantity"],
            "our_unit_price": a["unit_price"],
            "market_unit_price": market_unit,
            "our_subtotal": our_sub,
            "market_subtotal": market_sub,
            "savings": savings,
        })

    return {
        "items": items,
        "our_total": our_total,
        "market_total": market_total if has_all else None,
        "total_savings": (market_total - our_total) if has_all else None,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def suggest_order(user, region, items):
    """
    Returns two scenarios and a market comparison. Does NOT write to DB.

    items: list of {"product": Product, "quantity": Decimal}
    """
    cheapest = _assign_suppliers(items, user, region)
    fewest = _assign_fewest_suppliers(items, user, region)

    return {
        "cheapest": _assignments_to_scenario(cheapest, "cheapest"),
        "fewest_suppliers": _assignments_to_scenario(fewest, "fewest_suppliers"),
        "market_comparison": _market_comparison(cheapest),
    }


def generate_whatsapp_links(assignments):
    """
    Generates a WhatsApp deep link per supplier from an assignments list.

    Returns: dict of {supplier_id: {"supplier_id", "supplier_name", "phone", "whatsapp_url"}}
    """
    by_supplier = defaultdict(list)
    supplier_obj = {}
    for a in assignments:
        by_supplier[a["supplier"].id].append(a)
        supplier_obj[a["supplier"].id] = a["supplier"]

    links = {}
    for sid, sup_items in by_supplier.items():
        supplier = supplier_obj[sid]

        lines = ["שלום, ברצוני להזמין:"]
        for item in sup_items:
            lines.append(
                f"- {item['product'].name} x{item['quantity']} {item['product'].get_unit_display()}"
            )
        lines.append("תודה!")

        clean_phone = "".join(filter(str.isdigit, supplier.whatsapp_number))
        url = f"https://wa.me/{clean_phone}?text={quote(chr(10).join(lines))}"

        links[sid] = {
            "supplier_id": sid,
            "supplier_name": supplier.name,
            "phone": supplier.whatsapp_number,
            "whatsapp_url": url,
        }

    return links


def build_order(user, region, items, scenario="cheapest"):
    """
    Saves an order to DB and returns (OrderRequest, whatsapp_links).

    scenario: "cheapest" | "fewest_suppliers"
    items: list of {"product": Product, "quantity": Decimal}
    """
    if scenario == "fewest_suppliers":
        assignments = _assign_fewest_suppliers(items, user, region)
    else:
        assignments = _assign_suppliers(items, user, region)

    total = sum(a["quantity"] * a["unit_price"] for a in assignments)
    order = OrderRequest.objects.create(user=user, total_price=total)

    OrderRequestItem.objects.bulk_create([
        OrderRequestItem(
            order_request=order,
            product=a["product"],
            supplier=a["supplier"],
            quantity=a["quantity"],
            unit_price=a["unit_price"],
        )
        for a in assignments
    ])

    return order, generate_whatsapp_links(assignments)
