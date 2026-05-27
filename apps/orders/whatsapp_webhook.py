import json
import logging
import re
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from apps.orders.whatsapp import send_whatsapp_message

logger = logging.getLogger(__name__)

SESSION_TTL = 3600          # שעה
SUPPLIER_SESSION_TTL = 86400  # 24 שעות


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


# ─────────────────────── User flow helpers ───────────────────────

def save_pending_order(phone: str, cheapest: dict, fewest: dict,
                       products: list = None,
                       user_id: int = None,
                       region: str = None):
    """
    Cache the suggested order options for a user.
    `products` (list of {product_id, quantity}), `user_id`, and `region`
    are optional but required for actually building the order on confirmation.
    """
    key = f"whatsapp_order:{phone}"
    payload = {"cheapest": cheapest, "fewest": fewest}
    if products is not None:
        payload["products"] = products
    if user_id is not None:
        payload["user_id"] = user_id
    if region is not None:
        payload["region"] = region
    cache.set(key, json.dumps(payload, cls=DecimalEncoder), timeout=SESSION_TTL)


def _format_scenario(label, s):
    lines = [f"*{label}*"]
    for p in s["products"]:
        lines.append(
            f"  • {p['product_name']} x{p['quantity']} {p.get('unit', '')} "
            f"— {p['supplier_name']} — {p['subtotal']}₪"
        )
    lines.append(f'סה"כ: {s["total_price"]}₪')
    return "\n".join(lines)


def _build_and_send_confirmed_order(data: dict, scenario: str):
    """Build order in DB and send WhatsApp to each supplier. Errors are logged, not raised."""
    from django.contrib.auth import get_user_model
    from apps.catalog.models import Product
    from apps.orders.services import build_order
    from apps.orders.models import OrderRequest

    user_id = data.get("user_id")
    raw_products = data.get("products")
    region = data.get("region")

    if not user_id or not raw_products or not region:
        return

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
        order.status = OrderRequest.Status.SENT
        order.save(update_fields=["status"])

        by_supplier = defaultdict(list)
        for orp in order.products.select_related("product", "supplier").all():
            by_supplier[orp.supplier].append(orp)

        for supplier, items in by_supplier.items():
            lines = ["שלום, ברצוני להזמין:"]
            for item in items:
                lines.append(
                    f"- {item.product.name} x{item.quantity} {item.product.get_unit_display()}"
                )
            lines.append("\nאנא ענה *אישור* לאישור הכל, או שלח כמויות מעודכנות.")
            send_whatsapp_message(supplier.whatsapp_number, "\n".join(lines))

            save_supplier_pending_order(
                supplier_phone=supplier.whatsapp_number,
                order_request_id=order.id,
                products=[
                    {
                        "orp_id": item.id,
                        "product_name": item.product.name,
                        "quantity": str(item.quantity),
                        "unit": item.product.get_unit_display(),
                    }
                    for item in items
                ],
            )

    except Exception as exc:
        logger.error("Failed to build/send confirmed order for user %s: %s", user_id, exc)


def _handle_new_order(phone: str, body: str) -> HttpResponse:
    """Handle an incoming message that has no pending order — treat as a new customer order."""
    from apps.catalog.models import Product
    from apps.users.models import Profile
    from apps.orders.services import suggest_order
    from apps.orders.order_parser import parse_customer_order

    # Look up user by phone; try +972XXXXXXXXX and 0XXXXXXXXX
    profile = Profile.objects.filter(phone=phone).select_related("user").first()
    if not profile and phone.startswith("+972"):
        profile = Profile.objects.filter(phone="0" + phone[4:]).select_related("user").first()

    if not profile:
        send_whatsapp_message(phone, "מספר הטלפון שלך לא רשום במערכת. פנה למנהל.")
        return HttpResponse(status=200)

    user = profile.user
    product_names = list(Product.objects.values_list("name", flat=True))

    try:
        parsed_items = parse_customer_order(body, product_names)
    except ValueError:
        send_whatsapp_message(
            phone,
            "לא הצלחתי להבין את ההזמנה.\nנסה לשלוח כגון: 5 קילו עגבניות, 10 קילו גזר",
        )
        return HttpResponse(status=200)

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
        send_whatsapp_message(
            phone,
            f"לא זיהיתי מוצרים ידועים בהזמנה.\nלא זוהה: {', '.join(unrecognized)}",
        )
        return HttpResponse(status=200)

    try:
        result = suggest_order(user=user, region=profile.region, products=products)
    except ValueError as exc:
        send_whatsapp_message(phone, f"שגיאה בעיבוד ההזמנה: {exc}")
        return HttpResponse(status=200)

    cheapest = result["cheapest"]
    fewest = result["fewest_suppliers"]

    save_pending_order(
        phone,
        cheapest,
        fewest,
        products=[{"product_id": p["product"].id, "quantity": str(p["quantity"])} for p in products],
        user_id=user.id,
        region=profile.region,
    )

    same = cheapest["total_price"] == fewest["total_price"]
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

    if unrecognized:
        msg += f"\n\n⚠️ לא זוהה: {', '.join(unrecognized)}"

    send_whatsapp_message(phone, msg)
    return HttpResponse(status=200)


def _handle_user_flow(phone: str, body: str) -> HttpResponse:
    key = f"whatsapp_order:{phone}"
    raw = cache.get(key)

    if not raw:
        return _handle_new_order(phone, body)

    data = json.loads(raw)
    cheapest = data["cheapest"]
    fewest = data["fewest"]

    same = cheapest["total_price"] == fewest["total_price"]

    if same or body in ("א", "1"):
        chosen = cheapest
        label = "אפשרות א׳ — הזול ביותר" if not same else "ההזמנה"
        scenario = "cheapest"
    elif body in ("ב", "2"):
        chosen = fewest
        label = "אפשרות ב׳ — הכי פחות ספקים"
        scenario = "fewest_suppliers"
    else:
        send_whatsapp_message(phone, "אנא ענה *א* או *ב* כדי לבחור.")
        return HttpResponse(status=200)

    cache.delete(key)
    _build_and_send_confirmed_order(data, scenario)

    confirm = _format_scenario(f"✅ אושר! {label}", chosen)
    confirm += "\n\nההזמנה נשלחה לספקים."
    send_whatsapp_message(phone, confirm)

    return HttpResponse(status=200)


# ─────────────────────── Supplier flow helpers ───────────────────────

def save_supplier_pending_order(supplier_phone: str, order_request_id: int, products: list):
    """
    Call this after sending an order to a supplier so the webhook can match the reply.
    products: list of dicts with keys: orp_id, product_name, quantity, unit
    """
    key = f"whatsapp_supplier_pending:{supplier_phone}"
    data = {"order_request_id": order_request_id, "products": products}
    cache.set(key, json.dumps(data, cls=DecimalEncoder), timeout=SUPPLIER_SESSION_TTL)


def _parse_supplier_reply(body: str, products: list) -> dict:
    """
    Returns {orp_id: confirmed_quantity} parsed from the supplier's reply.
    Falls back to full confirmation if body contains a confirmation word.
    """
    body_stripped = body.strip()
    confirm_words = ["אישור", "אוקי", "כן", "ok", "yes", "בסדר", "מאושר", "✅", "👍"]
    if any(w in body_stripped.lower() for w in confirm_words):
        return {p["orp_id"]: Decimal(str(p["quantity"])) for p in products}

    confirmed = {}
    for p in products:
        pattern = re.compile(re.escape(p["product_name"]) + r"[:\s]+(\d+(?:\.\d+)?)", re.IGNORECASE)
        m = pattern.search(body_stripped)
        if m:
            try:
                confirmed[p["orp_id"]] = Decimal(m.group(1))
            except InvalidOperation:
                pass

    # Single product and a lone number → treat as its quantity
    if not confirmed and len(products) == 1:
        numbers = re.findall(r"\d+(?:\.\d+)?", body_stripped)
        if numbers:
            try:
                confirmed[products[0]["orp_id"]] = Decimal(numbers[0])
            except InvalidOperation:
                pass

    return confirmed


def _handle_supplier_price_update(phone: str, supplier, body: str) -> HttpResponse:
    """Supplier sent a price list (no pending order). Parse and update DB."""
    from apps.catalog.price_parser import update_prices_from_message

    try:
        result = update_prices_from_message(supplier, body)
    except ValueError as exc:
        send_whatsapp_message(phone, f"שגיאה בעיבוד המחירים: {exc}")
        return HttpResponse(status=200)

    updated = result["updated"]
    skipped = result["skipped"]

    if not updated and not skipped:
        send_whatsapp_message(phone, "לא זיהיתי מחירים בהודעה. נסה לשלוח כגון:\nעגבניות 3.50, מלפפון 2.00")
        return HttpResponse(status=200)

    existing = [u for u in updated if not u.get("is_new")]
    new_products = [u for u in updated if u.get("is_new")]

    lines = []
    if existing:
        lines.append("✅ מחירים עודכנו:")
        for u in existing:
            lines.append(f"  • {u['product_name']}: {u['price']}₪")

    if new_products:
        lines.append("\n🆕 מוצרים חדשים נוספו לקטלוג:")
        for u in new_products:
            lines.append(f"  • {u['product_name']}: {u['price']}₪")

    if skipped:
        lines.append("\n⚠️ לא זוהה:")
        for s in skipped:
            lines.append(f"  • {s['product_name']} — {s['reason']}")

    send_whatsapp_message(phone, "\n".join(lines))
    return HttpResponse(status=200)


def _handle_supplier_flow(phone: str, supplier, body: str) -> HttpResponse:
    from apps.orders.models import OrderRequestProduct, SupplierConfirmation

    key = f"whatsapp_supplier_pending:{phone}"
    raw = cache.get(key)

    # No pending order → treat message as a price update
    if not raw:
        return _handle_supplier_price_update(phone, supplier, body)

    data = json.loads(raw)
    products = data["products"]

    confirmed = _parse_supplier_reply(body, products)

    if not confirmed:
        send_whatsapp_message(
            phone,
            "לא הצלחתי להבין.\nשלח *אישור* לאישור הכל, או כמויות כגון:\nעגבניות 40, מלפפונים 25",
        )
        return HttpResponse(status=200)

    for orp_id, qty in confirmed.items():
        try:
            orp = OrderRequestProduct.objects.get(id=int(orp_id))
            SupplierConfirmation.objects.update_or_create(
                order_request_product=orp,
                defaults={"confirmed_quantity": qty},
            )
        except (OrderRequestProduct.DoesNotExist, ValueError):
            pass

    cache.delete(key)

    lines = ["✅ תודה! קיבלתי אישור:"]
    for p in products:
        qty = confirmed.get(p["orp_id"], p["quantity"])
        lines.append(f"  • {p['product_name']} x{qty} {p['unit']}")
    send_whatsapp_message(phone, "\n".join(lines))

    return HttpResponse(status=200)


# ─────────────────────── Main webhook ───────────────────────

@csrf_exempt
def whatsapp_webhook(request):
    if request.method != "POST":
        return HttpResponse(status=405)

    body = request.POST.get("Body", "").strip()
    from_raw = request.POST.get("From", "")
    phone = from_raw.replace("whatsapp:", "")

    from apps.catalog.models import Supplier
    try:
        supplier = Supplier.objects.get(whatsapp_number=phone)
        return _handle_supplier_flow(phone, supplier, body)
    except Supplier.DoesNotExist:
        pass

    return _handle_user_flow(phone, body)
