import json
from decimal import Decimal
from django.core.cache import cache


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from apps.orders.whatsapp import send_whatsapp_message

SESSION_TTL = 3600  # שעה


def save_pending_order(phone: str, cheapest: dict, fewest: dict):
    key = f"whatsapp_order:{phone}"
    cache.set(key, json.dumps({"cheapest": cheapest, "fewest": fewest}, cls=DecimalEncoder), timeout=SESSION_TTL)


def _format_scenario(label, s):
    lines = [f"*{label}*"]
    for p in s["products"]:
        lines.append(f"  • {p['product_name']} x{p['quantity']} — {p['supplier_name']} — {p['subtotal']}₪")
    lines.append(f"סה\"כ: {s['total_price']}₪")
    return "\n".join(lines)


@csrf_exempt
def whatsapp_webhook(request):
    if request.method != "POST":
        return HttpResponse(status=405)

    body = request.POST.get("Body", "").strip()
    from_raw = request.POST.get("From", "")
    phone = from_raw.replace("whatsapp:", "")

    key = f"whatsapp_order:{phone}"
    raw = cache.get(key)

    if not raw:
        send_whatsapp_message(phone, "אין הזמנה פעילה. אנא הפעל הזמנה חדשה.")
        return HttpResponse(status=200)

    data = json.loads(raw)
    cheapest = data["cheapest"]
    fewest = data["fewest"]

    same = cheapest["total_price"] == fewest["total_price"]

    if same or body in ("א", "1"):
        chosen = cheapest
        label = "אפשרות א׳ — הזול ביותר" if not same else "ההזמנה"
    elif body in ("ב", "2"):
        chosen = fewest
        label = "אפשרות ב׳ — הכי פחות ספקים"
    else:
        send_whatsapp_message(phone, "אנא ענה *א* או *ב* כדי לבחור.")
        return HttpResponse(status=200)

    cache.delete(key)

    confirm = _format_scenario(f"✅ אושר! {label}", chosen)
    confirm += "\n\nההזמנה תישלח לספקים."
    send_whatsapp_message(phone, confirm)

    return HttpResponse(status=200)
