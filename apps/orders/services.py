from decimal import Decimal
from collections import defaultdict
from urllib.parse import quote
from apps.catalog.models import SupplierProduct, Supplier, MarketPrice
from apps.orders.models import OrderRequest, OrderRequestProduct


def suggest_order(user, region, products):
    cheapest = _assign_suppliers(products, user, region)
    fewest = _assign_fewest_suppliers(products, user, region)
    return {
        "cheapest": _assignments_to_scenario(cheapest, "cheapest"),
        "fewest_suppliers": _assignments_to_scenario(fewest, "fewest_suppliers"),
        "market_comparison": {
            "products": [],
            "our_total": 0,
            "market_total": None,
            "total_savings": None,
        },
        "minimum_issues": {
            "cheapest": _check_missing_minimum(cheapest),
            "fewest_suppliers": _check_missing_minimum(fewest),
        },
    }


def _assign_suppliers(products, user, region):
    suppliers = _get_available_suppliers(user, region)
    assignments_list = _build_initial_assignments(products, suppliers)
    assignments = _force_minimum_switch(assignments_list)
    _validate_all_products_present(assignments, products)
    return assignments


def build_order(user, region, products, scenario="cheapest"):
    """
    Saves an order to DB and returns (OrderRequest, whatsapp_links).

    scenario: "cheapest" | "fewest_suppliers"
    products: list of {"product": Product, "quantity": Decimal}
    """
    if scenario == "fewest_suppliers":
        assignments = _assign_fewest_suppliers(products, user, region)
    else:
        assignments = _assign_suppliers(products, user, region)
    total = sum(a["quantity"] * a["unit_price"] for a in assignments)
    order = OrderRequest.objects.create(user=user, total_price=total)
    OrderRequestProduct.objects.bulk_create([
        OrderRequestProduct(
            order_request=order,
            product=a["product"],
            supplier=a["supplier"],
            quantity=a["quantity"],
            unit_price=a["unit_price"],
        )
        for a in assignments
    ])
    return order, generate_whatsapp_links(assignments)


def _build_initial_assignments(products, suppliers):
    assignments = []
    for product in products:
        prices = _prices_for_product(product["product"], suppliers)
        if not prices:
            raise ValueError(f"No supplier for {product['product'].name}")
        supplier, price = prices[0]
        assignments.append({
            "product": product["product"],
            "quantity": product["quantity"],
            "supplier": supplier,
            "unit_price": price,
            "all_prices": prices,
        })
    return assignments


def _validate_all_products_present(assignments, products):
    assigned_products = {a["product"].id for a in assignments}
    requested_products = {i["product"].id for i in products}
    if assigned_products != requested_products:
        raise ValueError("Some products are missing in assignment")


def _get_available_suppliers(user, region):
    return Supplier.objects.filter(
        region=region,
        owner__isnull=True,
    ) | Supplier.objects.filter(owner=user)


def _prices_for_product(product, suppliers):
    prices = (
        SupplierProduct.objects
        .filter(
            product=product,
            supplier__in=suppliers,
            price_per_unit__isnull=False,
        )
        .select_related("supplier")
        .order_by("price_per_unit")
    )
    return [(sp.supplier, sp.price_per_unit) for sp in prices]


def _assign_fewest_suppliers(products, user, region):
    """
    Greedy set cover: pick the fewest distinct suppliers that cover all products.
    Tie-break by lower total cost on the products covered. Then enforce minimum
    orders by handing off below-minimum supplier groups via _force_minimum_switch.
    """
    suppliers = _get_available_suppliers(user, region)

    product_options = {}
    for p in products:
        prices = _prices_for_product(p["product"], suppliers)
        if not prices:
            raise ValueError(f"No supplier for {p['product'].name}")
        product_options[p["product"].id] = prices

    supplier_coverage = defaultdict(set)
    for pid, prices in product_options.items():
        for s, _ in prices:
            supplier_coverage[s.id].add(pid)

    uncovered = {p["product"].id for p in products}
    quantities = {p["product"].id: p["quantity"] for p in products}
    chosen_suppliers = set()

    while uncovered:
        best_sid = None
        best_score = None
        for sid, covers in supplier_coverage.items():
            new_covered = covers & uncovered
            if not new_covered:
                continue
            cost = sum(
                quantities[pid] * _price_from(product_options[pid], sid)
                for pid in new_covered
            )
            score = (len(new_covered), -cost)
            if best_score is None or score > best_score:
                best_sid = sid
                best_score = score
        if best_sid is None:
            raise ValueError("Cannot cover all products")
        chosen_suppliers.add(best_sid)
        uncovered -= supplier_coverage[best_sid]

    assignments = []
    for p in products:
        pid = p["product"].id
        candidates = [
            (s, price) for s, price in product_options[pid]
            if s.id in chosen_suppliers
        ]
        supplier, price = min(candidates, key=lambda x: x[1])
        assignments.append({
            "product": p["product"],
            "quantity": p["quantity"],
            "supplier": supplier,
            "unit_price": price,
            "all_prices": product_options[pid],
        })

    return _force_minimum_switch(assignments)


def _price_from(prices_list, supplier_id):
    return next(p for s, p in prices_list if s.id == supplier_id)


def generate_whatsapp_links(assignments):
    by_supplier = defaultdict(list)
    supplier_obj = {}
    for a in assignments:
        by_supplier[a["supplier"].id].append(a)
        supplier_obj[a["supplier"].id] = a["supplier"]
    links = {}
    for sid, products in by_supplier.items():
        supplier = supplier_obj[sid]
        lines = ["שלום, ברצוני להזמין:"]
        for product in products:
            lines.append(
                f"- {product['product'].name} x{product['quantity']} {product['product'].get_unit_display()}"
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


def _assignments_to_scenario(assignments, scenario_name):
    total = sum(a["quantity"] * a["unit_price"] for a in assignments)
    supplier_ids = {a["supplier"].id for a in assignments}
    return {
        "scenario": scenario_name,
        "total_price": total,
        "supplier_count": len(supplier_ids),
        "products": [
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


def _check_missing_minimum(assignments):
    supplier_totals = defaultdict(Decimal)
    supplier_obj = {}

    for a in assignments:
        supplier_totals[a["supplier"].id] += a["quantity"] * a["unit_price"]
        supplier_obj[a["supplier"].id] = a["supplier"]

    problems = []

    for sid, total in supplier_totals.items():
        supplier = supplier_obj[sid]

        if total < supplier.minimum_order:
            missing = supplier.minimum_order - total

            problems.append({
                "supplier_id": sid,
                "supplier_name": supplier.name,
                "current_total": total,
                "minimum_required": supplier.minimum_order,
                "missing_amount": missing,
            })

    return problems


def _force_minimum_switch(assignments):
    totals, suppliers = _calculate_supplier_totals(assignments)
    grouped = _group_by_supplier(assignments)

    for sid, items in grouped.items():
        supplier = suppliers[sid]
        total = totals[sid]

        if total >= supplier.minimum_order:
            continue

        best_supplier = _find_next_valid_supplier(items, sid, assignments)

        if best_supplier:
            _move_items_to_supplier(items, best_supplier)

    return assignments


def _calculate_supplier_totals(assignments):
    totals = defaultdict(Decimal)
    suppliers = {}

    for a in assignments:
        sid = a["supplier"].id
        totals[sid] += a["quantity"] * a["unit_price"]
        suppliers[sid] = a["supplier"]

    return totals, suppliers


def _group_by_supplier(assignments):
    grouped = defaultdict(list)

    for a in assignments:
        grouped[a["supplier"].id].append(a)

    return grouped


def _calculate_total_for_supplier(items, supplier_id):
    total = Decimal(0)

    for a in items:
        price = next((p for s, p in a["all_prices"] if s.id == supplier_id), None)
        if price is None:
            return None

        total += a["quantity"] * price

    return total


def _find_next_valid_supplier(items, current_supplier_id, all_assignments):
    """
    Find the cheapest alternative supplier that can carry ALL items in the group
    AND, after absorbing the group on top of whatever it already has in
    `all_assignments`, meets its own minimum order.

    Picks by lowest cost-of-this-group (cheapest move), then checks the
    post-move total (group cost + supplier's existing assignments) against the
    candidate's minimum.
    """
    candidates = {}
    for item in items:
        for s, _ in item["all_prices"]:
            if s.id != current_supplier_id:
                candidates[s.id] = s

    scored = []
    for s in candidates.values():
        group_total = _calculate_total_for_supplier(items, s.id)
        if group_total is None:
            continue
        # items in `all_assignments` belong to `current_supplier_id`; anything
        # already at `s.id` is genuinely "existing" for the candidate.
        existing_total = sum(
            (a["quantity"] * a["unit_price"] for a in all_assignments
             if a["supplier"].id == s.id),
            Decimal(0),
        )
        scored.append((group_total, existing_total, s))

    scored.sort(key=lambda x: x[0])

    for group_total, existing_total, supplier in scored:
        if group_total + existing_total >= supplier.minimum_order:
            return supplier

    return None


def _move_items_to_supplier(items, new_supplier):
    for a in items:
        for s, p in a["all_prices"]:
            if s.id == new_supplier.id:
                a["supplier"] = new_supplier
                a["unit_price"] = p
                break
