import json
import logging
import re

from django.http import HttpResponse

from .cache import _get_delivery_state, _save_delivery_state, _clear_delivery_state, save_eta_request
from . import validators

logger = logging.getLogger(__name__)

# "קיבלתי" is the word a customer actually types to say "it arrived" — it
# was missing here, so that exact message fell straight through this check
# (returns None) into whatever else was pending, unrecognized.
ARRIVAL_WORDS = ["הגיע", "הגיעה", "נמסר", "נמסרה", "קיבלתי", "קיבלנו", "arrived", "received"]
ALL_WORDS = ["הכל", "כולם", "הכל הגיע", "הכל נמסר", "all", "כן"]

# Checked BEFORE ARRIVAL_WORDS: "לא הגיע" contains the bare word "הגיע", so
# without this a customer reporting a delivery PROBLEM was silently read as
# confirming it arrived — the exact opposite of what they said.
NOT_ARRIVED_WORDS = [
    "לא הגיע", "לא הגיעה", "לא הגיעו", "עדיין לא הגיע", "עדיין לא הגיעה",
    "לא קיבלתי", "לא קיבלנו", "לא נמסר", "לא נמסרה", "not arrived", "not received",
]


def _find_open_order(phone: str):
    """Customer's most recent order still awaiting delivery, or None if there isn't one / no profile."""
    from apps.orders.models import OrderRequest
    from apps.users.models import Profile

    profile = Profile.objects.filter(phone=phone).select_related("user").first()
    if not profile and phone.startswith("+972"):
        profile = Profile.objects.filter(phone="0" + phone[4:]).select_related("user").first()
    if not profile:
        return None, None

    # A supplier fully confirming an order moves it SENT -> APPROVED (and,
    # optionally, -> SHIPPED once they mark it out for delivery) — this used
    # to only look for SENT, so a delivery report found nothing for the most
    # common case (a supplier who already confirmed).
    order = (
        OrderRequest.objects
        .filter(user=profile.user, status__in=[OrderRequest.Status.APPROVED, OrderRequest.Status.SHIPPED])
        .order_by("-created_at")
        .first()
    )
    return order, profile


def _order_supplier_list(order):
    suppliers = list(
        order.products.select_related("supplier")
        .values("supplier__id", "supplier__name", "supplier__whatsapp_number")
        .distinct()
    )
    return [
        {"id": s["supplier__id"], "name": s["supplier__name"], "whatsapp": s["supplier__whatsapp_number"], "done": False}
        for s in suppliers
    ]


def _notify_supplier_not_arrived(order, supplier_row: dict, company_name: str, customer_phone: str) -> None:
    """Ask the supplier for an updated ETA; their reply is picked up by supplier_flow's ETA-request check."""
    company = company_name or "הלקוח"
    validators.send_whatsapp_message(
        supplier_row["whatsapp"],
        f"⚠️ {company} מדווח שהזמנה #{order.id} עדיין לא הגיעה.\n"
        "מתי בערך היא צפויה להגיע? השב עם שעה, לדוגמה: \"עד 16:00\" או \"תוך שעה\".",
    )
    save_eta_request(supplier_row["whatsapp"], order.id, customer_phone)


def _start_not_arrived_report(phone: str) -> HttpResponse:
    """
    Customer says an order hasn't arrived — ask each involved supplier for
    an updated ETA (or, with one supplier, ask right away with nothing to
    disambiguate). Deliberately doesn't touch the order's status: "not
    arrived" isn't a delivery confirmation, so nothing here should look like
    one — the fix for that lives in whether/when the supplier answers.
    """
    order, profile = _find_open_order(phone)
    if not order:
        validators.send_whatsapp_message(phone, "לא נמצאה הזמנה פתוחה שממתינה למסירה.")
        return HttpResponse(status=200)

    supplier_list = _order_supplier_list(order)
    company_name = profile.company_name if profile else ""

    if len(supplier_list) == 1:
        _notify_supplier_not_arrived(order, supplier_list[0], company_name, phone)
        validators.send_whatsapp_message(
            phone, f"פנינו ל-{supplier_list[0]['name']} לבירור. נעדכן אותך ברגע שיש תשובה."
        )
        return HttpResponse(status=200)

    _save_delivery_state(phone, {"order_id": order.id, "suppliers": supplier_list, "mode": "report_missing"})
    lines = [f"איזה ספק מהזמנה #{order.id} עדיין לא הגיע? ענה עם מספר:"]
    for i, s in enumerate(supplier_list, 1):
        lines.append(f"{i}. {s['name']}")
    lines.append('\nאו ענה *הכל* אם אף אחד מהספקים לא הגיע.')
    validators.send_whatsapp_message(phone, "\n".join(lines))
    return HttpResponse(status=200)


def _continue_not_arrived_report(phone: str, body: str, state: dict) -> HttpResponse:
    from apps.orders.models import OrderRequest

    order_id = state["order_id"]
    suppliers = state["suppliers"]

    newly_picked = []
    if any(w in body for w in ALL_WORDS):
        newly_picked = [s for s in suppliers if not s["done"]]
    else:
        for n in re.findall(r"\d+", body):
            idx = int(n) - 1
            if 0 <= idx < len(suppliers) and not suppliers[idx]["done"]:
                newly_picked.append(suppliers[idx])
        if not newly_picked:
            lines = ["לא הבנתי. ענה עם מספר הספק שלא הגיע:"]
            for i, s in enumerate(suppliers, 1):
                lines.append(f"  {i}. {s['name']}")
            lines.append('\nאו ענה *הכל*.')
            validators.send_whatsapp_message(phone, "\n".join(lines))
            return HttpResponse(status=200)

    order = OrderRequest.objects.select_related("user__profile").filter(id=order_id).first()
    company_name = order.user.profile.company_name if order and getattr(order.user, "profile", None) else ""
    for s in newly_picked:
        s["done"] = True
        if order:
            _notify_supplier_not_arrived(order, s, company_name, phone)

    remaining = [s for s in suppliers if not s["done"]]
    if remaining:
        _save_delivery_state(phone, {"order_id": order_id, "suppliers": suppliers, "mode": "report_missing"})
        lines = ["✅ פנינו לספקים הבאים לבירור:"]
        for s in newly_picked:
            lines.append(f"  • {s['name']}")
        lines.append("\n⏳ עדיין לא דיווחת על:")
        for i, s in enumerate(suppliers, 1):
            if not s["done"]:
                lines.append(f"  {i}. {s['name']}")
        lines.append('\nענה מספר ספק נוסף שלא הגיע, או *הכל*.')
        validators.send_whatsapp_message(phone, "\n".join(lines))
    else:
        _clear_delivery_state(phone)
        validators.send_whatsapp_message(phone, "✅ פנינו לכל הספקים הרלוונטיים לבירור. נעדכן אותך כשיש תשובה.")
    return HttpResponse(status=200)


def _handle_delivery_flow(phone: str, body: str) -> HttpResponse | None:
    """
    Handles delivery confirmation per supplier, and the "it hasn't arrived"
    report that asks suppliers for an updated ETA. Returns an HttpResponse
    if handled, None if the message is unrelated.
    """
    from apps.orders.models import OrderRequest

    raw = _get_delivery_state(phone)

    # Start delivery flow
    if raw is None:
        # Checked first — see NOT_ARRIVED_WORDS' comment above.
        if any(w in body for w in NOT_ARRIVED_WORDS):
            return _start_not_arrived_report(phone)
        if not any(w in body for w in ARRIVAL_WORDS):
            return None

        order, _profile = _find_open_order(phone)
        if not order:
            validators.send_whatsapp_message(phone, "לא נמצאה הזמנה פתוחה שממתינה לאישור מסירה.")
            return HttpResponse(status=200)

        supplier_list = _order_supplier_list(order)

        if len(supplier_list) == 1:
            try:
                order.transition_to(OrderRequest.Status.DELIVERED)
            except ValueError as exc:
                logger.error("Failed to mark order %s delivered: %s", order.id, exc)
            validators.send_whatsapp_message(
                phone,
                f"✅ הזמנה #{order.id} מ-{supplier_list[0]['name']} אושרה כנמסרה. תודה!"
            )
            return HttpResponse(status=200)

        _save_delivery_state(phone, {"order_id": order.id, "suppliers": supplier_list, "mode": "confirm"})
        lines = [f"מה הגיע מהזמנה #{order.id}? ענה עם מספר:"]
        for i, s in enumerate(supplier_list, 1):
            lines.append(f"{i}. {s['name']}")
        lines.append('\nאו ענה *הכל* אם כל הספקים הגיעו.')
        validators.send_whatsapp_message(phone, "\n".join(lines))
        return HttpResponse(status=200)

    # Continue an in-progress flow — dispatch by which one this is.
    state = json.loads(raw)
    if state.get("mode") == "report_missing":
        return _continue_not_arrived_report(phone, body, state)

    order_id = state["order_id"]
    suppliers = state["suppliers"]

    if any(w in body for w in ALL_WORDS):
        for s in suppliers:
            s["done"] = True
    else:
        nums = re.findall(r"\d+", body)
        matched = False
        for n in nums:
            idx = int(n) - 1
            if 0 <= idx < len(suppliers):
                suppliers[idx]["done"] = True
                matched = True
        if not matched:
            lines = ["לא הבנתי. ענה עם מספר הספק:"]
            for i, s in enumerate(suppliers, 1):
                status = "✅" if s["done"] else "⏳"
                lines.append(f"{i}. {s['name']} {status}")
            lines.append('\nאו ענה *הכל* לאישור כולם.')
            validators.send_whatsapp_message(phone, "\n".join(lines))
            return HttpResponse(status=200)

    pending = [s for s in suppliers if not s["done"]]
    delivered = [s for s in suppliers if s["done"]]

    reply_lines = ["✅ אושר:"]
    for s in delivered:
        reply_lines.append(f"  • {s['name']}")

    if pending:
        _save_delivery_state(phone, {"order_id": order_id, "suppliers": suppliers, "mode": "confirm"})
        reply_lines.append("\n⏳ עדיין ממתין:")
        for i, s in enumerate(suppliers, 1):
            if not s["done"]:
                reply_lines.append(f"  {i}. {s['name']}")
        reply_lines.append('\nענה מספר ספק שהגיע, או *הכל*.')
    else:
        _clear_delivery_state(phone)
        try:
            order = OrderRequest.objects.get(id=order_id)
            order.transition_to(OrderRequest.Status.DELIVERED)
        except OrderRequest.DoesNotExist:
            pass
        except ValueError as exc:
            logger.error("Failed to mark order %s delivered: %s", order_id, exc)
        reply_lines.append(f"\n✅ כל הזמנה #{order_id} אושרה כנמסרה. תודה!")

    validators.send_whatsapp_message(phone, "\n".join(reply_lines))
    return HttpResponse(status=200)
