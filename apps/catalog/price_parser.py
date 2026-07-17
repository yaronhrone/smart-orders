"""
Parses a free-text price message from a supplier using OpenAI,
then updates SupplierProduct prices in the DB.

The catalog is the single source of truth for product names.
The AI tries to match supplier text to EXISTING catalog names only.
Unrecognized products are collected and reported to the admin via WhatsApp.
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


def _parse_with_ai(message: str, product_names: list[str]) -> dict:
    """
    Calls OpenAI to extract prices and match them to catalog products.

    Returns:
        {
            "items": [{"product_name": "<exact canonical name>", "price": "3.50", "unit": "קג"}],
            "unmatched": [{"original": "<supplier text>", "price": "X.XX"}]
        }
    """
    known = "\n".join(f"- {n}" for n in product_names) if product_names else "— (קטלוג ריק) —"

    prompt = (
        "אתה מנתח מחירון של ספק ירקות/פירות בישראל.\n"
        "תפקידך: לחלץ מחירים מהודעת הספק ולהתאים כל מוצר לשמו הקנוני במערכת.\n\n"
        "שמות המוצרים הקנוניים במערכת (אלה השמות היחידים המותרים):\n"
        f"{known}\n\n"
        "חוקים:\n"
        "1. לכל מוצר בהודעה — נסה להתאים אותו לשם קנוני מהרשימה לעיל.\n"
        "   דוגמאות להתאמה:\n"
        "   • 'מלפפונים' → 'מלפפון'\n"
        "   • 'עגבניות' → 'עגבנייה'\n"
        "   • 'תפוחי אדמה' → 'תפוח אדמה'\n"
        "   • 'בצל יבש' → 'בצל' (אם 'בצל' קיים בקטלוג)\n"
        "   השתמש רק בשם הקנוני המדויק כפי שהוא מופיע ברשימה לעיל.\n"
        "2. אם אינך בטוח לגבי מוצר מסוים — הכנס אותו ב-'unmatched' עם הטקסט המקורי.\n"
        "3. עבור יחידה בחר אחת מ: קג, גרם, יחידה, ארגז, ליטר, מטר. ברירת מחדל: קג.\n\n"
        "החזר JSON בדיוק בפורמט:\n"
        "{\n"
        '  "items": [{"product_name": "<שם קנוני מדויק>", "price": "3.50", "unit": "קג"}],\n'
        '  "unmatched": [{"original": "<טקסט מהספק>", "price": "X.XX"}]\n'
        "}\n\n"
        f"הודעת הספק: {message}"
    )

    response = _get_client().chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
    )

    raw = response.choices[0].message.content
    data = json.loads(raw)

    return {
        "items": data.get("items", []) if isinstance(data, dict) else [],
        "unmatched": data.get("unmatched", []) if isinstance(data, dict) else [],
    }


def _notify_admin_unmatched(supplier, unmatched: list, original_message: str) -> None:
    """Send a WhatsApp alert to the admin about products not found in the catalog."""
    from django.conf import settings
    admin_number = getattr(settings, "ADMIN_WHATSAPP_NUMBER", "")
    if not admin_number:
        logger.warning(
            "ADMIN_WHATSAPP_NUMBER לא מוגדר — לא נשלחה התראה על מוצרים לא מוכרים"
        )
        return

    try:
        from apps.orders.whatsapp import send_whatsapp_message

        lines = [f"⚠️ *מוצרים לא מזוהים מספק {supplier.name}*"]
        lines.append(f"📞 טלפון: {supplier.phone}")
        lines.append("")
        lines.append("המוצרים הבאים לא נמצאו בקטלוג:")
        for u in unmatched:
            original = u.get("original", "")
            price = u.get("price", "")
            suffix = f" — ₪{price}" if price else ""
            lines.append(f"• {original}{suffix}")
        lines.append("")
        lines.append("*ההודעה המקורית של הספק:*")
        lines.append(original_message)
        lines.append("")
        lines.append("כדי להוסיף מוצר חדש לקטלוג, היכנס לפאנל הניהול.")

        send_whatsapp_message(admin_number, "\n".join(lines))
        logger.info(
            "נשלחה התראה לאדמין על %d מוצרים לא מוכרים מספק %s",
            len(unmatched),
            supplier.name,
        )
    except Exception as exc:
        logger.error("שגיאה בשליחת התראה לאדמין: %s", exc)


def update_prices_from_message(supplier, message: str) -> dict:
    """
    Parses `message`, matches products to the catalog, updates SupplierProduct rows.
    Unmatched products are skipped and the admin is notified via WhatsApp.

    Returns:
    {
        "updated": [{"product_name": str, "price": str, "unit": str}],
        "skipped": [{"product_name": str, "reason": str}],
    }
    """
    all_products = {p.name: p for p in Product.objects.all()}

    try:
        parsed = _parse_with_ai(message, list(all_products.keys()))
    except Exception as exc:
        logger.error("OpenAI price parsing failed: %s", exc)
        raise ValueError(f"שגיאה בעיבוד ההודעה עם AI: {exc}")

    updated = []
    skipped = []
    unmatched_for_admin = list(parsed.get("unmatched", []))

    for entry in parsed.get("items", []):
        name = entry.get("product_name", "").strip()
        price_raw = str(entry.get("price", "")).strip()

        if not name:
            continue

        try:
            price = Decimal(price_raw)
            if price <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            skipped.append({
                "product_name": name,
                "reason": f"מחיר לא תקין: {price_raw}",
            })
            continue

        product = all_products.get(name)
        if not product:
            # AI returned a name that is not in the catalog (hallucination or mismatch).
            # Treat as unmatched and alert the admin.
            unmatched_for_admin.append({"original": name, "price": price_raw})
            continue

        SupplierProduct.objects.update_or_create(
            supplier=supplier,
            product=product,
            defaults={"price_per_unit": price},
        )
        updated.append({
            "product_name": name,
            "price": str(price),
            "unit": product.get_unit_display(),
        })

    # Collect unmatched into skipped for the API response
    for u in unmatched_for_admin:
        original = u.get("original", "")
        skipped.append({
            "product_name": original,
            "reason": f"המוצר '{original}' לא קיים בקטלוג — האדמין קיבל התראה",
        })

    # Alert admin once for all unmatched items in this message
    if unmatched_for_admin:
        _notify_admin_unmatched(supplier, unmatched_for_admin, message)

    return {"updated": updated, "skipped": skipped}
