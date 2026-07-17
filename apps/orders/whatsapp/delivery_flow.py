import json
import logging
import re

from django.http import HttpResponse

from .cache import _get_delivery_state, _save_delivery_state, _clear_delivery_state
from . import validators

logger = logging.getLogger(__name__)


def _handle_delivery_flow(phone: str, body: str) -> HttpResponse | None:
    """
    Handles delivery confirmation per supplier.
    Returns an HttpResponse if handled, None if the message is unrelated.
    """
    from apps.orders.models import OrderRequest

    ARRIVAL_WORDS = ["הגיע", "הגיעה", "נמסר", "נמסרה", "arrived", "received"]
    ALL_WORDS = ["הכל", "כולם", "הכל הגיע", "הכל נמסר", "all", "כן"]

    raw = _get_delivery_state(phone)

    # Start delivery flow
    if raw is None:
        if not any(w in body for w in ARRIVAL_WORDS):
            return None

        from apps.users.models import Profile
        profile = Profile.objects.filter(phone=phone).select_related("user").first()
        if not profile and phone.startswith("+972"):
            profile = Profile.objects.filter(phone="0" + phone[4:]).select_related("user").first()
        if not profile:
            return None

        order = (
            OrderRequest.objects
            .filter(user=profile.user, status=OrderRequest.Status.SENT)
            .order_by("-created_at")
            .first()
        )
        if not order:
            validators.send_whatsapp_message(phone, "לא נמצאה הזמנה פתוחה שממתינה לאישור מסירה.")
            return HttpResponse(status=200)

        suppliers = list(
            order.products.select_related("supplier")
            .values("supplier__id", "supplier__name")
            .distinct()
        )
        supplier_list = [
            {"id": s["supplier__id"], "name": s["supplier__name"], "delivered": False}
            for s in suppliers
        ]

        if len(supplier_list) == 1:
            order.status = OrderRequest.Status.DELIVERED
            order.save(update_fields=["status"])
            validators.send_whatsapp_message(
                phone,
                f"✅ הזמנה #{order.id} מ-{supplier_list[0]['name']} אושרה כנמסרה. תודה!"
            )
            return HttpResponse(status=200)

        _save_delivery_state(phone, {"order_id": order.id, "suppliers": supplier_list})
        lines = [f"מה הגיע מהזמנה #{order.id}? ענה עם מספר:"]
        for i, s in enumerate(supplier_list, 1):
            lines.append(f"{i}. {s['name']}")
        lines.append('\nאו ענה *הכל* אם כל הספקים הגיעו.')
        validators.send_whatsapp_message(phone, "\n".join(lines))
        return HttpResponse(status=200)

    # Continue delivery flow
    state = json.loads(raw)
    order_id = state["order_id"]
    suppliers = state["suppliers"]

    if any(w in body for w in ALL_WORDS):
        for s in suppliers:
            s["delivered"] = True
    else:
        nums = re.findall(r"\d+", body)
        matched = False
        for n in nums:
            idx = int(n) - 1
            if 0 <= idx < len(suppliers):
                suppliers[idx]["delivered"] = True
                matched = True
        if not matched:
            lines = ["לא הבנתי. ענה עם מספר הספק:"]
            for i, s in enumerate(suppliers, 1):
                status = "✅" if s["delivered"] else "⏳"
                lines.append(f"{i}. {s['name']} {status}")
            lines.append('\nאו ענה *הכל* לאישור כולם.')
            validators.send_whatsapp_message(phone, "\n".join(lines))
            return HttpResponse(status=200)

    pending = [s for s in suppliers if not s["delivered"]]
    delivered = [s for s in suppliers if s["delivered"]]

    reply_lines = ["✅ אושר:"]
    for s in delivered:
        reply_lines.append(f"  • {s['name']}")

    if pending:
        _save_delivery_state(phone, {"order_id": order_id, "suppliers": suppliers})
        reply_lines.append("\n⏳ עדיין ממתין:")
        for i, s in enumerate(suppliers, 1):
            if not s["delivered"]:
                reply_lines.append(f"  {i}. {s['name']}")
        reply_lines.append('\nענה מספר ספק שהגיע, או *הכל*.')
    else:
        _clear_delivery_state(phone)
        try:
            order = OrderRequest.objects.get(id=order_id)
            order.status = OrderRequest.Status.DELIVERED
            order.save(update_fields=["status"])
        except OrderRequest.DoesNotExist:
            pass
        reply_lines.append(f"\n✅ כל הזמנה #{order_id} אושרה כנמסרה. תודה!")

    validators.send_whatsapp_message(phone, "\n".join(reply_lines))
    return HttpResponse(status=200)
