"""
Parses a free-text price message from a supplier using OpenAI,
then updates SupplierProduct prices in the DB.

Expected message example:
    "עגבנייה 3.50, מלפפון 2.00, גזר 1.80 לקילו"
"""
import json
import logging
import os
from decimal import Decimal, InvalidOperation

from openai import OpenAI

from apps.catalog.models import Product, SupplierProduct

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _parse_with_ai(message: str, product_names: list[str]) -> list[dict]:
    """
    Calls OpenAI to extract product names and prices from a free-text message.

    Returns a list of {"product_name": str, "price": str} dicts.
    Only returns entries whose product_name matches one of the known products.
    """
    known = ", ".join(product_names)
    prompt = (
        f"You are a price-list parser. Extract product names and prices from the message below.\n"
        f"Known products in the system: {known}\n"
        f"Match each extracted product to the closest known product name.\n"
        f"Return ONLY a JSON array. Each element: {{\"product_name\": \"...\", \"price\": \"...\"}}\n"
        f"Use the exact known product name as product_name. Price must be a decimal number.\n"
        f"Message: {message}"
    )

    response = _get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )

    raw = response.choices[0].message.content
    data = json.loads(raw)

    # The model may return {"items": [...]} or just [...]
    if isinstance(data, dict):
        data = next(iter(data.values()))

    return data if isinstance(data, list) else []


def update_prices_from_message(supplier, message: str) -> dict:
    """
    Main entry point.

    Parses `message`, matches products, updates SupplierProduct rows.

    Returns:
    {
        "updated": [{"product_name": str, "price": str}],
        "skipped": [{"product_name": str, "reason": str}],
    }
    """
    products = {p.name: p for p in Product.objects.all()}

    try:
        parsed = _parse_with_ai(message, list(products.keys()))
    except Exception as exc:
        logger.error("OpenAI price parsing failed: %s", exc)
        raise ValueError(f"AI parsing failed: {exc}")

    updated = []
    skipped = []

    for entry in parsed:
        name = entry.get("product_name", "").strip()
        price_raw = str(entry.get("price", "")).strip()

        if name not in products:
            skipped.append({"product_name": name, "reason": "product not found in system"})
            continue

        try:
            price = Decimal(price_raw)
        except InvalidOperation:
            skipped.append({"product_name": name, "reason": f"invalid price: {price_raw}"})
            continue

        SupplierProduct.objects.update_or_create(
            supplier=supplier,
            product=products[name],
            defaults={"price_per_unit": price},
        )
        updated.append({"product_name": name, "price": str(price)})

    return {"updated": updated, "skipped": skipped}
