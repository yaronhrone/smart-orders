from decimal import Decimal
from collections import defaultdict
from urllib.parse import quote

from apps.catalog.models import SupplierProduct, Supplier, MarketPrice
from apps.orders.models import OrderRequest, OrderRequestProduct

SPLIT_THRESHOLD = Decimal("0.10")

# def _fill_supplier_minimum(assignments):
#     """
#     Try to fill suppliers that are below minimum_order
#     by moving products from other suppliers.

#     Greedy approach: move cheapest transferable products.
#     """

#     from collections import defaultdict
#     from decimal import Decimal

#     supplier_totals = defaultdict(Decimal)

#     # calculate totals
#     for a in assignments:
#         supplier_totals[a["supplier"].id] += a["quantity"] * a["unit_price"]

#     # group assignments
#     by_supplier = defaultdict(list)
#     for a in assignments:
#         by_supplier[a["supplier"].id].append(a)

#     for sid, products in by_supplier.products():
#         supplier = products[0]["supplier"]

#         if supplier_totals[sid] >= supplier.minimum_order:
#             continue

#         missing = supplier.minimum_order - supplier_totals[sid]

#         # 🔥 try to steal products from other suppliers
#         for a in assignments:
#             if a["supplier"].id == sid:
#                 continue

#             # check if this supplier can supply this product
#             alt_price = None
#             for s, p in a["all_prices"]:
#                 if s.id == sid:
#                     alt_price = p
#                     break

#             if alt_price is None:
#                 continue

#             current_cost = a["quantity"] * a["unit_price"]
#             new_cost = a["quantity"] * alt_price

#             # 🔥 move product
#             supplier_totals[a["supplier"].id] -= current_cost
#             supplier_totals[sid] += new_cost

#             a["supplier"] = supplier
#             a["unit_price"] = alt_price

#             missing -= new_cost

#             if missing <= 0:
#                 break

#     return assignments
# def _enforce_minimum_orders(assignments):
#     """
#     For each supplier whose assigned total is below their minimum_order,
#     moves ALL their products together to the cheapest viable alternative supplier.

#     "Viable" means the alt supplier:
#       1. Carries every product in the group.
#       2. Would meet its own minimum_order after receiving all those products.

#     If no viable alt exists the original assignment is kept (best-effort).
#     Items are always moved as a group so that the alt's minimum can be reached
#     even when no single product alone would cover it.
#     """
#     supplier_totals = defaultdict(Decimal)
#     supplier_obj = {}
#     for a in assignments:
#         supplier_totals[a["supplier"].id] += a["quantity"] * a["unit_price"]
#         supplier_obj[a["supplier"].id] = a["supplier"]

#     # Group assignments by supplier
#     by_supplier = defaultdict(list)
#     for a in assignments:
#         by_supplier[a["supplier"].id].append(a)

#     for sid in list(by_supplier.keys()):
#         supplier = supplier_obj[sid]
#         if supplier_totals[sid] >= supplier.minimum_order:
#             missing = supplier.minimum_order - supplier_totals[sid]
#             for a in assignments:
#                 if a["supplier"].id == sid:
#                     continue

#                 # אם הספק הזה יכול לספק את המוצר
#                 for s, price in a["all_prices"]:
#                     if s.id == sid:
#                         extra_cost = a["quantity"] * price

#                         if extra_cost <= missing:
#                             # 🔥 מעבירים את המוצר לספק הזה
#                             supplier_totals[a["supplier"].id] -= a["quantity"] * a["unit_price"]
#                             supplier_totals[sid] += extra_cost

#                             a["supplier"] = s
#                             a["unit_price"] = price

#                             missing -= extra_cost

#                         if missing <= 0:
#                             break
#             continue

#         products = by_supplier[sid]
#         if not products:
#             continue

#         # Find alt suppliers that carry EVERY product in this group
#         candidate_ids = None
#         alt_supplier_map = {}
#         for product in products:
#             item_alt_ids = set()
#             for s, _ in product["all_prices"]:
#                 if s.id != sid:
#                     item_alt_ids.add(s.id)
#                     alt_supplier_map[s.id] = s
#             candidate_ids = item_alt_ids if candidate_ids is None else candidate_ids & item_alt_ids

#         if not candidate_ids:
#             continue  # No single supplier covers all products in this group

#         # Pick the cheapest alt whose total (existing + group) meets its minimum
#         best_alt_id = None
#         best_cost = None
#         for alt_id in candidate_ids:
#             price_map = {s.id: p for product in products for s, p in product["all_prices"]}
#             cost = sum(product["quantity"] * next(p for s, p in product["all_prices"] if s.id == alt_id)
#                        for product in products)
#             new_total = supplier_totals[alt_id] + cost
#             if new_total >= alt_supplier_map[alt_id].minimum_order:
#                 if best_cost is None or cost < best_cost:
#                     best_alt_id = alt_id
#                     best_cost = cost

#         if best_alt_id is None:
#             continue  # No alt meets its own minimum even after receiving all products

#         alt_supplier = alt_supplier_map[best_alt_id]
#         for product in products:
#             alt_price = next(p for s, p in product["all_prices"] if s.id == best_alt_id)
#             supplier_totals[sid] -= product["quantity"] * product["unit_price"]
#             supplier_totals[best_alt_id] += product["quantity"] * alt_price
#             product["supplier"] = alt_supplier
#             product["unit_price"] = alt_price
#     assignments = _fill_supplier_minimum(assignments)
#     # Filter out suppliers that don't meet their own minimum
#     valid_assignments = []

#     supplier_totals = defaultdict(Decimal)
#     # for sid, supplier in supplier_obj.products():
#     #     if supplier_totals[sid] < supplier.minimum_order:
#     #         raise ValueError(f"{supplier.name} below minimum order")
#     for a in assignments:
#         supplier_totals[a["supplier"].id] += a["quantity"] * a["unit_price"]

#     for a in assignments:
#         supplier = a["supplier"]
#         if supplier_totals[supplier.id] >= supplier.minimum_order:
#             valid_assignments.append(a)

#     # best-effort: if no supplier meets minimum, keep original assignments
#     if not valid_assignments:
#         raise ValueError("Order cannot be fulfilled: minimum order not reached")

#     return valid_assignments




# def _get_available_suppliers(user, region):
#     """Returns all suppliers available to a user: global ones in their region + their private ones."""
#     return Supplier.objects.filter(
#         region=region,
#         owner__isnull=True,
#     ) | Supplier.objects.filter(owner=user)


# def _prices_for_product(product, suppliers):
#     """Returns list of (supplier, price) sorted cheapest first for a given product."""
#     prices = (
#         SupplierProduct.objects.filter(product=product, supplier__in=suppliers)
#         .select_related("supplier")
#         .order_by("price_per_unit")
#     )
#     return [(sp.supplier, sp.price_per_unit) for sp in prices]


# def _assign_suppliers(products, user, region):
#     """
#     Cheapest scenario algorithm.
#     products: list of {"product": Product, "quantity": Decimal}
#     Returns: list of {"product", "quantity", "supplier", "unit_price", "all_prices"}
#     """
#     suppliers = _get_available_suppliers(user, region)

#     assignments = []
#     for product in products:
#         prices = _prices_for_product(product["product"], suppliers)
#         if not prices:
#             raise ValueError(f"אין ספק זמין למוצר: {product['product'].name}")

#         chosen_supplier = None
#         chosen_price = None

#         for supplier, price in prices:
#             # 🔥 בדיקה של מינימום (בסיסית ל-POC)
#             if supplier.minimum_order == 0 or (product["quantity"] * price) >= supplier.minimum_order:
#                 chosen_supplier = supplier
#                 chosen_price = price
#                 break

#         if chosen_supplier is None:
#             # fallback → קח את הכי זול גם אם לא עומד במינימום
#             chosen_supplier, chosen_price = prices[0]

#         assignments.append({
#             "product": product["product"],
#             "quantity": product["quantity"],
#             "supplier": chosen_supplier,
#             "unit_price": chosen_price,
#             "all_prices": prices,
#         })
#         prices = _prices_for_product(product["product"], suppliers)

#     return _enforce_minimum_orders(assignments)


# def _assign_fewest_suppliers(products, user, region):
#     """
#     Fewest suppliers scenario — greedy set-cover.

#     Each iteration picks the supplier that covers the most uncovered products.
#     Ties are broken by lowest total cost for the covered products.

#     products: list of {"product": Product, "quantity": Decimal}
#     Returns: list of {"product", "quantity", "supplier", "unit_price", "all_prices"}
#     """
#     suppliers = list(_get_available_suppliers(user, region))

#     # product_id → {prices, product, quantity, price_map}
#     product_data = {}
#     for product in products:
#         prices = _prices_for_product(product["product"], suppliers)
#         if not prices:
#             raise ValueError(f"אין ספק זמין למוצר: {product['product'].name}")
#         product_data[product["product"].id] = {
#             "product": product["product"],
#             "quantity": product["quantity"],
#             "prices": prices,
#             "price_map": {s.id: p for s, p in prices},
#         }

#     # supplier_id → set of product_ids that supplier can cover
#     supplier_coverage = defaultdict(set)
#     supplier_obj = {}
#     for pid, data in product_data.products():
#         for supplier, _ in data["prices"]:
#             supplier_coverage[supplier.id].add(pid)
#             supplier_obj[supplier.id] = supplier

#     uncovered = set(product_data.keys())
#     chosen = {}  # product_id → chosen supplier_id

#     while uncovered:
#         best_sid = None
#         best_covered = set()
#         best_cost = Decimal("Infinity")

#         for sid, covered_pids in supplier_coverage.products():
#             covered = covered_pids & uncovered
#             if not covered:
#                 continue
#             cost = sum(
#                 product_data[pid]["quantity"] * product_data[pid]["price_map"][sid]
#                 for pid in covered
#                 if sid in product_data[pid]["price_map"]
#             )
#             if len(covered) > len(best_covered) or (
#                 len(covered) == len(best_covered) and cost < best_cost
#             ):
#                 best_sid = sid
#                 best_covered = covered
#                 best_cost = cost

#         if best_sid is None:
#             raise ValueError("Cannot cover all products with available suppliers")

#         for pid in best_covered:
#             chosen[pid] = best_sid
#         uncovered -= best_covered

#     # build assignments from chosen supplier per product
#     assignments = []
#     for pid, sid in chosen.products():
#         data = product_data[pid]
#         unit_price = data["price_map"][sid]
#         assignments.append({
#             "product": data["product"],
#             "quantity": data["quantity"],
#             "supplier": supplier_obj[sid],
#             "unit_price": unit_price,
#             "all_prices": data["prices"],
#         })

#     return _enforce_minimum_orders(assignments)


# def _assignments_to_scenario(assignments, scenario_name):
#     """Converts a raw assignments list into a structured dict for the serializer."""
#     total = sum(a["quantity"] * a["unit_price"] for a in assignments)
#     supplier_ids = {a["supplier"].id for a in assignments}

#     return {
#         "scenario": scenario_name,
#         "total_price": total,
#         "supplier_count": len(supplier_ids),
#         "products": [
#             {
#                 "product_id": a["product"].id,
#                 "product_name": a["product"].name,
#                 "quantity": a["quantity"],
#                 "unit_price": a["unit_price"],
#                 "subtotal": a["quantity"] * a["unit_price"],
#                 "supplier_id": a["supplier"].id,
#                 "supplier_name": a["supplier"].name,
#             }
#             for a in assignments
#         ],
#     }


# def _market_comparison(assignments):
#     """
#     Compares order prices against Agricultural Authority reference prices.

#     savings: positive = our price is cheaper than market; negative = market is cheaper.
#     """
#     product_ids = [a["product"].id for a in assignments]
#     market_prices = {
#         mp.product_id: mp.price_per_unit
#         for mp in MarketPrice.objects.filter(product_id__in=product_ids)
#     }

#     products = []
#     our_total = Decimal("0")
#     market_total = Decimal("0")
#     has_all = True

#     for a in assignments:
#         our_sub = a["unit_price"] * a["quantity"]
#         our_total += our_sub

#         market_unit = market_prices.get(a["product"].id)
#         if market_unit is not None:
#             market_sub = market_unit * a["quantity"]
#             market_total += market_sub
#             savings = market_sub - our_sub
#         else:
#             market_sub = None
#             savings = None
#             has_all = False

#         products.append({
#             "product_id": a["product"].id,
#             "product_name": a["product"].name,
#             "quantity": a["quantity"],
#             "our_unit_price": a["unit_price"],
#             "market_unit_price": market_unit,
#             "our_subtotal": our_sub,
#             "market_subtotal": market_sub,
#             "savings": savings,
#         })

#     return {
#         "products": products,
#         "our_total": our_total,
#         "market_total": market_total if has_all else None,
#         "total_savings": (market_total - our_total) if has_all else None,
#     }


# # ---------------------------------------------------------------------------
# # Public API
# # ---------------------------------------------------------------------------

# def suggest_order(user, region, products):
#     """
#     Returns two scenarios and a market comparison. Does NOT write to DB.

#     products: list of {"product": Product, "quantity": Decimal}
#     """
#     cheapest = _assign_suppliers(products, user, region)
#     fewest = _assign_fewest_suppliers(products, user, region)

#     return {
#         "cheapest": _assignments_to_scenario(cheapest, "cheapest"),
#         "fewest_suppliers": _assignments_to_scenario(fewest, "fewest_suppliers"),
#         "market_comparison": _market_comparison(cheapest),
#     }


# def generate_whatsapp_links(assignments):
#     """
#     Generates a WhatsApp deep link per supplier from an assignments list.

#     Returns: dict of {supplier_id: {"supplier_id", "supplier_name", "phone", "whatsapp_url"}}
#     """
#     by_supplier = defaultdict(list)
#     supplier_obj = {}
#     for a in assignments:
#         by_supplier[a["supplier"].id].append(a)
#         supplier_obj[a["supplier"].id] = a["supplier"]

#     links = {}
#     for sid, sup_items in by_supplier.products():
#         supplier = supplier_obj[sid]

#         lines = ["שלום, ברצוני להזמין:"]
#         for product in sup_items:
#             lines.append(
#                 f"- {product['product'].name} x{product['quantity']} {product['product'].get_unit_display()}"
#             )
#         lines.append("תודה!")

#         clean_phone = "".join(filter(str.isdigit, supplier.whatsapp_number))
#         url = f"https://wa.me/{clean_phone}?text={quote(chr(10).join(lines))}"

#         links[sid] = {
#             "supplier_id": sid,
#             "supplier_name": supplier.name,
#             "phone": supplier.whatsapp_number,
#             "whatsapp_url": url,
#         }

#     return links


# def build_order(user, region, products, scenario="cheapest"):
#     """
#     Saves an order to DB and returns (OrderRequest, whatsapp_links).

#     scenario: "cheapest" | "fewest_suppliers"
#     products: list of {"product": Product, "quantity": Decimal}
#     """
#     if scenario == "fewest_suppliers":
#         assignments = _assign_fewest_suppliers(products, user, region)
#     else:
#         assignments = _assign_suppliers(products, user, region)

#     total = sum(a["quantity"] * a["unit_price"] for a in assignments)
#     order = OrderRequest.objects.create(user=user, total_price=total)

#     OrderRequestProduct.objects.bulk_create([
#         OrderRequestProduct(
#             order_request=order,
#             product=a["product"],
#             supplier=a["supplier"],
#             quantity=a["quantity"],
#             unit_price=a["unit_price"],
#         )
#         for a in assignments
#     ])

#     return order, generate_whatsapp_links(assignments)

# option 2
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

    # 1. initial
    assignments = _build_initial_assignments(products, suppliers)

    # 2. fix minimum
    assignments = fill_until_stable(assignments)

    # 3. validate
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
from collections import defaultdict
from decimal import Decimal


def _fill_supplier_minimum(assignments):
    # group by supplier
    by_supplier = defaultdict(list)
    for a in assignments:
        by_supplier[a["supplier"].id].append(a)

    for sid, products in by_supplier.items():
        supplier = products[0]["supplier"]

        total = sum(a["quantity"] * a["unit_price"] for a in products)

        # אם עומד במינימום → לא נוגעים
        if total >= supplier.minimum_order:
            continue

        # 🔥 למצוא ספקים שיכולים לספק את כל המוצרים
        candidate_suppliers = None

        for a in products:
            suppliers_for_product = {s.id: s for s, _ in a["all_prices"]}

            if candidate_suppliers is None:
                candidate_suppliers = suppliers_for_product
            else:
                # intersection נכון
                candidate_suppliers = {
                    s_id: candidate_suppliers[s_id]
                    for s_id in candidate_suppliers
                    if s_id in suppliers_for_product
                }

        # אין ספקים מתאימים
        if not candidate_suppliers:
            continue

        # להסיר את הספק הנוכחי
        candidate_suppliers.pop(sid, None)
        best_supplier = None
        best_total = None

        # 🔥 לנסות כל ספק חלופי
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
            # ✅ perfect match (meets minimum)
            if new_total >= cand_supplier.minimum_order:
                best_supplier = cand_supplier
                break

            # 🔥 fallback: keep best even if not meeting minimum
            if best_total is None or new_total < best_total:
                best_total = new_total
                best_supplier = cand_supplier

        # 🔥 apply best found
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

        # פשוט לוקחים את הראשון (אפשר לשפר בעתיד)
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
        # שומרים snapshot לפני שינוי
        before = [
            (a["supplier"].id, a["unit_price"])
            for a in assignments
        ]

        # מריצים את האלגוריתם
        assignments = _fill_supplier_minimum(assignments)

        # snapshot אחרי
        after = [
            (a["supplier"].id, a["unit_price"])
            for a in assignments
        ]

        # אם אין שינוי → לעצור
        if before == after:
            break

    return assignments