from decimal import Decimal
from collections import defaultdict
from urllib.parse import quote
from apps.catalog.models import SupplierProduct, Supplier, MarketPrice
from apps.orders.models import OrderRequest, OrderRequestProduct
def suggest_order(user, region, products):
    cheapest = _assign_suppliers(products, user, region)
    fewest = _assign_fewest_suppliers(products, user, region)
    problems = _check_missing_minimum(cheapest)
    return {
        "cheapest": _assignments_to_scenario(cheapest, "cheapest"),
        "fewest_suppliers": _assignments_to_scenario(fewest, "fewest_suppliers"),
        "market_comparison": {
        "products": [],
        "our_total": 0,
        "market_total": None,
        "total_savings": None,
    },
        "minimum_issues": problems or [],
    }
def _assign_suppliers(products, user, region):
    suppliers = _get_available_suppliers(user, region)
    assignments = _build_initial_assignments(products, suppliers)
    assignments = fill_until_stable(assignments)
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
def _fill_supplier_minimum(assignments):

    by_supplier = defaultdict(list)
    for a in assignments:
        by_supplier[a["supplier"].id].append(a)
    for sid, products in by_supplier.items():
        supplier = products[0]["supplier"]
        total = sum(a["quantity"] * a["unit_price"] for a in products)
        if total >= supplier.minimum_order:
            continue
        candidate_suppliers = None
        for a in products:
            suppliers_for_product = {s.id: s for s, _ in a["all_prices"]}

            if candidate_suppliers is None:
                candidate_suppliers = suppliers_for_product
            else:
                candidate_suppliers = {
                    s_id: candidate_suppliers[s_id]
                    for s_id in candidate_suppliers
                    if s_id in suppliers_for_product
                }
        if not candidate_suppliers:
            continue
        candidate_suppliers.pop(sid, None)
        best_supplier = None
        best_total = None
        for cand_id, cand_supplier in candidate_suppliers.items():
            new_total = Decimal(0)
            valid = True
            for a in products:
                found_price = False
                for s, p in a["all_prices"]:
                    if s.id == cand_id:
                        new_total += a["quantity"] * p
                        found_price = True
                        break
                if not found_price:
                    valid = False
                    break
            if not valid:
                continue
            if new_total >= cand_supplier.minimum_order:
                best_supplier = cand_supplier
                break
            if best_total is None or new_total < best_total:
                best_total = new_total
                best_supplier = cand_supplier
        if best_supplier:
            for a in products:
                for s, p in a["all_prices"]:
                    if s.id == best_supplier.id:
                        a["supplier"] = best_supplier
                        a["unit_price"] = p
                        break
    return assignments
def _get_available_suppliers(user, region):
    return Supplier.objects.filter(
        region=region,
        owner__isnull=True,
    ) | Supplier.objects.filter(owner=user)
def _prices_for_product(product, suppliers):
    prices = (
        SupplierProduct.objects
        .filter(product=product, supplier__in=suppliers)
        .select_related("supplier")
        .order_by("price_per_unit")
    )
    return [(sp.supplier, sp.price_per_unit) for sp in prices]
def _assign_fewest_suppliers(products, user, region):
    suppliers = _get_available_suppliers(user, region)
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
                "missing_amount": missing
            })

    return problems
def fill_until_stable(assignments, max_iterations=10):
    """
    Run _fill_supplier_minimum until no more changes happen
    or until max_iterations is reached.
    """
    for _ in range(max_iterations):
        before = [
            (a["supplier"].id, a["unit_price"])
            for a in assignments
        ]
        assignments = _fill_supplier_minimum(assignments)
        after = [
            (a["supplier"].id, a["unit_price"])
            for a in assignments
        ]
        if before == after:
            break
    return assignments