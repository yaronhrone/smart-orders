import json
import logging
from decimal import Decimal

from django.core.cache import cache

logger = logging.getLogger(__name__)

SUPPLIER_SESSION_TTL = 86400  # 24 שעות
SESSION_TTL = 3600
CUTOFF_TTL = 86400
FALLBACK_TTL = 3600
DELIVERY_TTL = 86400  # 24 שעות


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return str(obj)
        return super().default(obj)


def save_supplier_pending_order(supplier_phone: str, order_request_id: int, products: list):
    """
    Cache a pending supplier order so the webhook can match the reply.
    products: list of dicts with keys: orp_id, product_name, quantity, unit
    """
    key = f"whatsapp_supplier_pending:{supplier_phone}"
    data = {"order_request_id": order_request_id, "products": products}
    cache.set(key, json.dumps(data, cls=DecimalEncoder), timeout=SUPPLIER_SESSION_TTL)


def save_pending_order(phone: str, cheapest: dict, fewest: dict,
                       products: list = None,
                       user_id: int = None,
                       region: str = None,
                       minimum_issues: dict = None):
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
    if minimum_issues is not None:
        payload["minimum_issues"] = minimum_issues
    cache.set(key, json.dumps(payload, cls=DecimalEncoder), timeout=SESSION_TTL)


def _get_delivery_state(phone: str):
    return cache.get(f"whatsapp_delivery:{phone}")


def _save_delivery_state(phone: str, state: dict):
    cache.set(f"whatsapp_delivery:{phone}", json.dumps(state, cls=DecimalEncoder), timeout=DELIVERY_TTL)


def _clear_delivery_state(phone: str):
    cache.delete(f"whatsapp_delivery:{phone}")


def _save_fallback_state(phone: str, state: dict):
    cache.set(f"whatsapp_fallback:{phone}", json.dumps(state, cls=DecimalEncoder), timeout=FALLBACK_TTL)
    try:
        from apps.orders.tasks import handle_fallback_timeout
        handle_fallback_timeout.apply_async(
            args=[phone, state.get("order_request_id")],
            countdown=FALLBACK_TTL,
        )
    except Exception as exc:
        logger.warning("Could not schedule fallback timeout task: %s", exc)


def _get_fallback_state(phone: str):
    return cache.get(f"whatsapp_fallback:{phone}")


def _clear_fallback_state(phone: str):
    cache.delete(f"whatsapp_fallback:{phone}")
