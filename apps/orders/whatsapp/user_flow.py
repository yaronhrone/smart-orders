import json
import logging
from datetime import time as dtime
from decimal import Decimal

from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone

from .cache import (
    clear_pending_clarification,
    DecimalEncoder,
    get_pending_clarification,
    save_pending_clarification,
    save_pending_order,
)
from .delivery_flow import _handle_delivery_flow
from .fallback_flow import _handle_fallback_approval
from . import validators

logger = logging.getLogger(__name__)


def _format_scenario(label, s):
    lines = [f"*{label}*"]
    for p in s["products"]:
        lines.append(
            f"  • {p['product_name']} x{p['quantity']} {p.get('unit', '')} "
            f"— {p['supplier_name']} — {p['subtotal']}₪"
        )
    lines.append(f'סה"כ: {s["total_price"]}₪')
    return "\n".join(lines)


def _format_minimum_warning(issues: list) -> str:
    lines = ["⛔ מינימום הזמנה לא עומד:"]
    for issue in issues:
        lines.append(
            f"  • {issue['supplier_name']}: נדרש ₪{issue['minimum_required']}, "
            f"חסר ₪{Decimal(str(issue['missing_amount'])):.2f}"
        )
    return "\n".join(lines)


def _build_and_send_confirmed_order(data: dict, scenario: str) -> bool:
    """Build order in DB and send WhatsApp to each supplier. Returns True on success."""
    from django.contrib.auth import get_user_model
    from apps.catalog.models import Product
    from apps.orders.services import build_order
    from .supplier_flow import notify_suppliers_for_order

    user_id = data.get("user_id")
    raw_products = data.get("products")
    region = data.get("region")

    if not user_id or not raw_products or not region:
        return False

    User = get_user_model()
    try:
        user = User.objects.get(id=user_id)
        products = [
            {
                "product": Product.objects.get(id=p["product_id"]),
                "quantity": Decimal(p["quantity"]),
            }
            for p in raw_products
        ]
        order, _ = build_order(user, region, products, scenario=scenario)
        notify_suppliers_for_order(order)
        return True
    except Exception as exc:
        logger.error("Failed to build/send confirmed order for user %s: %s", user_id, exc)
        return False


def _resolve_profile(phone: str):
    """Look up a customer Profile by phone; try +972XXXXXXXXX and 0XXXXXXXXX."""
    from apps.users.models import Profile

    profile = Profile.objects.filter(phone=phone).select_related("user").first()
    if not profile and phone.startswith("+972"):
        profile = Profile.objects.filter(phone="0" + phone[4:]).select_related("user").first()
    return profile


def _handle_ambiguous_products(
    phone: str, ambiguous: list, resolved: list, extra: dict = None
) -> HttpResponse:
    """
    Ask which specific product was meant instead of guessing or handing an
    underspecified name to the AI. `resolved` (already-clear items from the
    same message) rides along in the cache so confirming later doesn't lose
    them. `extra` carries modification context (order id, intent, region)
    when this is a change to an existing order rather than a fresh one —
    see save_pending_clarification.
    """
    lines = ["איזה בדיוק? יש לנו כמה סוגים:"]
    for item in ambiguous:
        options = ", ".join(c[len(item["query"]):].strip() for c in item["candidates"])
        lines.append(f"  • {item['query']} — {options}")
    lines.append("\nענה עם הסוג (למשל: אדום).")
    save_pending_clarification(phone, resolved, ambiguous, extra=extra)
    validators.send_whatsapp_message(phone, "\n".join(lines))
    return HttpResponse(status=200)


def _handle_clarification_reply(phone: str, body: str, raw_state: str) -> HttpResponse:
    from apps.catalog.product_matcher import resolve_clarification

    state = json.loads(raw_state)
    newly_resolved, still_ambiguous = resolve_clarification(body, state["ambiguous"])
    # DecimalEncoder made quantities JSON-safe (str) going into the cache;
    # nothing decodes them back on the way out, so every quantity here is
    # currently a plain string — restore Decimal before it reaches pricing.
    all_resolved = [
        {"product_name": item["product_name"], "quantity": Decimal(str(item["quantity"]))}
        for item in state["resolved_items"] + newly_resolved
    ]
    extra = {
        k: state[k] for k in ("context", "order_id", "intent", "region") if k in state
    }

    if still_ambiguous:
        # Partial or unrecognized answer — clear first so re-asking doesn't
        # layer state from two different rounds on top of each other.
        clear_pending_clarification(phone)
        return _handle_ambiguous_products(phone, still_ambiguous, all_resolved, extra=extra)

    clear_pending_clarification(phone)

    if extra.get("context") == "modification":
        return _complete_modification_after_clarification(phone, extra, all_resolved)

    profile = _resolve_profile(phone)
    if not profile:
        validators.send_whatsapp_message(phone, "מספר הטלפון שלך לא רשום במערכת. פנה למנהל.")
        return HttpResponse(status=200)
    return _suggest_and_respond(phone, profile.user, profile, all_resolved)


def _apply_single_modification(
    product, quantity: Decimal, intent: str, order, region: str,
    supplier_batches: dict, changes_made: list,
) -> bool:
    """
    Resolve one already-identified Product + quantity into an add/update on
    `order`, appending to `supplier_batches` (grouped by supplier, for one
    consolidated confirmation message each) and `changes_made` in place.
    Returns False if no supplier in the region carries the product at all.
    """
    from apps.catalog.models import SupplierProduct
    from apps.orders.models import OrderRequestProduct

    existing_orp = OrderRequestProduct.objects.filter(
        order_request=order, product=product
    ).select_related("supplier").first()

    # "update" a product that isn't actually in the order yet has no
    # existing_orp to update — treat it the same as "add" rather than
    # silently doing nothing for it.
    if intent == "update" and existing_orp:
        old_qty = existing_orp.quantity
        existing_orp.quantity = quantity
        existing_orp.save(update_fields=["quantity"])
        order.total_price = sum(p.quantity * p.unit_price for p in order.products.all())
        order.save(update_fields=["total_price"])
        supplier_batches[existing_orp.supplier].append({
            "orp_id": existing_orp.id, "product_name": product.name,
            "quantity": str(quantity), "unit": product.get_unit_display(),
            "line": f"🔄 {product.name}: {old_qty} → {quantity} {product.get_unit_display()}",
        })
        changes_made.append(
            f"עודכן: {product.name} {old_qty}→{quantity} {product.get_unit_display()}"
        )
        return True

    sp = (
        SupplierProduct.objects
        .filter(product=product, supplier__region=region)
        .select_related("supplier")
        .order_by("price_per_unit")
        .first()
    )
    if not sp:
        return False

    orp, created = OrderRequestProduct.objects.get_or_create(
        order_request=order, product=product, supplier=sp.supplier,
        defaults={"quantity": quantity, "unit_price": sp.price_per_unit},
    )
    if not created:
        orp.quantity += quantity
        orp.save(update_fields=["quantity"])
    order.total_price = sum(p.quantity * p.unit_price for p in order.products.all())
    order.save(update_fields=["total_price"])
    supplier_batches[orp.supplier].append({
        "orp_id": orp.id, "product_name": product.name,
        "quantity": str(orp.quantity), "unit": product.get_unit_display(),
        "line": f"➕ {product.name} x{quantity} {product.get_unit_display()}",
    })
    changes_made.append(f"נוסף: {product.name} x{quantity} {product.get_unit_display()}")
    return True


def _dispatch_modification_batches(supplier_batches: dict, order, company: str, address: str) -> None:
    """Send one consolidated confirmation message per supplier and register it as pending."""
    from .cache import save_supplier_pending_order

    for supplier, batch in supplier_batches.items():
        msg_lines = [f"📝 *{company}* עדכן הזמנה:"]
        msg_lines += [entry["line"] for entry in batch]
        if address:
            msg_lines.append(f"📍 {address}")
        msg_lines.append("\nאנא ענה *אישור* לאישור.")
        validators.send_whatsapp_message(supplier.whatsapp_number, "\n".join(msg_lines))

        save_supplier_pending_order(
            supplier_phone=supplier.whatsapp_number,
            order_request_id=order.id,
            products=[
                {
                    "orp_id": entry["orp_id"],
                    "product_name": entry["product_name"],
                    "quantity": entry["quantity"],
                    "unit": entry["unit"],
                }
                for entry in batch
            ],
        )


def _complete_modification_after_clarification(phone: str, extra: dict, resolved_items: list) -> HttpResponse:
    """
    The customer just answered "which one did you mean" for a change to an
    existing order — finish applying it the same way _handle_order_
    modification would have, had the name been unambiguous from the start.
    """
    from collections import defaultdict
    from apps.catalog.models import Product
    from apps.orders.models import OrderRequest

    try:
        order = OrderRequest.objects.get(id=extra["order_id"])
    except OrderRequest.DoesNotExist:
        validators.send_whatsapp_message(phone, "לא מצאתי את ההזמנה. נסה שוב.")
        return HttpResponse(status=200)

    intent = extra.get("intent", "add")
    region = extra.get("region", "center")
    profile = getattr(order.user, "profile", None)
    all_products_map = {p.name: p for p in Product.objects.all()}

    changes_made = []
    errors = []
    supplier_batches = defaultdict(list)

    for item in resolved_items:
        product = all_products_map.get(item["product_name"])
        if not product or not _apply_single_modification(
            product, item["quantity"], intent, order, region, supplier_batches, changes_made
        ):
            errors.append(item["product_name"])

    _dispatch_modification_batches(
        supplier_batches, order,
        profile.company_name if profile else "",
        profile.company_address if profile else "",
    )

    reply_lines = []
    if changes_made:
        reply_lines.append("✅ השינויים נשלחו לספקים:")
        reply_lines += [f"  • {c}" for c in changes_made]
    if errors:
        reply_lines.append(f"⚠️ לא נמצאו: {', '.join(errors)}")
    if not reply_lines:
        reply_lines = ["לא הצלחתי לזהות שינוי בהזמנה. נסה שוב."]
    validators.send_whatsapp_message(phone, "\n".join(reply_lines))
    return HttpResponse(status=200)


def _suggest_and_respond(phone: str, user, profile, parsed_items: list) -> HttpResponse:
    """
    Given fully-resolved {product_name, quantity} items (no ambiguity left to
    ask about), look them up, price the order, and send the scenario/
    confirmation message. Shared by a fresh order and by one whose product
    ambiguity was just cleared up.
    """
    from apps.catalog.models import Product
    from apps.orders.services import suggest_order

    all_products_map = {p.name: p for p in Product.objects.all()}
    products = []
    unrecognized = []
    for item in parsed_items:
        product = all_products_map.get(item["product_name"])
        if product:
            products.append({"product": product, "quantity": item["quantity"]})
        else:
            unrecognized.append(item["product_name"])

    if not products:
        validators.send_whatsapp_message(
            phone,
            f"לא זיהיתי מוצרים ידועים בהזמנה.\nלא זוהה: {', '.join(unrecognized)}",
        )
        return HttpResponse(status=200)

    try:
        result = suggest_order(user=user, region=profile.region, products=products)
    except ValueError as exc:
        validators.send_whatsapp_message(phone, f"שגיאה בעיבוד ההזמנה: {exc}")
        return HttpResponse(status=200)

    cheapest = result["cheapest"]
    fewest = result["fewest_suppliers"]
    minimum_issues = result.get("minimum_issues", {})
    cheapest_issues = minimum_issues.get("cheapest", [])
    fewest_issues = minimum_issues.get("fewest_suppliers", [])
    same = cheapest["total_price"] == fewest["total_price"]

    pending_kwargs = dict(
        products=[{"product_id": p["product"].id, "quantity": str(p["quantity"])} for p in products],
        user_id=user.id,
        region=profile.region,
        minimum_issues=minimum_issues,
    )

    if same or (not cheapest_issues and not fewest_issues):
        # Either there's only one real scenario, or both pass the minimum —
        # offer the choice (or the single option) exactly as before.
        save_pending_order(phone, cheapest, fewest, **pending_kwargs)
        if same:
            msg = _format_scenario("ההזמנה שלך", cheapest)
            msg += "\n\nענה *אישור* לאישור."
        else:
            msg = (
                _format_scenario("אפשרות א׳ — הזול ביותר", cheapest)
                + "\n\n"
                + _format_scenario("אפשרות ב׳ — הכי פחות ספקים", fewest)
                + "\n\nענה *א* לאפשרות הזולה יותר, *ב* לאפשרות עם פחות ספקים."
            )
    elif cheapest_issues and fewest_issues:
        # Neither scenario clears its suppliers' minimums — there is nothing
        # valid to offer. Show both shortfalls so the customer knows which is
        # closer, but don't cache anything to confirm into.
        msg = (
            "⛔ אף אחת מהאפשרויות לא עומדת במינימום הזמנה של הספקים:\n\n"
            + _format_scenario("אפשרות א׳ — הזול ביותר", cheapest)
            + "\n" + _format_minimum_warning(cheapest_issues)
            + "\n\n" + _format_scenario("אפשרות ב׳ — הכי פחות ספקים", fewest)
            + "\n" + _format_minimum_warning(fewest_issues)
            + "\n\nשלח הזמנה מחודשת עם כמויות גדולות יותר."
        )
    else:
        # Exactly one scenario is actually orderable — offer only that one,
        # instead of letting the customer pick a dead end and then, on
        # confirming, lose the whole pending order with no way back to the
        # option that would have worked.
        valid_scenario, valid_label, broken_issues, broken_label = (
            ("cheapest", "הזול ביותר", fewest_issues, "האפשרות עם פחות ספקים")
            if fewest_issues
            else ("fewest_suppliers", "עם הכי פחות ספקים", cheapest_issues, "האפשרות הזולה ביותר")
        )
        chosen = cheapest if valid_scenario == "cheapest" else fewest
        save_pending_order(phone, cheapest, fewest, single_scenario=valid_scenario, **pending_kwargs)
        msg = _format_scenario(f"ההזמנה שלך — {valid_label}", chosen)
        msg += (
            f"\n\n({broken_label} לא עומדת במינימום הזמנה של "
            f"{broken_issues[0]['supplier_name']} — חסר ₪{Decimal(str(broken_issues[0]['missing_amount'])):.2f})"
        )
        msg += "\n\nענה *אישור* לאישור."

    if unrecognized:
        msg += f"\n\n⚠️ לא זוהה: {', '.join(unrecognized)}"

    validators.send_whatsapp_message(phone, msg)
    return HttpResponse(status=200)


def _handle_new_order(phone: str, body: str) -> HttpResponse:
    """Handle an incoming message that has no pending order — treat as a new customer order."""
    from apps.catalog.models import Product
    from apps.orders.order_parser import AmbiguousProductError, parse_customer_order

    profile = _resolve_profile(phone)
    if not profile:
        validators.send_whatsapp_message(phone, "מספר הטלפון שלך לא רשום במערכת. פנה למנהל.")
        return HttpResponse(status=200)

    product_names = list(Product.objects.values_list("name", flat=True))

    try:
        parsed_items = parse_customer_order(body, product_names)
    except AmbiguousProductError as exc:
        return _handle_ambiguous_products(phone, exc.ambiguous, exc.resolved)
    except ValueError:
        validators.send_whatsapp_message(
            phone,
            "לא הצלחתי להבין את ההזמנה.\nנסה לשלוח כגון: 5 קילו עגבניות, 10 קילו גזר",
        )
        return HttpResponse(status=200)

    return _suggest_and_respond(phone, profile.user, profile, parsed_items)


def _handle_order_modification(phone: str, body: str, user, order) -> HttpResponse:
    """Handle ADD or UPDATE modification(s) to a SENT order."""
    from collections import defaultdict
    from apps.catalog.models import Product
    from apps.catalog.product_matcher import find_ambiguous_group
    from apps.orders.models import OrderRequestProduct
    from apps.orders.order_parser import parse_modification_intent

    product_names = list(Product.objects.values_list("name", flat=True))
    parsed = parse_modification_intent(body, product_names)
    intent = parsed["intent"]
    items = parsed["items"]

    if intent == "none" or not items:
        return _handle_new_order(phone, body)

    profile = getattr(user, "profile", None)
    region = profile.region if profile else "center"
    company = profile.company_name if profile else ""
    address = profile.company_address if profile else ""

    changes_made = []
    errors = []
    ambiguous = []
    # One batch per supplier: several changes in the same message (or several
    # messages before the supplier gets around to replying) used to fire one
    # standalone "please confirm" per item, and the supplier's single "אישור"
    # could only ever confirm the last one — worse, *none* of these ever
    # registered as pending, so "אישור" matched nothing and fell through to
    # the free-text price-update parser ("המוצר 'אישור' לא קיים בקטלוג").
    # Collect everything first, then send one consolidated message per
    # supplier and register it as pending, same contract as a fresh order.
    supplier_batches = defaultdict(list)

    for item in items:
        product = Product.objects.filter(name=item["product_name"]).first()
        if not product:
            # "בצל" alone isn't a catalog product — only "בצל יבש"/"בצל
            # סגול"/... are. Ask which one instead of just reporting it as
            # not found, same as a fresh order already does.
            group = find_ambiguous_group(item["product_name"], product_names)
            if group:
                ambiguous.append({
                    "query": item["product_name"], "quantity": item["quantity"], "candidates": group,
                })
            else:
                errors.append(item["product_name"])
            continue

        # Check cutoff for the supplier handling this product
        existing_orp = OrderRequestProduct.objects.filter(
            order_request=order, product=product
        ).select_related("supplier").first()

        if existing_orp:
            cutoff_key = f"supplier_cutoff:{existing_orp.supplier.whatsapp_number}:{order.id}"
            cutoff = cache.get(cutoff_key)
            if cutoff:
                now = timezone.localtime().time()
                cutoff_time = dtime(*map(int, cutoff.split(":")))
                if now > cutoff_time:
                    validators.send_whatsapp_message(
                        phone,
                        f"⛔ לא ניתן לשנות את {product.name} — "
                        f"{existing_orp.supplier.name} קבע שעת הגבלה עד {cutoff}.",
                    )
                    continue

        if not _apply_single_modification(
            product, item["quantity"], intent, order, region, supplier_batches, changes_made
        ):
            errors.append(product.name)

    if ambiguous:
        # Anything else in the same message already resolved cleanly and, for
        # a modification, is already committed to the DB (unlike a fresh
        # order, which only computes everything after the whole message is
        # understood) — dispatch those now rather than holding them hostage
        # to the one ambiguous item.
        if supplier_batches:
            _dispatch_modification_batches(supplier_batches, order, company, address)
        if changes_made:
            validators.send_whatsapp_message(
                phone,
                "✅ השינויים הבאים נשלחו לספקים:\n" + "\n".join(f"  • {c}" for c in changes_made),
            )
        return _handle_ambiguous_products(
            phone, ambiguous, [],
            extra={"context": "modification", "order_id": order.id, "intent": intent, "region": region},
        )

    if not changes_made and not errors:
        validators.send_whatsapp_message(phone, "לא הצלחתי לזהות שינוי בהזמנה. נסה שוב.")
        return HttpResponse(status=200)

    _dispatch_modification_batches(supplier_batches, order, company, address)

    reply_lines = []
    if changes_made:
        reply_lines.append(f"✅ השינויים נשלחו לספקים:")
        reply_lines += [f"  • {c}" for c in changes_made]
    if errors:
        reply_lines.append(f"⚠️ לא נמצאו: {', '.join(errors)}")

    validators.send_whatsapp_message(phone, "\n".join(reply_lines))
    return HttpResponse(status=200)


def _handle_user_flow(phone: str, body: str) -> HttpResponse:
    from apps.orders.models import OrderRequest

    delivery_response = _handle_delivery_flow(phone, body)
    if delivery_response is not None:
        return delivery_response

    fallback_response = _handle_fallback_approval(phone, body)
    if fallback_response is not None:
        return fallback_response

    clarify_raw = get_pending_clarification(phone)
    if clarify_raw:
        return _handle_clarification_reply(phone, body, clarify_raw)

    key = f"whatsapp_order:{phone}"
    raw = cache.get(key)

    if not raw:
        # Check if user has a SENT order (awaiting supplier confirmation) → offer modification
        profile = _resolve_profile(phone)

        if profile:
            sent_order = (
                OrderRequest.objects
                .filter(user=profile.user, status=OrderRequest.Status.SENT)
                .order_by("-created_at")
                .first()
            )
            if sent_order:
                return _handle_order_modification(phone, body, profile.user, sent_order)

        return _handle_new_order(phone, body)

    data = json.loads(raw)
    cheapest = data["cheapest"]
    fewest = data["fewest"]

    same = cheapest["total_price"] == fewest["total_price"]
    single_scenario = data.get("single_scenario")

    if single_scenario:
        # Only one scenario was ever offered (the other failed a supplier
        # minimum) — any reply confirms it, same as the same-price shortcut.
        chosen = cheapest if single_scenario == "cheapest" else fewest
        label = "ההזמנה"
        scenario = single_scenario
    elif same or body in ("א", "1"):
        chosen = cheapest
        label = "אפשרות א׳ — הזול ביותר" if not same else "ההזמנה"
        scenario = "cheapest"
    elif body in ("ב", "2"):
        chosen = fewest
        label = "אפשרות ב׳ — הכי פחות ספקים"
        scenario = "fewest_suppliers"
    else:
        validators.send_whatsapp_message(phone, "אנא ענה *א* או *ב* כדי לבחור.")
        return HttpResponse(status=200)

    minimum_issues = data.get("minimum_issues", {})
    scenario_issues = minimum_issues.get(scenario, [])
    if scenario_issues:
        # Clear the pending order so the customer's next message (a renewed,
        # larger order — as this warning instructs them to send) is parsed as
        # a fresh order by _handle_new_order, instead of being reinterpreted
        # as a stale א/ב scenario choice against the old (too-small) totals.
        cache.delete(key)
        msg = _format_minimum_warning(scenario_issues)
        msg += "\n\nשלח הזמנה מחודשת עם כמויות גדולות יותר כדי לעמוד במינימום."
        validators.send_whatsapp_message(phone, msg)
        return HttpResponse(status=200)

    cache.delete(key)
    success = _build_and_send_confirmed_order(data, scenario)

    if success:
        confirm = _format_scenario(f"✅ אושר! {label}", chosen)
        confirm += "\n\nההזמנה נשלחה לספקים."
    else:
        confirm = "❌ אירעה שגיאה בעיבוד ההזמנה. אנא נסה שנית או פנה לתמיכה."
    validators.send_whatsapp_message(phone, confirm)

    return HttpResponse(status=200)
