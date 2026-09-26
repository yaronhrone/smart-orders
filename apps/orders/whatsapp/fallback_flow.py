import json
import logging
from collections import defaultdict
from decimal import Decimal

from django.http import HttpResponse

from .cache import (
    _get_fallback_state, _save_fallback_state, _clear_fallback_state,
    _get_reroute_grace_state, _clear_reroute_grace_state,
    save_supplier_pending_order,
)
from . import validators

logger = logging.getLogger(__name__)


def _recalculate_order_total(order_request_id: int):
    """
    After items were removed from / moved out of an order: recompute its
    total, cancel it if it's now empty, approve it if everything left is
    confirmed (see services.refresh_order_after_changes). One order is one
    supplier now, so "the rest of this order" is always just that supplier's.
    """
    from apps.orders.services import refresh_order_after_changes
    try:
        refresh_order_after_changes(order_request_id)
    except Exception as exc:
        logger.error("_recalculate_order_total(%s): %s", order_request_id, exc)


def _handle_missing_items(original_supplier, missing_products: list, order_request_id: int, partial_products: list = None):
    """
    Find fallback suppliers for items the supplier can't fulfill and notify the customer.
    missing_products: fully missing (entire ORP needs redirect).
    partial_products: supplier confirmed partial qty; remaining qty needs fallback (ORP already reduced).
    Edge case 5: if no fallback exists for a product, auto-remove it from the order.
    """
    from apps.catalog.models import Product, SupplierProduct
    from apps.orders.models import OrderRequest, OrderRequestProduct
    from apps.orders.services import find_fallback_for_product

    partial_products = partial_products or []

    order = (
        OrderRequest.objects
        .select_related("user__profile")
        .filter(id=order_request_id)
        .first()
    )
    if not order:
        return

    customer_profile = getattr(order.user, "profile", None)
    customer_phone = (
        validators._local_to_e164(customer_profile.phone)
        if customer_profile and customer_profile.phone else None
    )
    if not customer_phone:
        return

    redirects = []
    no_fallback = []  # list of {"product_name", "quantity", "unit", "partial"}
    auto_removed = False

    # ── Process fully missing items ──
    for mp in missing_products:
        product = Product.objects.filter(name=mp["product_name"]).first()
        if not product:
            no_fallback.append({"product_name": mp["product_name"], "quantity": mp.get("quantity", ""), "unit": mp.get("unit", "")})
            continue

        existing_orp = OrderRequestProduct.objects.filter(
            order_request_id=order_request_id, product=product
        ).first()
        original_price = str(existing_orp.unit_price) if existing_orp else "?"

        # Supplier has none of this product right now — stop offering them for it
        # in future orders until they send an updated price (update_or_create in
        # price_parser.py recreates the row and restores it).
        SupplierProduct.objects.filter(supplier=original_supplier, product=product).delete()

        fallback = find_fallback_for_product(
            product=product,
            excluded_supplier_id=original_supplier.id,
            order_request_id=order_request_id,
            quantity=Decimal(str(mp["quantity"])),
        )

        if not fallback:
            # Edge case 5: no supplier at all — auto-remove from order immediately
            if existing_orp:
                existing_orp.delete()
                auto_removed = True
            no_fallback.append({"product_name": mp["product_name"], "quantity": mp.get("quantity", ""), "unit": mp.get("unit", "")})
            continue

        redirects.append({
            "type": "missing",
            "orp_id": mp["orp_id"],
            "product_name": mp["product_name"],
            "quantity": mp["quantity"],
            "unit": mp.get("unit", ""),
            "original_supplier_id": original_supplier.id,
            "original_supplier_name": original_supplier.name,
            "original_price": original_price,
            "fallback_supplier_id": fallback["supplier"].id,
            "fallback_supplier_name": fallback["supplier"].name,
            "fallback_supplier_whatsapp": fallback["supplier"].whatsapp_number,
            "fallback_price": str(fallback["price"]),
            "minimum_met": fallback["minimum_met"],
            "missing_amount": str(fallback["missing_amount"]),
        })

    # ── Process partially confirmed items (edge case 2) ──
    for pp in partial_products:
        try:
            orp = OrderRequestProduct.objects.select_related("product").get(id=int(pp["orp_id"]))
        except OrderRequestProduct.DoesNotExist:
            continue
        product = orp.product

        fallback = find_fallback_for_product(
            product=product,
            excluded_supplier_id=original_supplier.id,
            order_request_id=order_request_id,
            quantity=Decimal(str(pp["quantity"])),
        )

        if not fallback:
            no_fallback.append({
                "product_name": pp["product_name"],
                "quantity": pp["quantity"],
                "unit": pp.get("unit", ""),
                "partial": True,
            })
            continue

        redirects.append({
            "type": "partial",
            "orp_id": pp["orp_id"],  # original ORP already reduced to confirmed_qty
            "product_name": pp["product_name"],
            "quantity": pp["quantity"],  # remaining (unconfirmed) qty
            "unit": pp.get("unit", ""),
            "original_supplier_id": original_supplier.id,
            "original_supplier_name": original_supplier.name,
            "original_price": str(orp.unit_price),
            "fallback_supplier_id": fallback["supplier"].id,
            "fallback_supplier_name": fallback["supplier"].name,
            "fallback_supplier_whatsapp": fallback["supplier"].whatsapp_number,
            "fallback_price": str(fallback["price"]),
            "minimum_met": fallback["minimum_met"],
            "missing_amount": str(fallback["missing_amount"]),
        })

    # Edge case 1: recalculate total after any auto-removals
    if auto_removed:
        _recalculate_order_total(order_request_id)

    # ── Build customer message ──
    lines = [f"⚠️ *{original_supplier.name}* דיווח:"]
    for r in redirects:
        if r["type"] == "partial":
            lines.append(
                f"\n• *{r['product_name']}* — ספק אישר רק חלק מהכמות"
                f"\n  נשארו {r['quantity']} {r['unit']} שטרם סופקו"
                f"\n  ✅ {r['fallback_supplier_name']} יכול לספק את הנותר ב-{r['fallback_price']}₪"
            )
        else:
            lines.append(
                f"\n• *{r['product_name']}* x{r['quantity']} {r['unit']}"
                f"\n  ❌ {r['original_supplier_name']} — חסר"
                f"\n  ✅ {r['fallback_supplier_name']} — {r['fallback_price']}₪ (במקום {r['original_price']}₪)"
            )
        if not r["minimum_met"]:
            lines.append(f"  ⚠️ חסר עוד {Decimal(r['missing_amount']):.2f}₪ למינימום ספק זה")

    for nf in no_fallback:
        if nf.get("partial"):
            lines.append(
                f"\n• *{nf['product_name']}* {nf['quantity']} {nf['unit']} — "
                "לא נמצא ספק חלופי לכמות הנותרת"
            )
        else:
            lines.append(
                f"\n• *{nf['product_name']}* x{nf['quantity']} {nf['unit']} — "
                "חסר המוצר הזה במלאי לכל הספקים, הוסר מההזמנה אוטומטית"
            )

    if redirects:
        lines.append("\nענה *כן* להעברה לספק חלופי, *לא* לביטול.")
        _save_fallback_state(customer_phone, {
            "order_request_id": order_request_id,
            "original_supplier_name": original_supplier.name,
            "redirects": redirects,
        })
    elif not lines[1:]:
        pass  # Only auto-removed items with no fallback — nothing to confirm

    validators.send_whatsapp_message(customer_phone, "\n".join(lines))


def _handle_fallback_approval(phone: str, body: str) -> HttpResponse | None:
    """Handle customer's yes/no to a fallback supplier suggestion. Returns None if no fallback pending."""
    raw = _get_fallback_state(phone)
    if not raw:
        return None

    state = json.loads(raw)
    body_lower = body.strip().lower()

    yes_words = ["כן", "yes", "אישור", "אוקי", "ok", "בסדר", "1"]
    no_words = ["לא", "no", "ביטול", "cancel", "2"]

    if any(w in body_lower for w in yes_words):
        return _execute_fallback_redirect(phone, state)
    elif any(w in body_lower for w in no_words):
        return _remove_missing_items(phone, state)
    else:
        supplier_name = state.get("original_supplier_name", "הספק")
        validators.send_whatsapp_message(
            phone,
            f"⏳ ממתין לתשובתך: *{supplier_name}* דיווח על פריטים חסרים.\n"
            "ענה *כן* להעברה לספק חלופי, *לא* לביטול.",
        )
        return HttpResponse(status=200)


def _handle_reroute_grace_topup(phone: str, body: str) -> HttpResponse | None:
    """
    A supplier's cancellation left items whose replacement supplier(s) don't
    clear their minimum — held open (see supplier_flow.process_reroute).
    Returns None if nothing is held open for this phone. Otherwise the
    customer's reply is one of:
      • "שלח"   — send as-is, below minimum; the supplier decides.
      • "ביטול" — drop the held items (the rest of the checkout is untouched).
      • anything else — an addition to the same basket (same parser a new
        order uses); rerouted again, dispatched once it clears the minimum.
    """
    from apps.catalog.models import Product
    from apps.orders.models import OrderRequest, OrderRequestProduct
    from apps.orders.order_parser import AmbiguousProductError, parse_customer_order
    from .supplier_flow import GRACE_CANCEL_WORDS, GRACE_SEND_WORDS, _cancel_source, process_reroute

    raw = _get_reroute_grace_state(phone)
    if not raw:
        return None

    state = json.loads(raw)
    order_request_id = state["order_request_id"]
    failing_supplier_id = state["failing_supplier_id"]
    reply = body.strip().lower()

    if reply in GRACE_SEND_WORDS:
        _clear_reroute_grace_state(phone)
        process_reroute(
            order_request_id, phone,
            header_lines=["👍 שולחים כמו שזה — הספק יאשר או יסרב, ונעדכן אותך."],
            force=True, start_grace=False,
        )
        return HttpResponse(status=200)

    if reply in GRACE_CANCEL_WORDS:
        _clear_reroute_grace_state(phone)
        _cancel_source(order_request_id)
        order = OrderRequest.objects.get(id=order_request_id)
        validators.send_whatsapp_message(
            phone,
            f"✅ הפריטים שנשארו מהזמנה #{order_request_id} בוטלו. "
            "שאר ההזמנות שלך מאותה הזמנה ממשיכות כרגיל."
            if order.batch.orders.exclude(id=order_request_id).exists()
            else f"✅ הפריטים שנשארו מהזמנה #{order_request_id} בוטלו.",
        )
        return HttpResponse(status=200)

    product_names = list(Product.objects.values_list("name", flat=True))
    try:
        parsed_items = parse_customer_order(body, product_names)
    except AmbiguousProductError:
        validators.send_whatsapp_message(
            phone, "לא ברור לאיזה מוצר בדיוק התכוונת — נסה שוב עם שם מדויק יותר.",
        )
        return HttpResponse(status=200)
    except ValueError:
        validators.send_whatsapp_message(
            phone, "לא הצלחתי להבין. שלח למשל: עוד 10 עגבניות — או *שלח* / *ביטול*.",
        )
        return HttpResponse(status=200)

    all_products_map = {p.name: p for p in Product.objects.all()}
    added_any = False
    for item in parsed_items:
        product = all_products_map.get(item["product_name"])
        if not product:
            continue
        existing = OrderRequestProduct.objects.filter(
            order_request_id=order_request_id, supplier_id=failing_supplier_id, product=product,
        ).first()
        if existing:
            existing.quantity = existing.quantity + Decimal(str(item["quantity"]))
            existing.save(update_fields=["quantity"])
        else:
            # New product, not part of the original cancelled group — parked
            # on the (now-blocked) failing supplier's order as a placeholder,
            # same as every other item still waiting on this reroute;
            # unit_price is never read before the reroute overwrites it.
            OrderRequestProduct.objects.create(
                order_request_id=order_request_id,
                product=product, supplier_id=failing_supplier_id,
                quantity=Decimal(str(item["quantity"])), unit_price=Decimal("0"),
            )
        added_any = True

    if not added_any:
        validators.send_whatsapp_message(phone, "לא זיהיתי מוצר ידוע בהודעה. נסה שוב, או ענה *שלח* / *ביטול*.")
        return HttpResponse(status=200)

    outcome = process_reroute(
        order_request_id, phone, header_lines=["✅ קיבלתי את התוספת."], start_grace=False,
    )
    if outcome != "grace":
        _clear_reroute_grace_state(phone)
    return HttpResponse(status=200)


def _remove_missing_items(phone: str, state: dict) -> HttpResponse:
    """Customer declined fallback — remove the missing products and check remaining minimums."""
    from apps.catalog.models import Supplier
    from apps.orders.models import OrderRequestProduct

    _clear_fallback_state(phone)

    order_request_id = state["order_request_id"]
    redirects = state["redirects"]

    # The supplier who reported the shortage in the first place never heard
    # back at all before - they confirmed what they had and then silence,
    # whichever way the customer's decision went. Tell them what the
    # customer settled for, grouped per supplier in case more than one
    # reported something in the same round.
    by_original_supplier = defaultdict(list)
    for r in redirects:
        by_original_supplier[r["original_supplier_id"]].append(r)
    for supplier_id, items in by_original_supplier.items():
        supplier = Supplier.objects.filter(id=supplier_id).first()
        if not supplier:
            continue
        lines = [f"עדכון להזמנה #{order_request_id} — הלקוח החליט שלא להעביר לספק חלופי:"]
        for r in items:
            if r.get("type") == "partial":
                lines.append(f"  • {r['product_name']} — נשאר על הכמות שאישרת, היתרה לא תוזמן.")
            else:
                lines.append(f"  • {r['product_name']} x{r['quantity']} {r.get('unit', '')} — הוסר מההזמנה.")
        validators.send_whatsapp_message(supplier.whatsapp_number, "\n".join(lines))

    removed_lines = []
    affected_supplier_ids = set()

    for r in redirects:
        if r.get("type") == "partial":
            # Original ORP was already reduced to confirmed_qty; just skip creating new ORP
            removed_lines.append(
                f"  • {r['product_name']} {r['quantity']} {r.get('unit', '')} (כמות חלקית — לא תוזמן)"
            )
        else:
            try:
                orp = OrderRequestProduct.objects.get(id=r["orp_id"])
                affected_supplier_ids.add(orp.supplier_id)
                orp.delete()
                removed_lines.append(f"  • {r['product_name']} x{r['quantity']} {r.get('unit', '')} הוסר")
            except OrderRequestProduct.DoesNotExist:
                pass

    # Edge case 1: update total_price after removals
    _recalculate_order_total(order_request_id)

    lines = ["🗑️ הבנתי — הפריטים הבאים הוסרו מההזמנה:"]
    lines += removed_lines

    # Check if remaining suppliers still meet their minimum
    remaining_orps = list(
        OrderRequestProduct.objects.filter(order_request_id=order_request_id)
        .select_related("supplier")
    )

    supplier_totals = defaultdict(Decimal)
    supplier_obj = {}
    for orp in remaining_orps:
        supplier_totals[orp.supplier_id] += orp.quantity * orp.unit_price
        supplier_obj[orp.supplier_id] = orp.supplier

    for sid in affected_supplier_ids:
        if sid not in supplier_obj:
            continue
        supplier = supplier_obj[sid]
        total = supplier_totals.get(sid, Decimal(0))
        if total >= supplier.minimum_order:
            continue

        missing_amount = supplier.minimum_order - total
        lines.append(
            f"\n⚠️ {supplier.name} נפל ל-{total:.2f}₪ (מינימום {supplier.minimum_order}₪, חסר {missing_amount:.2f}₪)."
        )
        lines.append("מחפש ספק חלופי לשאר המוצרים...")

        _auto_transfer_remaining(
            phone=phone,
            order_request_id=order_request_id,
            failing_supplier=supplier,
            lines=lines,
        )

    validators.send_whatsapp_message(phone, "\n".join(lines))
    return HttpResponse(status=200)


def _auto_transfer_remaining(phone: str, order_request_id: int, failing_supplier, lines: list):
    """Move all remaining items from failing_supplier to the best available fallback."""
    from apps.orders.models import OrderRequest, OrderRequestProduct
    from apps.orders.services import find_full_coverage_fallback

    result = find_full_coverage_fallback(
        order_request_id=order_request_id,
        failing_supplier_id=failing_supplier.id,
    )

    if not result:
        lines.append(f"❌ לא נמצא ספק שיכול לכסות את כל המוצרים של {failing_supplier.name}.")
        return

    new_supplier = result["supplier"]

    if not result["minimum_met"]:
        lines.append(
            f"⛔ {new_supplier.name} יכול לכסות הכל אך גם לא עומד במינימום "
            f"(חסר {result['missing_amount']:.2f}₪). הוסף מוצרים נוספים."
        )
        return

    # Execute the transfer — the items move into the new supplier's order in
    # the same batch (an open sibling, or a new order), and the failing
    # supplier's now-empty order is cancelled by the refresh below.
    from apps.orders.services import move_item_to_supplier
    from .supplier_flow import notify_supplier_of_items

    target = None
    created = False
    moved = []
    for item in result["items"]:
        orp = item["orp"]
        if target is None:
            target = move_item_to_supplier(orp, new_supplier, item["new_price"])
            created = not target.products.exclude(id=orp.id).exists()
        else:
            move_item_to_supplier(orp, new_supplier, item["new_price"])
        moved.append(orp)
        lines.append(f"  ↪ {orp.product.name} x{orp.quantity} → {new_supplier.name} ({item['new_price']}₪)")

    lines.append(f"✅ כל המוצרים של {failing_supplier.name} הועברו ל-{new_supplier.name} (הזמנה #{target.id}).")

    _recalculate_order_total(order_request_id)
    _recalculate_order_total(target.id)
    notify_supplier_of_items(target, moved, created=created)


def _execute_fallback_redirect(phone: str, state: dict) -> HttpResponse:
    """Execute approved fallback: update order items, send WhatsApp to new supplier."""
    from apps.catalog.models import Supplier
    from apps.orders.models import OrderRequest, OrderRequestProduct

    order_request_id = state["order_request_id"]
    redirects = state["redirects"]

    from apps.orders.services import (
        batch_supplier_totals, get_or_create_supplier_order, move_item_to_supplier,
    )
    from .supplier_flow import notify_supplier_of_items

    source_order = OrderRequest.objects.select_related("batch").get(id=order_request_id)
    batch_open_totals = batch_supplier_totals(
        source_order.batch_id, exclude_order_id=order_request_id, only_open=True,
    )

    by_supplier = defaultdict(list)
    for r in redirects:
        by_supplier[r["fallback_supplier_id"]].append(r)

    success_lines = []
    below_minimum_msgs = []
    # Redirects that couldn't actually be transferred this round (their
    # target's minimum still isn't met) — used to just vanish along with
    # the state clear below, regardless of outcome. Found live: a customer
    # who answered "כן" to a fallback offer that still didn't clear minimum
    # got told so, but the pending state was already gone by then — no way
    # left to retry "כן" after adding more products, or to say "לא" and
    # settle for what the original supplier could provide. The item's
    # shortfall then had NO record anywhere: no ORP for it, no cancellation
    # line, nothing — the order went on to silently auto-approve at the
    # reduced quantity as if that had been the whole order all along.
    unresolved_redirects = []

    for supplier_id, items in by_supplier.items():
        try:
            supplier = Supplier.objects.get(id=supplier_id)
        except Supplier.DoesNotExist:
            continue

        # Check minimum with existing + redirected items BEFORE updating DB.
        # "Existing" = what this supplier already has in an open (SENT) order
        # of the same batch — that's the order the items would merge into.
        existing_total = batch_open_totals.get(supplier.id, Decimal(0))
        redirect_total = sum(
            Decimal(r["quantity"]) * Decimal(r["fallback_price"]) for r in items
        )
        new_total = existing_total + redirect_total

        if new_total < supplier.minimum_order:
            missing_amount = supplier.minimum_order - new_total
            below_minimum_msgs.append(
                f"⛔ {supplier.name}: סה\"כ {new_total:.2f}₪, חסר {missing_amount:.2f}₪ למינימום ({supplier.minimum_order}₪)\n"
                f"   הוסף מוצרים נוספים מ-{supplier.name} כדי לעמוד במינימום."
            )
            unresolved_redirects.extend(items)
            continue  # Skip this supplier — don't update DB or send message

        # Move/create the items in this supplier's order in the same batch
        target, created = get_or_create_supplier_order(source_order.batch, supplier)
        moved_items = []
        for r in items:
            if r.get("type") == "partial":
                # Edge case 2: create NEW ORP for remaining qty — original ORP already reduced
                try:
                    original_orp = OrderRequestProduct.objects.select_related("product").get(id=r["orp_id"])
                    new_orp = OrderRequestProduct.objects.create(
                        order_request=target,
                        product=original_orp.product,
                        supplier=supplier,
                        quantity=Decimal(r["quantity"]),
                        unit_price=Decimal(r["fallback_price"]),
                    )
                    success_lines.append(
                        f"  • {r['product_name']} {r['quantity']} {r.get('unit', '')} (חלקי) → {supplier.name}"
                    )
                    moved_items.append(new_orp)
                except OrderRequestProduct.DoesNotExist:
                    pass
            else:
                # Full redirect: the existing item moves to the new supplier's order
                try:
                    orp = OrderRequestProduct.objects.select_related("product").get(id=r["orp_id"])
                    move_item_to_supplier(orp, supplier, Decimal(r["fallback_price"]))
                    success_lines.append(f"  • {r['product_name']} x{r['quantity']} {r.get('unit', '')} → {supplier.name}")
                    moved_items.append(orp)
                except OrderRequestProduct.DoesNotExist:
                    pass

        if not moved_items:
            if created:
                target.delete()  # nothing actually landed in it
            continue

        _recalculate_order_total(target.id)
        notify_supplier_of_items(target, moved_items, created=created)

    # Edge case 1: recalculate the source order after all redirect changes
    _recalculate_order_total(order_request_id)

    if unresolved_redirects:
        # Keep the pending state alive — trimmed to just what's still
        # unresolved — so a later "כן" (after adding more products) or "לא"
        # (settle for the original supplier's partial quantity / drop the
        # item) still has something to act on. _save_fallback_state also
        # reschedules handle_fallback_timeout, restoring the same
        # eventually-auto-resolves safety net the FIRST offer already had.
        _save_fallback_state(phone, {**state, "redirects": unresolved_redirects})
    else:
        _clear_fallback_state(phone)

    reply_parts = []
    if success_lines:
        reply_parts.append("\n".join(["✅ ההעברה בוצעה:"] + success_lines))
    if below_minimum_msgs:
        reply_parts.append(
            "\n".join(["⛔ ההעברה לא בוצעה — הפריט טרם נפתר:"] + below_minimum_msgs)
        )
        reply_parts.append(
            "אפשר לענות שוב *כן* אחרי שתוסיף עוד מוצרים מהספק החלופי, או *לא* לוותר על הפריטים האלה."
        )
    validators.send_whatsapp_message(phone, "\n\n".join(reply_parts))
    return HttpResponse(status=200)
