import json
import logging
import os
from decimal import Decimal, InvalidOperation

from openai import OpenAI

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def parse_customer_order(message: str, product_names: list) -> list:
    """
    Extracts products and quantities from a free-text customer order message.
    Returns [{"product_name": str, "quantity": Decimal}].
    Raises ValueError("no_items") if nothing extracted.
    Raises ValueError("AI parsing failed: ...") on OpenAI error.
    """
    known = ", ".join(product_names) if product_names else "—"
    prompt = (
        "You are an order parser for a vegetable/fruit ordering system in Israel.\n"
        "Extract ALL products and quantities from the customer's order message.\n"
        f"Known products in the system: {known}\n"
        "Rules:\n"
        "1. Match product names to known products using fuzzy/phonetic Hebrew matching. "
        "Use the exact known name when there is a match.\n"
        "2. Default quantity is 1 if not specified.\n"
        "Return ONLY a JSON object with key 'items'. "
        'Each element: {"product_name": "...", "quantity": "5.0"}\n'
        f"Message: {message}"
    )

    try:
        response = _get_client().chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        raw = response.choices[0].message.content
        data = json.loads(raw)
        if isinstance(data, dict):
            data = data.get("items", next(iter(data.values()), []))
        items = data if isinstance(data, list) else []
    except Exception as exc:
        logger.error("OpenAI order parsing failed: %s", exc)
        raise ValueError(f"AI parsing failed: {exc}")

    result = []
    for entry in items:
        name = entry.get("product_name", "").strip()
        qty_raw = str(entry.get("quantity", "")).strip()
        if not name or not qty_raw:
            continue
        try:
            qty = Decimal(qty_raw)
            if qty <= 0:
                continue
        except InvalidOperation:
            continue
        result.append({"product_name": name, "quantity": qty})

    if not result:
        raise ValueError("no_items")

    return result
