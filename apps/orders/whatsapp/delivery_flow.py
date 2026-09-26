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


def _find_open_orders(phone: str):
    """
    The customer's orders still awaiting delivery, from their most recent
    checkout (OrderBatch) that has any — one order per supplier, so this is
    the list of deliveries the customer could be talking about.
    Returns (orders, profile); orders is [] if there's nothing open / no profile.
    """
    from apps.orders.models import OrderRequest
    from apps.users.models import Profile

    profile = Profile.objects.filter(phone=phone).select_related("user").first()
    if not profile and phone.startswith("+972"):
        profile = Profile.objects.filter(phone="0" + phone[4:]).select_related("user").first()
    if not profile:
        return [], None

    # A supplier fully confirming an order moves it SENT -> APPROVED (and,
    # optionally, -> SHIPPED once they mark it out for delivery).
    open_statuses = [OrderRequest.Status.APPROVED, OrderRequest.Status.SHIPPED]
    latest = (
        OrderRequest.objects
        .filter(user=profile.user, status__in=open_statuses)
        .order_by("-created_at")
        .first()
    )
    if not latest:
        return [], profile
    orders = list(
        OrderRequest.objects
        .filter(batch_id=latest.batch_id, status__in=open_statuses)
        .select_related("supplier")
        .order_by("id")
    )
    return orders, profile


def _order_rows(orders):
    return [
        {
            "order_id": o.id,
            "name": o.supplier.name,
            "whatsapp": o.supplier.whatsapp_number,
            "done": False,
        }
        for o in orders
    ]


def _row_label(row: dict) -> str:
    return f"{row['name']} (הזמנה #{row['order_id']})"


def _mark_delivered(order_id: int) -> None:
    from apps.orders.models import OrderRequest
    try:
        OrderRequest.objects.get(id=order_id).transition_to(OrderRequest.Status.DELIVERED)
    except OrderRequest.DoesNotExist:
        pass
    except ValueError as exc:
        logger.error("Failed to mark order %s delivered: %s", order_id, exc)


def _notify_supplier_not_arrived(row: dict, company_name: str, customer_phone: str) -> None:
    """Ask the supplier for an updated ETA; their reply is picked up by supplier_flow's ETA-request check."""
    company = company_name or "הלקוח"
    validators.send_whatsapp_message(
        row["whatsapp"],
        f"⚠️ {company} מדווח שהזמנה #{row['order_id']} עדיין לא הגיעה.\n"
        "מתי בערך היא צפויה להגיע? השב עם שעה, לדוגמה: \"עד 16:00\" או \"תוך שעה\".",
    )
    save_eta_request(row["whatsapp"], row["order_id"], customer_phone)


def _start_not_arrived_report(phone: str) -> HttpResponse:
    """
    Customer says an order hasn't arrived — ask the supplier(s) involved for
    an updated ETA (or, with one open order, ask right away with nothing to
    disambiguate). Deliberately doesn't touch any order's status: "not
    arrived" isn't a delivery confirmation.
    """
    orders, profile = _find_open_orders(phone)
    if not orders:
        validators.send_whatsapp_message(phone, "לא נמצאה הזמנה פתוחה שממתינה למסירה.")
        return HttpResponse(status=200)

    rows = _order_rows(orders)
    company_name = profile.company_name if profile else ""

    if len(rows) == 1:
        _notify_supplier_not_arrived(rows[0], company_name, phone)
        validators.send_whatsapp_message(
            phone, f"פנינו ל-{rows[0]['name']} לבירור. נעדכן אותך ברגע שיש תשובה."
        )
        return HttpResponse(status=200)

    _save_delivery_state(phone, {"orders": rows, "mode": "report_missing"})
    lines = ["איזו הזמנה עדיין לא הגיעה? ענה עם מספר:"]
    for i, row in enumerate(rows, 1):
        lines.append(f"{i}. {_row_label(row)}")
    lines.append('\nאו ענה *הכל* אם אף אחת לא הגיעה.')
    validators.send_whatsapp_message(phone, "\n".join(lines))
    return HttpResponse(status=200)


def _continue_not_arrived_report(phone: str, body: str, state: dict) -> HttpResponse:
    from apps.users.models import Profile

    rows = state["orders"]

    newly_picked = []
    if any(w in body for w in ALL_WORDS):
        newly_picked = [r for r in rows if not r["done"]]
    else:
        for n in re.findall(r"\d+", body):
            idx = int(n) - 1
            if 0 <= idx < len(rows) and not rows[idx]["done"]:
                newly_picked.append(rows[idx])
        if not newly_picked:
            lines = ["לא הבנתי. ענה עם מספר ההזמנה שלא הגיעה:"]
            for i, r in enumerate(rows, 1):
                lines.append(f"  {i}. {_row_label(r)}")
            lines.append('\nאו ענה *הכל*.')
            validators.send_whatsapp_message(phone, "\n".join(lines))
            return HttpResponse(status=200)

    profile = Profile.objects.filter(phone=phone).first()
    if not profile and phone.startswith("+972"):
        profile = Profile.objects.filter(phone="0" + phone[4:]).first()
    company_name = profile.company_name if profile else ""
    for r in newly_picked:
        r["done"] = True
        _notify_supplier_not_arrived(r, company_name, phone)

    remaining = [r for r in rows if not r["done"]]
    if remaining:
        _save_delivery_state(phone, {"orders": rows, "mode": "report_missing"})
        lines = ["✅ פנינו לספקים הבאים לבירור:"]
        for r in newly_picked:
            lines.append(f"  • {_row_label(r)}")
        lines.append("\n⏳ עדיין לא דיווחת על:")
        for i, r in enumerate(rows, 1):
            if not r["done"]:
                lines.append(f"  {i}. {_row_label(r)}")
        lines.append('\nענה מספר הזמנה נוספת שלא הגיעה, או *הכל*.')
        validators.send_whatsapp_message(phone, "\n".join(lines))
    else:
        _clear_delivery_state(phone)
        validators.send_whatsapp_message(phone, "✅ פנינו לכל הספקים הרלוונטיים לבירור. נעדכן אותך כשיש תשובה.")
    return HttpResponse(status=200)


def _handle_delivery_flow(phone: str, body: str) -> HttpResponse | None:
    """
    Handles delivery confirmation per order (= per supplier), and the "it
    hasn't arrived" report that asks suppliers for an updated ETA. Returns an
    HttpResponse if handled, None if the message is unrelated.
    """
    raw = _get_delivery_state(phone)

    # Start delivery flow
    if raw is None:
        # Checked first — see NOT_ARRIVED_WORDS' comment above.
        if any(w in body for w in NOT_ARRIVED_WORDS):
            return _start_not_arrived_report(phone)
        if not any(w in body for w in ARRIVAL_WORDS):
            return None

        orders, _profile = _find_open_orders(phone)
        if not orders:
            validators.send_whatsapp_message(phone, "לא נמצאה הזמנה פתוחה שממתינה לאישור מסירה.")
            return HttpResponse(status=200)

        rows = _order_rows(orders)

        if len(rows) == 1:
            _mark_delivered(rows[0]["order_id"])
            validators.send_whatsapp_message(
                phone,
                f"✅ הזמנה #{rows[0]['order_id']} מ-{rows[0]['name']} אושרה כנמסרה. תודה!"
            )
            return HttpResponse(status=200)

        _save_delivery_state(phone, {"orders": rows, "mode": "confirm"})
        lines = ["מה הגיע? ענה עם מספר:"]
        for i, row in enumerate(rows, 1):
            lines.append(f"{i}. {_row_label(row)}")
        lines.append('\nאו ענה *הכל* אם הכל הגיע.')
        validators.send_whatsapp_message(phone, "\n".join(lines))
        return HttpResponse(status=200)

    # Continue an in-progress flow — dispatch by which one this is.
    state = json.loads(raw)
    if state.get("mode") == "report_missing":
        return _continue_not_arrived_report(phone, body, state)

    rows = state["orders"]

    newly_delivered = []
    if any(w in body for w in ALL_WORDS):
        newly_delivered = [r for r in rows if not r["done"]]
    else:
        for n in re.findall(r"\d+", body):
            idx = int(n) - 1
            if 0 <= idx < len(rows) and not rows[idx]["done"]:
                newly_delivered.append(rows[idx])
        if not newly_delivered:
            lines = ["לא הבנתי. ענה עם מספר ההזמנה שהגיעה:"]
            for i, r in enumerate(rows, 1):
                status = "✅" if r["done"] else "⏳"
                lines.append(f"{i}. {_row_label(r)} {status}")
            lines.append('\nאו ענה *הכל* לאישור כולן.')
            validators.send_whatsapp_message(phone, "\n".join(lines))
            return HttpResponse(status=200)

    # Each order is its own delivery — mark it delivered right away rather
    # than waiting for the whole checkout to arrive.
    for r in newly_delivered:
        r["done"] = True
        _mark_delivered(r["order_id"])

    reply_lines = ["✅ אושר כנמסר:"]
    for r in newly_delivered:
        reply_lines.append(f"  • {_row_label(r)}")

    pending = [r for r in rows if not r["done"]]
    if pending:
        _save_delivery_state(phone, {"orders": rows, "mode": "confirm"})
        reply_lines.append("\n⏳ עדיין ממתין:")
        for i, r in enumerate(rows, 1):
            if not r["done"]:
                reply_lines.append(f"  {i}. {_row_label(r)}")
        reply_lines.append('\nענה מספר הזמנה שהגיעה, או *הכל*.')
    else:
        _clear_delivery_state(phone)
        reply_lines.append("\nכל ההזמנות אושרו כנמסרו. תודה!")

    validators.send_whatsapp_message(phone, "\n".join(reply_lines))
    return HttpResponse(status=200)
