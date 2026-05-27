"""
Parses a free-text price message from a supplier using OpenAI,
then updates SupplierProduct prices in the DB.

Example message:
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


_UNIT_MAP = {
    "קג": "kg", "קילו": "kg", 'ק"ג': "kg", "kg": "kg",
    "גרם": "gram", "gram": "gram",
    "יחידה": "unit", 'יח\'': "unit", "unit": "unit",
    "ארגז": "box", "box": "box",
    "ליטר": "liter", "liter": "liter",
    "מטר": "meter", "meter": "meter",
}


def _normalize_unit(raw: str) -> str:
    return _UNIT_MAP.get(raw.strip().lower(), "kg")


def _parse_with_ai(message: str, product_names: list[str]) -> list[dict]:
    """
    Calls OpenAI to extract product names, prices, and units from a free-text message.
    Returns a list of {"product_name", "price", "unit", "is_new"} dicts.
    """
    known = ", ".join(product_names) if product_names else "—"
    prompt = (
        "You are a price-list parser for a vegetable/fruit supplier system in Israel.\n"
        "Extract ALL products with prices from the message below.\n"
        f"Known products in the system: {known}\n"
        "Rules:\n"
        "1. If the product matches a known product (fuzzy/phonetic Hebrew matching) — use the exact known name and set is_new=false.\n"
        "2. If the product is NEW (not in the known list) — use the name as written in Hebrew and set is_new=true.\n"
        "3. For unit, pick ONE of: קג, גרם, יחידה, ארגז, ליטר, מטר. Default to קג if not specified.\n"
        "Return ONLY a JSON object with key 'items' containing an array.\n"
        'Each element: {"product_name": "...", "price": "3.50", "unit": "קג", "is_new": false}\n'
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

    if isinstance(data, dict):
        data = data.get("items", next(iter(data.values()), []))

    return data if isinstance(data, list) else []


def update_prices_from_message(supplier, message: str) -> dict:
    """
    Parses `message`, matches products, updates SupplierProduct rows.

    Returns:
    {
        "updated": [{"product_name": str, "price": str}],
        "skipped": [{"product_name": str, "reason": str}],
    }
    """
    all_products = {p.name: p for p in Product.objects.all()}

    if not all_products:
        return {"updated": [], "skipped": [{"product_name": "*", "reason": "אין מוצרים בקטלוג"}]}

    try:
        parsed = _parse_with_ai(message, list(all_products.keys()))
    except Exception as exc:
        logger.error("OpenAI price parsing failed: %s", exc)
        raise ValueError(f"AI parsing failed: {exc}")

    updated = []
    skipped = []

    for entry in parsed:
        name = entry.get("product_name", "").strip()
        price_raw = str(entry.get("price", "")).strip()
        unit_raw = str(entry.get("unit", "קג")).strip()
        is_new = entry.get("is_new", False)

        if not name:
            continue

        try:
            price = Decimal(price_raw)
            if price <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            skipped.append({"product_name": name, "reason": f"מחיר לא תקין: {price_raw}"})
            continue

        # Get existing or create new product
        product = all_products.get(name)
        created_product = False
        if not product:
            unit_code = _normalize_unit(unit_raw)
            product, created_product = Product.objects.get_or_create(
                name=name,
                defaults={"unit": unit_code},
            )
            all_products[name] = product

        SupplierProduct.objects.update_or_create(
            supplier=supplier,
            product=product,
            defaults={"price_per_unit": price},
        )
        updated.append({
            "product_name": name,
            "price": str(price),
            "is_new": created_product,
        })

    return {"updated": updated, "skipped": skipped}
