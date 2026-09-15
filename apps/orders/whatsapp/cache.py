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

# How long to wait after a customer's last order-building message before
# actually pricing and offering it — lets "20 עגבנייה" followed a moment
# later by "גם 5 חסה" merge into one order instead of the second message
# landing on an already-cached scenario-choice and getting lost.
DRAFT_DEBOUNCE_SECONDS = 180
DRAFT_TTL = 600  # generous vs. the debounce itself; the dispatch task clears it well before this

# When an order doesn't clear a supplier's minimum, hold it as a draft instead
# of dropping it outright - gives the customer a window to top up the same
# basket (more quantity, more products) rather than having to start over.
MINIMUM_GRACE_SECONDS = 7200  # 2 hours
MINIMUM_GRACE_TTL = 7500  # generous vs. the grace window itself, same reasoning as DRAFT_TTL above


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
                       minimum_issues: dict = None,
                       single_scenario: str = None):
    """
    Cache the suggested order options for a user.
    `products` (list of {product_id, quantity}), `user_id`, and `region`
    are optional but required for actually building the order on confirmation.
    `single_scenario` ("cheapest" | "fewest_suppliers"), when set, means only
    that one scenario was actually offered (the other failed a supplier
    minimum) — any reply confirms it, same as the cheapest==fewest shortcut.
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
    if single_scenario is not None:
        payload["single_scenario"] = single_scenario
    cache.set(key, json.dumps(payload, cls=DecimalEncoder), timeout=SESSION_TTL)


def save_pending_clarification(phone: str, resolved_items: list, ambiguous_items: list, extra: dict = None):
    """
    Cache an order that's on hold pending a "which one did you mean" answer.
    `resolved_items`/`ambiguous_items` are the shapes AmbiguousProductError
    carries — {"product_name", "quantity"} and {"query", "quantity",
    "candidates"} respectively. `extra` carries context needed to resume
    afterward — for a modification (as opposed to a fresh order), that's
    {"context": "modification", "order_id", "intent", "region"}.
    """
    key = f"whatsapp_clarify:{phone}"
    payload = {"resolved_items": resolved_items, "ambiguous": ambiguous_items}
    if extra:
        payload.update(extra)
    cache.set(key, json.dumps(payload, cls=DecimalEncoder), timeout=SESSION_TTL)


def get_pending_clarification(phone: str):
    return cache.get(f"whatsapp_clarify:{phone}")


def clear_pending_clarification(phone: str):
    cache.delete(f"whatsapp_clarify:{phone}")


def save_draft_order(phone: str, items: list, ambiguous: list = None, ttl: int = DRAFT_TTL) -> int:
    """
    Merge `items` ({"product_name", "quantity": Decimal}) into the customer's
    in-progress draft order, combining quantities for a product that appears
    in more than one message. Returns the new generation number, to hand to
    the debounce task scheduled right after this call.

    `ambiguous` ({"query", "quantity", "candidates"}, the shape
    AmbiguousProductError carries) is APPENDED, not merged like `items` —
    each entry is its own "which one did you mean" question, not a quantity
    to sum. A message with an unresolved product family (e.g. "בצל" instead
    of a specific variant) used to interrupt the draft entirely and ask about
    it right away, which split one customer's intent into two orders when
    another message landed in the same debounce window. Now it rides along
    in the draft and dispatch_draft_order_task asks about it once the window
    closes, against the fully-merged basket — see AmbiguousProductError's
    docstring and _handle_new_order.

    `ttl` defaults to the short post-message debounce window, but is also
    reused with MINIMUM_GRACE_TTL to hold a basket that failed a supplier's
    minimum open for a couple of hours instead of dropping it outright — same
    merge behavior either way, a later message just tops up the same draft.
    """
    key = f"whatsapp_draft:{phone}"
    raw = cache.get(key)
    existing = json.loads(raw) if raw else {}
    existing_items = existing.get("items", [])
    existing_ambiguous = existing.get("ambiguous", [])

    by_name = {i["product_name"]: Decimal(str(i["quantity"])) for i in existing_items}
    for item in items:
        qty = Decimal(str(item["quantity"]))
        by_name[item["product_name"]] = by_name.get(item["product_name"], Decimal(0)) + qty

    generation = (existing["generation"] + 1) if existing else 1
    payload = {
        "items": [{"product_name": name, "quantity": qty} for name, qty in by_name.items()],
        "ambiguous": existing_ambiguous + (ambiguous or []),
        "generation": generation,
    }
    cache.set(key, json.dumps(payload, cls=DecimalEncoder), timeout=ttl)
    return generation


def get_draft_order(phone: str):
    raw = cache.get(f"whatsapp_draft:{phone}")
    if not raw:
        return None
    data = json.loads(raw)
    # DecimalEncoder stringifies quantity for the JSON round-trip — undo
    # that here so callers (suggest_order's arithmetic) get Decimal back,
    # not the string that broke `quantity * unit_price` downstream.
    for item in data["items"]:
        item["quantity"] = Decimal(item["quantity"])
    data.setdefault("ambiguous", [])
    for item in data["ambiguous"]:
        item["quantity"] = Decimal(item["quantity"])
    return data


def clear_draft_order(phone: str):
    cache.delete(f"whatsapp_draft:{phone}")


def _get_delivery_state(phone: str):
    return cache.get(f"whatsapp_delivery:{phone}")


def _save_delivery_state(phone: str, state: dict):
    cache.set(f"whatsapp_delivery:{phone}", json.dumps(state, cls=DecimalEncoder), timeout=DELIVERY_TTL)


def _clear_delivery_state(phone: str):
    cache.delete(f"whatsapp_delivery:{phone}")


ETA_REQUEST_TTL = 86400  # supplier has a day to reply with an updated ETA before this just expires


def save_eta_request(supplier_phone: str, order_id: int, customer_phone: str):
    """
    Cache that `supplier_phone` owes an updated delivery ETA for `order_id`,
    after the customer reported it hadn't arrived yet (see delivery_flow's
    not-arrived report). The supplier's next reply is checked against this
    before anything else — see _handle_supplier_flow_inner.
    """
    key = f"whatsapp_eta_request:{supplier_phone}"
    payload = {"order_id": order_id, "customer_phone": customer_phone}
    cache.set(key, json.dumps(payload, cls=DecimalEncoder), timeout=ETA_REQUEST_TTL)


def get_eta_request(supplier_phone: str):
    raw = cache.get(f"whatsapp_eta_request:{supplier_phone}")
    return json.loads(raw) if raw else None


def clear_eta_request(supplier_phone: str):
    cache.delete(f"whatsapp_eta_request:{supplier_phone}")


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
