from decimal import Decimal
from collections import defaultdict
from urllib.parse import quote
from apps.catalog.models import SupplierProduct, Supplier
from apps.orders.models import OrderRequest, OrderRequestProduct


def suggest_order(user, region, products):
    suppliers = _get_available_suppliers(user, region)
    price_options = _get_price_options(products, suppliers)
    cheapest = _assign_suppliers(products, user, region, suppliers, price_options)
    fewest = _assign_fewest_suppliers(products, user, region, suppliers, price_options)
    return {
        "cheapest": _assignments_to_scenario(cheapest, "cheapest"),
        "fewest_suppliers": _assignments_to_scenario(fewest, "fewest_suppliers"),
        "minimum_issues": {
            "cheapest": _check_missing_minimum(cheapest),
            "fewest_suppliers": _check_missing_minimum(fewest),
        },
    }


def _assign_suppliers(products, user, region, suppliers=None, price_options=None):
    if suppliers is None:
        suppliers = _get_available_suppliers(user, region)
    if price_options is None:
        price_options = _get_price_options(products, suppliers)
    assignments_list = _build_initial_assignments(products, price_options)
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


def _build_initial_assignments(products, price_options):
    assignments = []
    missing = []
    for product in products:
        prices = price_options.get(product["product"].id, [])
        if not prices:
            missing.append(product["product"].name)
            continue
        supplier, price = prices[0]
        assignments.append({
            "product": product["product"],
            "quantity": product["quantity"],
            "supplier": supplier,
            "unit_price": price,
            "all_prices": prices,
            "price_by_supplier": {s.id: p for s, p in prices},
        })
    if missing:
        raise ValueError(f"אין ספק שיכול לספק: {', '.join(missing)}")
    return assignments


def _validate_all_products_present(assignments, products):
    assigned_products = {a["product"].id for a in assignments}
    requested_products = {i["product"].id for i in products}
    if assigned_products != requested_products:
        raise ValueError("חלק מהמוצרים לא שובצו לספק")


def _get_available_suppliers(user, region):
    return Supplier.objects.filter(region=region)


def _get_price_options(products, suppliers):
    """
    Single query for all products (instead of one query per product), grouped
    into {product_id: [(supplier, price), ...]} sorted ascending by price —
    same shape/order the old per-product query returned, just batched.
    """
    product_ids = [p["product"].id for p in products]
    rows = (
        SupplierProduct.objects
        .filter(
            product_id__in=product_ids,
            supplier__in=suppliers,
            price_per_unit__isnull=False,
        )
        .select_related("supplier")
        .order_by("product_id", "price_per_unit", "id")
    )
    options = defaultdict(list)
    for sp in rows:
        options[sp.product_id].append((sp.supplier, sp.price_per_unit))
    return options


def _assign_fewest_suppliers(products, user, region, suppliers=None, price_options=None):
    """
    Greedy set cover: pick the fewest distinct suppliers that cover all products.
    Tie-break by lower total cost on the products covered. Then enforce minimum
    orders by handing off below-minimum supplier groups via _force_minimum_switch.
    """
    if suppliers is None:
        suppliers = _get_available_suppliers(user, region)
    if price_options is None:
        price_options = _get_price_options(products, suppliers)

    missing = [p["product"].name for p in products if not price_options.get(p["product"].id)]
    if missing:
        raise ValueError(f"אין ספק שיכול לספק: {', '.join(missing)}")

    product_options = {p["product"].id: price_options[p["product"].id] for p in products}

    # Per-product {supplier_id: price} lookup, built once so the greedy loop
    # below does O(1) price lookups instead of a linear scan per candidate.
    price_by_product_supplier = {
        pid: {s.id: p for s, p in prices} for pid, prices in product_options.items()
    }

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
                quantities[pid] * price_by_product_supplier[pid][sid]
                for pid in new_covered
            )
            score = (len(new_covered), -cost)
            if best_score is None or score > best_score:
                best_sid = sid
                best_score = score
        if best_sid is None:
            raise ValueError("לא ניתן לכסות את כל המוצרים עם הספקים הזמינים")
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
            "price_by_supplier": price_by_product_supplier[pid],
        })

    return _force_minimum_switch(assignments)


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
                "unit": a["product"].get_unit_display(),
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
    """
    For each supplier group below its own minimum order, automatically resolve
    it with whichever of two strategies costs less — no human/customer
    intervention needed unless neither strategy can clear the minimum at all:

      - "pad": pull a few more items onto this supplier from elsewhere in the
        SAME order — cheapest-penalty-first (the items where this supplier is
        closest to what's already being paid) — until its total clears its
        minimum. Only pulls from suppliers who have slack to spare (won't
        rob one group's minimum to fix another's).
      - "switch": move the whole under-minimum group to a different supplier
        who can carry all of it and already clears (or reaches) their own
        minimum — the original, simpler strategy.

    Both strategies only ever raise the order's total versus the pure
    cheapest-per-item baseline (by definition — that baseline is what's
    already assigned), so comparing them is just comparing added cost.

    Which groups get one resolution attempt is decided once upfront (the
    initially-under-minimum suppliers), but the totals/suppliers snapshot
    used for each attempt's safety checks is recomputed fresh every time —
    an earlier group's pad/switch in this same call can move items and
    change other suppliers' totals, and a stale snapshot would let a later
    group's padding steal from a donor that's already been drawn down.
    """
    initial_totals, initial_suppliers = _calculate_supplier_totals(assignments)
    problem_sids = [
        sid for sid, total in initial_totals.items()
        if total < initial_suppliers[sid].minimum_order
    ]

    for sid in problem_sids:
        totals, suppliers = _calculate_supplier_totals(assignments)
        items = [a for a in assignments if a["supplier"].id == sid]
        if not items:
            continue  # this group was fully absorbed by an earlier group's switch already
        supplier = suppliers[sid]
        total = totals[sid]

        if total >= supplier.minimum_order:
            continue  # already resolved as a side effect of an earlier group

        pad_plan, pad_added_cost = _try_pad_plan(sid, supplier, total, items, assignments, totals, suppliers)

        switch_supplier = _find_next_valid_supplier(items, sid, assignments)
        switch_added_cost = None
        if switch_supplier is not None:
            new_group_total = _calculate_total_for_supplier(items, switch_supplier.id)
            switch_added_cost = new_group_total - total

        if pad_plan is not None and (switch_added_cost is None or pad_added_cost <= switch_added_cost):
            for item, new_price in pad_plan:
                item["supplier"] = supplier
                item["unit_price"] = new_price
        elif switch_supplier is not None:
            _move_items_to_supplier(items, switch_supplier)
        # else: neither strategy can clear the minimum — left as-is, still
        # surfaced via _check_missing_minimum for the caller to act on.

    return assignments


def _try_pad_plan(sid, supplier, current_total, group_items, all_assignments, totals, suppliers):
    """
    Try to reach `supplier`'s minimum by moving OTHER order items onto it,
    cheapest-penalty-first (penalty = extra cost vs. that item's current
    supplier). Skips any item whose current supplier would itself drop below
    its own minimum by losing it — padding only draws on genuine slack.

    Returns ([(item, new_price), ...], added_cost) on success, or (None, None)
    if this supplier doesn't carry enough of the rest of the order to ever
    reach its minimum this way.
    """
    group_ids = {id(a) for a in group_items}
    candidates = []
    for item in all_assignments:
        if id(item) in group_ids:
            continue
        price_at_sid = item["price_by_supplier"].get(sid)
        if price_at_sid is None:
            continue

        orig_sid = item["supplier"].id
        item_cost_at_orig = item["quantity"] * item["unit_price"]
        if totals[orig_sid] - item_cost_at_orig < suppliers[orig_sid].minimum_order:
            continue  # would push the item's current supplier below its own minimum

        penalty = (price_at_sid - item["unit_price"]) * item["quantity"]
        candidates.append((penalty, price_at_sid, item))

    candidates.sort(key=lambda c: c[0])

    running_total = current_total
    added_cost = Decimal(0)
    plan = []
    for penalty, price_at_sid, item in candidates:
        if running_total >= supplier.minimum_order:
            break
        running_total += item["quantity"] * price_at_sid
        added_cost += penalty
        plan.append((item, price_at_sid))

    if running_total >= supplier.minimum_order:
        return plan, added_cost
    return None, None


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
        price = a["price_by_supplier"].get(supplier_id)
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
        price = a["price_by_supplier"].get(new_supplier.id)
        if price is not None:
            a["supplier"] = new_supplier
            a["unit_price"] = price


def find_full_coverage_fallback(order_request_id: int, failing_supplier_id: int):
    """
    Find the best single supplier to take over ALL remaining items from a failing supplier.

    Returns {supplier, items: [{orp, new_price}], new_total, minimum_met} or None.
    Prefers suppliers that meet minimum; tie-breaks by cheapest redirect cost.
    """
    from apps.orders.models import OrderRequestProduct

    order = OrderRequest.objects.select_related("user__profile").get(id=order_request_id)
    customer_region = getattr(getattr(order.user, "profile", None), "region", None)

    orps = list(
        OrderRequestProduct.objects
        .filter(order_request_id=order_request_id, supplier_id=failing_supplier_id)
        .select_related("product")
    )
    if not orps:
        return None

    # For each ORP, collect alternative (supplier, price) options filtered by customer region
    product_alternatives = {}
    for orp in orps:
        sp_qs = (
            SupplierProduct.objects
            .filter(product=orp.product, price_per_unit__isnull=False)
            .exclude(supplier_id=failing_supplier_id)
            .select_related("supplier")
            .order_by("price_per_unit")
        )
        if customer_region:
            sp_qs = sp_qs.filter(supplier__region=customer_region)
        options = [(sp.supplier, sp.price_per_unit) for sp in sp_qs]
        product_alternatives[orp.id] = options

    # Build coverage: which suppliers cover which orp_ids
    supplier_coverage = defaultdict(set)
    supplier_obj = {}
    for orp_id, options in product_alternatives.items():
        for s, _ in options:
            supplier_coverage[s.id].add(orp_id)
            supplier_obj[s.id] = s

    all_orp_ids = {orp.id for orp in orps}
    full_coverage_ids = [
        sid for sid, covered in supplier_coverage.items()
        if all_orp_ids <= covered
    ]
    if not full_coverage_ids:
        return None

    # Existing order totals for candidate suppliers
    existing_totals = defaultdict(Decimal)
    for orp in OrderRequestProduct.objects.filter(
        order_request_id=order_request_id, supplier_id__in=full_coverage_ids
    ):
        existing_totals[orp.supplier_id] += orp.quantity * orp.unit_price

    scored = []
    for sid in full_coverage_ids:
        supplier = supplier_obj[sid]
        item_details = []
        redirect_total = Decimal(0)
        for orp in orps:
            price = next(p for s, p in product_alternatives[orp.id] if s.id == sid)
            redirect_total += orp.quantity * price
            item_details.append({"orp": orp, "new_price": price})
        new_total = existing_totals.get(sid, Decimal(0)) + redirect_total
        minimum_met = new_total >= supplier.minimum_order
        scored.append({
            "supplier": supplier,
            "items": item_details,
            "redirect_total": redirect_total,
            "new_total": new_total,
            "minimum_met": minimum_met,
            "missing_amount": max(Decimal(0), supplier.minimum_order - new_total),
        })

    scored.sort(key=lambda x: (not x["minimum_met"], x["redirect_total"]))
    return scored[0]


def find_fallback_for_product(product, excluded_supplier_id: int, order_request_id: int, quantity: Decimal):
    """
    Find the cheapest alternative supplier for a product a supplier can't fulfill.
    Returns {"supplier", "price", "minimum_met", "missing_amount"} or None.
    """
    from apps.orders.models import OrderRequestProduct

    order = OrderRequest.objects.select_related("user__profile").get(id=order_request_id)
    customer_region = getattr(getattr(order.user, "profile", None), "region", None)

    sp_qs = (
        SupplierProduct.objects
        .filter(product=product, price_per_unit__isnull=False)
        .exclude(supplier_id=excluded_supplier_id)
        .select_related("supplier")
        .order_by("price_per_unit")
    )
    if customer_region:
        sp_qs = sp_qs.filter(supplier__region=customer_region)
    candidates = sp_qs

    if not candidates.exists():
        return None

    existing_totals = defaultdict(Decimal)
    for orp in OrderRequestProduct.objects.filter(order_request_id=order_request_id).select_related("supplier"):
        existing_totals[orp.supplier_id] += orp.quantity * orp.unit_price

    for sp in candidates:
        supplier = sp.supplier
        price = sp.price_per_unit
        existing = existing_totals.get(supplier.id, Decimal(0))
        new_total = existing + quantity * price
        minimum_met = new_total >= supplier.minimum_order
        missing_amount = max(Decimal(0), supplier.minimum_order - new_total)
        return {
            "supplier": supplier,
            "price": price,
            "minimum_met": minimum_met,
            "missing_amount": missing_amount,
        }

    return None
