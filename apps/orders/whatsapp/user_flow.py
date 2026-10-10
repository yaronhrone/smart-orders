import json
import logging
from datetime import time as dtime
from decimal import Decimal

from django.core.cache import cache
from django.http import HttpResponse
from django.utils import timezone

from .cache import (
    clear_merge_offer,
    clear_pending_clarification,
    DecimalEncoder,
    get_merge_offer,
    get_pending_clarification,
    save_merge_offer,
    MINIMUM_GRACE_SECONDS,
    MINIMUM_GRACE_TTL,
    save_draft_order,
    save_pending_clarification,
    save_pending_order,
    SESSION_TTL,
)
from .delivery_flow import _handle_delivery_flow
from .fallback_flow import _handle_fallback_approval, _handle_reroute_grace_topup
from apps.orders.services import NOT_ADDED_REASONS
from . import validators

logger = logging.getLogger(__name__)

# A bare "yes" with no offer waiting means the offer expired (SESSION_TTL) or
# never existed. Better to say so than to parse "אישור" as a new order.
_CONFIRM_WORDS = {"אישור", "אשר", "מאשר", "אני מאשר", "כן", "אוקי", "אוקיי", "ok", "okay"}
_DECLINE_WORDS = {"לא", "לא תודה", "no", "ביטול", "בטל"}


def _offer_validity_text(seconds: int) -> str:
    if seconds == 3600:
        return "לשעה"
    if seconds == 7200:
        return "לשעתיים"
    return f"ל-{seconds // 60} דקות"


def _normalized_reply(body: str) -> str:
    return body.strip().strip("!.").strip().casefold()


def _is_bare_confirmation(body: str) -> bool:
    return _normalized_reply(body) in _CONFIRM_WORDS


def _offer_merge(phone: str, user, batch, products: list, region: str, note: str = "") -> None:
    """
    The customer already has an open order today and just tried to start
    another one: ask before adding anything ("לא" adds nothing at all).
    products: [{"product": Product, "quantity": Decimal}].
    """
    from apps.orders.models import OrderRequest, OrderRequestProduct

    save_merge_offer(
        phone, batch.id, user.id, region,
        [{"product_id": p["product"].id, "quantity": str(p["quantity"])} for p in products],
    )
    lines = ["📦 יש לך כבר הזמנה פתוחה מהיום:"]
    existing = (
        OrderRequestProduct.objects
        .filter(order_request__batch=batch)
        .exclude(order_request__status=OrderRequest.Status.CANCELLED)
        .select_related("product", "supplier")
        .order_by("order_request_id", "id")
    )
    for item in existing:
        lines.append(
            f"  • {item.product.name} x{item.quantity} {item.product.get_unit_display()} — {item.supplier.name}"
        )
    lines.append("\nלהוסיף אליה גם:")
    for p in products:
        lines.append(f"  • {p['product'].name} x{p['quantity']} {p['product'].get_unit_display()}")
    if note:
        lines.append(f"\n{note}")
    lines.append("\nענה *כן* להוספה או *לא* כדי לא להוסיף כלום. אפשר להוסיף עד 23:00.")
    validators.send_whatsapp_message(phone, "\n".join(lines))


def _handle_merge_offer_reply(phone: str, body: str):
    """Answer to _offer_merge's question. Returns None when no such question is waiting."""
    from django.contrib.auth import get_user_model
    from apps.catalog.models import Product
    from apps.orders.services import add_items_to_batch, checkout_lock, get_open_batch
    from .supplier_flow import notify_supplier_of_items

    offer = get_merge_offer(phone)
    if not offer:
        return None

    reply = _normalized_reply(body)
    if reply in _DECLINE_WORDS:
        clear_merge_offer(phone)
        validators.send_whatsapp_message(phone, "בסדר, לא הוספנו כלום. ההזמנה הפתוחה נשארת כמו שהיא.")
        return HttpResponse(status=200)
    if reply not in _CONFIRM_WORDS:
        validators.send_whatsapp_message(phone, "ענה *כן* כדי להוסיף להזמנה הפתוחה, או *לא* כדי לא להוסיף כלום.")
        return HttpResponse(status=200)

    clear_merge_offer(phone)
    user = get_user_model().objects.filter(id=offer["user_id"]).first()
    products_by_id = Product.objects.in_bulk([i["product_id"] for i in offer["items"]])
    items = [
        {"product": products_by_id[i["product_id"]], "quantity": Decimal(i["quantity"])}
        for i in offer["items"] if i["product_id"] in products_by_id
    ]

    batch = None
    if user:
        with checkout_lock(user):
            batch = get_open_batch(user)
            if batch is not None and batch.id == offer["batch_id"]:
                changes, not_added = add_items_to_batch(batch, items, offer["region"])
            else:
                batch = None
    if batch is None:
        validators.send_whatsapp_message(
            phone,
            "⏰ ההזמנה הפתוחה כבר נסגרה להוספות (אפשר להוסיף עד 23:00). שלח את המוצרים מחדש כהזמנה חדשה.",
        )
        return HttpResponse(status=200)

    for change in changes:
        notify_supplier_of_items(change["order"], change["items"], created=change["created"])
    validators.send_whatsapp_message(phone, _format_additions(changes, not_added))
    return HttpResponse(status=200)


def _format_additions(changes: list, not_added: list) -> str:
    lines = []
    if changes:
        lines.append("📨 התוספת נשלחה לספקים לאישור; נעדכן אותך כשהספק יאשר:")
        for change in changes:
            for item in change["items"]:
                lines.append(
                    f"  • {item.product.name} x{item.quantity} {item.product.get_unit_display()} "
                    f"— {item.supplier.name} (הזמנה #{change['order'].id})"
                )
    if not_added:
        if lines:
            lines.append("")
        lines.append("⚠️ לא נוספו:")
        for miss in not_added:
            lines.append(f"  • {miss['product'].name} — {NOT_ADDED_REASONS.get(miss['reason'], miss['reason'])}")
    return "\n".join(lines) or "לא נוסף שום מוצר."


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


MERGE_OFFERED = "merge_offered"


def _build_and_send_confirmed_order(data: dict, scenario: str, phone: str = None):
    """
    Build the checkout in DB (one order per supplier, one OrderBatch) and
    send each supplier its order. Returns the list of created orders, None
    on failure, or MERGE_OFFERED when the customer already has an open order
    today (e.g. just placed on the site) and was asked whether to add to it.
    """
    from django.contrib.auth import get_user_model
    from apps.catalog.models import Product
    from apps.orders.services import build_order, checkout_lock, get_open_batch
    from .supplier_flow import notify_suppliers_for_batch

    user_id = data.get("user_id")
    raw_products = data.get("products")
    region = data.get("region")

    if not user_id or not raw_products or not region:
        return None

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
        with checkout_lock(user):
            open_batch = get_open_batch(user)
            if open_batch is None:
                _batch, orders, _links = build_order(user, region, products, scenario=scenario)
        if open_batch is not None:
            _offer_merge(phone, user, open_batch, products, region)
            return MERGE_OFFERED
        notify_suppliers_for_batch(orders)
        return orders
    except Exception as exc:
        logger.error("Failed to build/send confirmed order for user %s: %s", user_id, exc)
        return None


def notify_customer_of_checkout(user, orders) -> None:
    """
    WhatsApp the customer the checkout they just placed on the site — each
    supplier's order number and total. (A WhatsApp checkout already gets
    this in its "✅ אושר!" reply.) Silently skipped without a phone.
    """
    profile = getattr(user, "profile", None)
    if not profile or not profile.phone:
        return
    lines = ["✅ ההזמנה שלך התקבלה ונשלחה לספקים:"]
    total = Decimal(0)
    for order in orders:
        lines.append(f"  • הזמנה #{order.id} — {order.supplier.name} — {order.total_price:.2f}₪")
        total += order.total_price
    if len(orders) > 1:
        lines.append(f'\nסה"כ: {total:.2f}₪ ({len(orders)} ספקים, הזמנה נפרדת לכל ספק)')
    lines.append("\nנעדכן אותך כשכל ספק מאשר.")
    try:
        validators.send_whatsapp_message(validators._local_to_e164(profile.phone), "\n".join(lines))
    except Exception as exc:
        logger.error("Failed to WhatsApp checkout confirmation to user %s: %s", user.id, exc)


def notify_customer_of_additions(user, changes: list, not_added: list) -> None:
    """WhatsApp the customer what a site checkout added to their open order. Skipped without a phone."""
    profile = getattr(user, "profile", None)
    if not profile or not profile.phone:
        return
    try:
        validators.send_whatsapp_message(
            validators._local_to_e164(profile.phone), _format_additions(changes, not_added),
        )
    except Exception as exc:
        logger.error("Failed to WhatsApp order additions to user %s: %s", user.id, exc)


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
        k: state[k] for k in ("context", "batch_id", "intent", "region") if k in state
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
    product, quantity: Decimal, intent: str, batch, region: str,
    order_changes: dict, changes_made: list, blocked: list,
) -> str:
    """
    Apply one customer request about one product to the open checkout `batch`.
    Everything is expressed as a target total of that product in the whole
    checkout (all suppliers, approved or not):

    - "add"    -> total + quantity        ("תוסיף 20", "עוד 20", "גם 20")
    - "update" -> quantity                ("תעדכן ל-50", "במקום 30 תביא 50")
    - "reduce" -> total - quantity        ("תוריד 10", "הקטן ב-10")

    More than today: the difference is added (to the supplier already holding
    the product, else one already on the order, else the cheapest that clears
    its minimum), even if that supplier already approved: it goes in its own
    order, because an approved order isn't silently re-opened. Less than
    today: taken out even after approval and the supplier is just told, unless
    that would push the supplier under its minimum order, in which case
    nothing changes and the customer is told how much more is needed.

    Appends to `order_changes` (one consolidated supplier message per order),
    `changes_made` (what the customer is told) and `blocked` (what couldn't be
    done, and why). Returns "ok", "not_found" (no supplier in the region
    carries it) or "blocked".
    """
    from apps.orders.models import OrderRequest, OrderRequestProduct, SupplierConfirmation
    from apps.orders.services import (
        addition_shortfall, get_or_create_supplier_order, pick_supplier_for_addition,
        product_total_in_batch, reduce_product_in_batch,
    )
    from .supplier_flow import fmt_qty, supplier_total_note

    amount = Decimal(str(quantity))
    current = product_total_in_batch(batch, product)
    unit = product.get_unit_display()
    if intent == "reduce":
        if current <= 0:
            blocked.append(f"{product.name}: אין כרגע בהזמנה")
            return "blocked"
        target = max(current - amount, Decimal(0))
    elif intent == "update":
        target = amount
    else:
        target = current + amount
    delta = target - current

    if delta == 0:
        blocked.append(f"{product.name}: כבר {fmt_qty(current)} {unit} בהזמנה, אין שינוי")
        return "blocked"

    if delta < 0:
        changes, problem = reduce_product_in_batch(batch, product, -delta)
        if problem:
            blocked.append(_describe_reduction_problem(product, problem))
            return "blocked"
        for change in changes:
            order = change["order"]
            for line in change["lines"]:
                _record_order_change(order_changes, order, created=False, line=(
                    f"🔽 {product.name}: {fmt_qty(line['old'])} → {fmt_qty(line['new'])} {unit}"
                ), needs_confirmation=change["needs_confirmation"])
            changes_made.append(
                f"הופחת: {product.name} {fmt_qty(current)}→{fmt_qty(target)} {unit} "
                f"({change['lines'][0]['supplier'].name}, הזמנה #{order.id})"
            )
        return "ok"

    supplier, price, reason = pick_supplier_for_addition(batch, product, delta, region)
    if supplier is None:
        if reason != "below_minimum":
            return "not_found"
        shortfall = addition_shortfall(batch, product, delta, region)
        if shortfall:
            blocked.append(
                f"{product.name}: אצל {shortfall[0].name} המינימום להזמנה הוא ₪{shortfall[0].minimum_order:.2f}. "
                f"חסר עוד ₪{shortfall[1]:.2f}. אפשר להוסיף עוד מוצרים בסכום הזה ולשלוח שוב"
            )
        else:
            blocked.append(f"{product.name}: מתחת למינימום של הספק")
        return "blocked"

    order, created = get_or_create_supplier_order(batch, supplier)
    orp, item_created = OrderRequestProduct.objects.get_or_create(
        order_request=order, product=product, supplier=supplier,
        defaults={"quantity": delta, "unit_price": price},
    )
    if not item_created:
        orp.quantity += delta
        orp.save(update_fields=["quantity"])
        SupplierConfirmation.objects.filter(order_request_product=orp).delete()
    _recalc_order_total(order)
    _record_order_change(order_changes, order, created=created, line=(
        f"➕ {product.name} x{fmt_qty(delta)} {unit}{supplier_total_note(order, product, delta)}"
    ))
    if intent == "update" and current > 0:
        changes_made.append(
            f"עודכן: {product.name} {fmt_qty(current)}→{fmt_qty(target)} {unit}, נוספו {fmt_qty(delta)} "
            f"({supplier.name}, הזמנה #{order.id})"
        )
    else:
        changes_made.append(f"נוסף: {product.name} x{fmt_qty(delta)} {unit} ({supplier.name}, הזמנה #{order.id})")
    return "ok"


def _describe_reduction_problem(product, problem: dict) -> str:
    reason = problem["reason"]
    if reason == "below_minimum":
        return (
            f"{product.name} לא הופחת: ההזמנה אצל {problem['supplier'].name} תרד ל-₪{problem['after']:.2f}, "
            f"מתחת למינימום של ₪{problem['minimum']:.2f}. חסר עוד ₪{problem['missing']:.2f}. "
            "אפשר להוסיף עוד מוצרים בסכום הזה (או יותר) ולנסות שוב"
        )
    if reason == "would_empty":
        return (
            f"{product.name}: ההפחתה תרוקן לגמרי את ההזמנה אצל {problem['supplier'].name}. "
            "לביטול הזמנה שלמה פנה אלינו"
        )
    return f"{product.name}: אין כרגע בהזמנה"


def _recalc_order_total(order) -> None:
    order.total_price = sum(p.quantity * p.unit_price for p in order.products.all())
    order.save(update_fields=["total_price"])


def _record_order_change(
    order_changes: dict, order, created: bool, line: str, needs_confirmation: bool = True,
) -> None:
    entry = order_changes.setdefault(
        order.id, {"order": order, "created": created, "lines": [], "needs_confirmation": False},
    )
    entry["created"] = entry["created"] or created
    entry["needs_confirmation"] = entry["needs_confirmation"] or needs_confirmation
    entry["lines"].append(line)


def _dispatch_modification_batches(order_changes: dict, company: str, address: str) -> None:
    """
    One consolidated message per changed order (= per supplier). When
    something in it still needs the supplier's answer, everything still
    unconfirmed in that order is re-registered as pending (not just the
    changed lines, or the supplier's "אישור" would leave the rest of the order
    unconfirmed forever). A pure reduction of what the supplier already
    approved only informs it: no question, and its pending reply (one slot per
    phone, maybe for another order) is left alone.
    """
    from apps.orders.models import OrderRequest
    from .cache import clear_supplier_pending_for_order
    from .supplier_flow import _save_pending_for_order, followup_header

    for entry in order_changes.values():
        order = entry["order"]
        if order.status == OrderRequest.Status.CANCELLED:
            # Everything in it was reduced away (an addition the customer took back).
            validators.send_whatsapp_message(
                order.supplier.whatsapp_number,
                f"❌ *{company}* ביטל את הזמנה #{order.id}. אין צורך לספק אותה.",
            )
            clear_supplier_pending_for_order(order.supplier.whatsapp_number, order.id)
            continue
        needs_confirmation = entry["needs_confirmation"]
        if entry["created"]:
            msg_lines = [followup_header(order, company) or f"שלום, *{company}* מבקש להזמין (הזמנה #{order.id}):"]
        else:
            msg_lines = [f"📝 *{company}* עדכן הזמנה #{order.id}:"]
        msg_lines += entry["lines"]
        if needs_confirmation:
            if address:
                msg_lines.append(f"📍 {address}")
            msg_lines.append("\nאנא ענה *אישור* לאישור.")
        else:
            msg_lines.append("\nאין צורך באישור נוסף. זו הקטנה של מה שכבר אישרת.")
        validators.send_whatsapp_message(order.supplier.whatsapp_number, "\n".join(msg_lines))
        if needs_confirmation:
            _save_pending_for_order(order)


def _modification_header(order_changes: dict) -> str:
    """How the customer is told their changes went out."""
    if any(entry["needs_confirmation"] for entry in order_changes.values()):
        return "📨 השינויים נשלחו לספקים לאישור; נעדכן אותך כשהספק יאשר:"
    return "📨 עדכנו את הספקים:"


def _complete_modification_after_clarification(phone: str, extra: dict, resolved_items: list) -> HttpResponse:
    """
    The customer just answered "which one did you mean" for a change to an
    existing order — finish applying it the same way _handle_order_
    modification would have, had the name been unambiguous from the start.
    """
    from apps.catalog.models import Product
    from apps.orders.models import OrderBatch

    try:
        batch = OrderBatch.objects.select_related("user__profile").get(id=extra["batch_id"])
    except (OrderBatch.DoesNotExist, KeyError):
        validators.send_whatsapp_message(phone, "לא מצאתי את ההזמנה. נסה שוב.")
        return HttpResponse(status=200)

    intent = extra.get("intent", "add")
    region = extra.get("region", "center")
    profile = getattr(batch.user, "profile", None)
    all_products_map = {p.name: p for p in Product.objects.all()}

    changes_made = []
    errors = []
    blocked = []
    order_changes = {}

    for item in resolved_items:
        product = all_products_map.get(item["product_name"])
        result = (
            _apply_single_modification(
                product, item["quantity"], item.get("intent") or intent, batch, region,
                order_changes, changes_made, blocked,
            )
            if product else "not_found"
        )
        if result == "not_found":
            errors.append(item["product_name"])

    _dispatch_modification_batches(
        order_changes,
        profile.company_name if profile else "",
        profile.company_address if profile else "",
    )

    reply_lines = []
    if changes_made:
        reply_lines.append(_modification_header(order_changes))
        reply_lines += [f"  • {c}" for c in changes_made]
    if errors:
        reply_lines.append(f"⚠️ לא נמצאו: {', '.join(errors)}")
    if blocked:
        reply_lines.append("⚠️ לא בוצע:")
        reply_lines += [f"  • {b}" for b in blocked]
    if not reply_lines:
        reply_lines = ["לא הצלחתי לזהות שינוי בהזמנה. נסה שוב."]
    validators.send_whatsapp_message(phone, "\n".join(reply_lines))
    return HttpResponse(status=200)


def _suggest_and_respond(
    phone: str, user, profile, parsed_items: list, is_grace_retry: bool = False
) -> HttpResponse:
    """
    Given fully-resolved {product_name, quantity} items (no ambiguity left to
    ask about), look them up, price the order, and send the scenario/
    confirmation message. Shared by a fresh order and by one whose product
    ambiguity was just cleared up.

    `is_grace_retry` marks the one re-entry that isn't a customer message: the
    MINIMUM_GRACE_SECONDS follow-up scheduled after a basket first failed
    every supplier's minimum (see the cheapest_issues/fewest_issues branch
    below). It decides what happens if the basket *still* doesn't clear a
    minimum — start a fresh grace window (first time) vs. give up and drop it
    (second time), so a customer who never tops up doesn't get held forever.
    """
    from apps.catalog.models import Product
    from apps.orders.services import get_open_batch, suggest_order

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

    # Ordered on the site (or confirmed another offer) while this message was
    # still in its debounce window: ask to add to that order, don't price a second one.
    open_batch = get_open_batch(user)
    if open_batch is not None:
        note = f"⚠️ לא זוהה: {', '.join(unrecognized)}" if unrecognized else ""
        _offer_merge(phone, user, open_batch, products, profile.region, note=note)
        return HttpResponse(status=200)

    try:
        result = suggest_order(user=user, region=profile.region, products=products)
    except ValueError as exc:
        validators.send_whatsapp_message(phone, f"שגיאה בעיבוד ההזמנה: {exc}")
        return HttpResponse(status=200)

    cheapest = result["cheapest"]
    fewest = result["fewest_suppliers"]
    minimum_issues = result.get("minimum_issues", {})
    unavailable_products = result.get("unavailable_products", [])
    cheapest_issues = minimum_issues.get("cheapest", [])
    # One option only: the cheapest scenario whose suppliers all clear their
    # minimum (see services.pick_recommended_scenario). Offering both
    # "cheapest" and "fewest suppliers" and asking א/ב just made the customer
    # do the comparison the system can do for them.
    from apps.orders.services import pick_recommended_scenario
    recommended = pick_recommended_scenario(result)

    # A product with no supplier at all doesn't kill the rest of a perfectly
    # orderable basket — suggest_order already dropped it from both
    # scenarios; drop it here too so a later "אישור" doesn't try to build an
    # order against a product no supplier can actually fulfill.
    if unavailable_products:
        products = [p for p in products if p["product"].name not in unavailable_products]

    pending_kwargs = dict(
        products=[{"product_id": p["product"].id, "quantity": str(p["quantity"])} for p in products],
        user_id=user.id,
        region=profile.region,
        minimum_issues=minimum_issues,
    )

    if recommended:
        chosen = cheapest if recommended == "cheapest" else fewest
        save_pending_order(phone, cheapest, fewest, single_scenario=recommended, **pending_kwargs)
        msg = _format_scenario("ההזמנה שלך", chosen)
        msg += (
            "\n\nענה *אישור* לאישור. "
            f"ההצעה תקפה {_offer_validity_text(SESSION_TTL)}; אחרי זה צריך לשלוח את ההזמנה מחדש."
        )
    else:
        # Nothing clears the suppliers' minimums — nothing valid to offer yet.
        shortfalls = (
            "⛔ ההזמנה לא עומדת במינימום הזמנה של הספקים:\n\n"
            + _format_scenario("ההזמנה שלך", cheapest)
            + "\n" + _format_minimum_warning(cheapest_issues)
        )
        if is_grace_retry:
            # Already got one grace window and still doesn't clear a minimum —
            # stop holding it open. clear_draft_order isn't needed here: the
            # dispatch task that called us already cleared this generation's
            # draft before calling _suggest_and_respond.
            msg = shortfalls + "\n\nההזמנה בוטלה. שלח הזמנה חדשה כדי לנסות שוב."
        else:
            # Instead of dropping the basket and making the customer start
            # over, hold it as a draft for MINIMUM_GRACE_SECONDS — a follow-up
            # message ("גם 5 חסה") merges into it via the same debounce path a
            # brand-new order uses (save_draft_order), so topping up the
            # quantity or adding a product is enough to clear the minimum
            # without retyping everything already sent.
            generation = save_draft_order(
                phone,
                [{"product_name": p["product"].name, "quantity": p["quantity"]} for p in products],
                ttl=MINIMUM_GRACE_TTL,
            )
            _schedule_draft_dispatch(phone, generation, MINIMUM_GRACE_SECONDS, is_grace_retry=True)
            hours = MINIMUM_GRACE_SECONDS // 3600
            msg = (
                shortfalls
                + f"\n\nההזמנה נשמרת — יש לך {hours} שעות להוסיף עוד מוצרים או כמות כדי "
                "לעמוד במינימום (שלח הודעה נוספת, היא תתווסף להזמנה זו). "
                "אם לא תעדכן, ההזמנה תבוטל אוטומטית."
            )

    if unrecognized:
        msg += f"\n\n⚠️ לא זוהה: {', '.join(unrecognized)}"
    if unavailable_products:
        msg += f"\n\n⚠️ אין ספק שיכול לספק: {', '.join(unavailable_products)} (לא נכלל בהזמנה)"

    validators.send_whatsapp_message(phone, msg)
    return HttpResponse(status=200)


def _handle_new_order(phone: str, body: str) -> HttpResponse:
    """
    Handle an incoming message that has no pending order — treat as a new
    customer order. Doesn't price or offer anything itself: merges the
    parsed items into a short-lived draft and (re)schedules the debounced
    dispatch, so "20 עגבנייה" followed a moment later by "גם 5 חסה" become
    one order instead of the second message landing on an already-cached
    scenario-choice and getting silently dropped.
    """
    from apps.catalog.models import Product
    from apps.orders.order_parser import AmbiguousProductError, parse_customer_order
    from .cache import DRAFT_DEBOUNCE_SECONDS

    profile = _resolve_profile(phone)
    if not profile:
        validators.send_whatsapp_message(phone, "מספר הטלפון שלך לא רשום במערכת. פנה למנהל.")
        return HttpResponse(status=200)

    product_names = list(Product.objects.values_list("name", flat=True))

    try:
        parsed_items = parse_customer_order(body, product_names)
    except AmbiguousProductError as exc:
        # Used to answer this immediately, outside the draft/debounce
        # mechanism entirely — but that meant a message sent moments before
        # or after, in the same debounce window, landed in a SEPARATE draft
        # and became a second order. Merge what already resolved into the
        # draft same as usual, stash the ambiguous part alongside it, and
        # let the same debounce dispatch ask about it once the window
        # closes — against the fully-merged basket, not just this message.
        generation = save_draft_order(phone, exc.resolved, ambiguous=exc.ambiguous)
        _schedule_draft_dispatch(phone, generation, DRAFT_DEBOUNCE_SECONDS)
        return HttpResponse(status=200)
    except ValueError:
        validators.send_whatsapp_message(
            phone,
            "לא הצלחתי להבין את ההזמנה.\nנסה לשלוח כגון: 5 קילו עגבניות, 10 קילו גזר",
        )
        return HttpResponse(status=200)

    generation = save_draft_order(phone, parsed_items)
    _schedule_draft_dispatch(phone, generation, DRAFT_DEBOUNCE_SECONDS)
    return HttpResponse(status=200)


def _schedule_draft_dispatch(phone: str, generation: int, countdown: int, is_grace_retry: bool = False):
    try:
        from apps.orders.tasks import dispatch_draft_order_task
        dispatch_draft_order_task.apply_async(
            args=[phone, generation], kwargs={"is_grace_retry": is_grace_retry}, countdown=countdown,
        )
    except Exception as exc:
        logger.warning("Could not schedule draft dispatch task: %s", exc)


def _handle_order_modification(phone: str, body: str, user, batch) -> HttpResponse:
    """Handle ADD or UPDATE modification(s) to the customer's open checkout (OrderBatch)."""
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
    blocked = []
    ambiguous = []
    # One entry per changed order (= per supplier): several changes in the
    # same message (or several messages before the supplier gets around to
    # replying) used to fire one standalone "please confirm" per item, and
    # the supplier's single "אישור" could only ever confirm the last one.
    # Collect everything first, then send one consolidated message per
    # supplier and register it as pending, same contract as a fresh order.
    order_changes = {}

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
            order_request__batch=batch, product=product
        ).select_related("supplier").first()

        if existing_orp:
            cutoff_key = f"supplier_cutoff:{existing_orp.supplier.whatsapp_number}:{existing_orp.order_request_id}"
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

        result = _apply_single_modification(
            product, item["quantity"], item.get("intent") or intent, batch, region,
            order_changes, changes_made, blocked,
        )
        if result == "not_found":
            errors.append(product.name)

    if ambiguous:
        # Anything else in the same message already resolved cleanly and, for
        # a modification, is already committed to the DB (unlike a fresh
        # order, which only computes everything after the whole message is
        # understood) — dispatch those now rather than holding them hostage
        # to the one ambiguous item.
        if order_changes:
            _dispatch_modification_batches(order_changes, company, address)
        if changes_made:
            validators.send_whatsapp_message(
                phone,
                _modification_header(order_changes) + "\n" + "\n".join(f"  • {c}" for c in changes_made),
            )
        return _handle_ambiguous_products(
            phone, ambiguous, [],
            extra={"context": "modification", "batch_id": batch.id, "intent": intent, "region": region},
        )

    if not changes_made and not errors and not blocked:
        validators.send_whatsapp_message(phone, "לא הצלחתי לזהות שינוי בהזמנה. נסה שוב.")
        return HttpResponse(status=200)

    _dispatch_modification_batches(order_changes, company, address)

    reply_lines = []
    if changes_made:
        reply_lines.append(_modification_header(order_changes))
        reply_lines += [f"  • {c}" for c in changes_made]
    if errors:
        reply_lines.append(f"⚠️ לא נמצאו: {', '.join(errors)}")
    if blocked:
        reply_lines.append("⚠️ לא בוצע:")
        reply_lines += [f"  • {b}" for b in blocked]

    validators.send_whatsapp_message(phone, "\n".join(reply_lines))
    return HttpResponse(status=200)


def _handle_user_flow(phone: str, body: str) -> HttpResponse:
    delivery_response = _handle_delivery_flow(phone, body)
    if delivery_response is not None:
        return delivery_response

    fallback_response = _handle_fallback_approval(phone, body)
    if fallback_response is not None:
        return fallback_response

    reroute_grace_response = _handle_reroute_grace_topup(phone, body)
    if reroute_grace_response is not None:
        return reroute_grace_response

    # Before the pending-offer check, so its "כן" isn't taken as confirming an offer.
    merge_response = _handle_merge_offer_reply(phone, body)
    if merge_response is not None:
        return merge_response

    clarify_raw = get_pending_clarification(phone)
    if clarify_raw:
        return _handle_clarification_reply(phone, body, clarify_raw)

    key = f"whatsapp_order:{phone}"
    raw = cache.get(key)

    if not raw:
        if _is_bare_confirmation(body):
            validators.send_whatsapp_message(
                phone,
                "⏰ אין כרגע הזמנה שממתינה לאישור. "
                f"ההצעה תקפה {_offer_validity_text(SESSION_TTL)} מרגע שנשלחה, "
                "ואם עבר הזמן צריך לשלוח את ההזמנה מחדש.",
            )
            return HttpResponse(status=200)

        # Today's order is open for changes until 23:00 Israel time; after that
        # (or with no order today) a message starts a new order.
        from apps.orders.services import before_update_cutoff, todays_batch

        profile = _resolve_profile(phone)
        if profile:
            batch = todays_batch(profile.user)
            if batch:
                if before_update_cutoff():
                    return _handle_order_modification(phone, body, profile.user, batch)
                from apps.orders.models import OrderRequest
                ids = ", ".join(
                    f"#{order_id}" for order_id in batch.orders
                    .exclude(status=OrderRequest.Status.CANCELLED).order_by("id").values_list("id", flat=True)
                )
                validators.send_whatsapp_message(
                    phone, f"⏰ חלון העדכון להזמנה של היום ({ids}) נסגר ב-23:00. פותח הזמנה חדשה.",
                )

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
    orders = _build_and_send_confirmed_order(data, scenario, phone)
    if orders == MERGE_OFFERED:
        return HttpResponse(status=200)

    if orders:
        confirm = _format_scenario(f"✅ אושר! {label}", chosen)
        # One order per supplier — tell the customer each order number, so
        # later messages ("הזמנה #41 יצאה למשלוח") map back to something.
        confirm += "\n\nנשלח לספקים:"
        for order in orders:
            confirm += f"\n  • הזמנה #{order.id} — {order.supplier.name}"
    else:
        confirm = "❌ אירעה שגיאה בעיבוד ההזמנה. אנא נסה שנית או פנה לתמיכה."
    validators.send_whatsapp_message(phone, confirm)

    return HttpResponse(status=200)
