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
from apps.catalog.product_matcher import match_price_items

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
            "items": [{
                "product_name": "<exact canonical name>", "price": "3.50", "unit": "קג",
                "original": "<supplier text for this item>", "confidence": "exact" | "fuzzy",
            }],
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
        "   • 'בצל' (בלי תיאור נוסף) → 'בצל יבש' — זהו הבצל הרגיל/ברירת המחדל בשוק\n"
        "   • 'בצל סגול' / 'בצל אדום' → 'בצל סגול'\n"
        "   • 'בצל ירוק' / 'בצל ירק' → 'בצל ירוק'\n"
        "   כלל כללי: אם מוצר מוזכר בלי תיאור/צבע/זן, אבל בקטלוג יש כמה גרסאות שלו "
        "(למשל עם 'יבש'/'סגול'/'ירוק'/'אדום') — התאם לגרסה הבסיסית/הנפוצה ביותר "
        "(בדרך כלל זו עם 'יבש', אם קיימת). התאם לגרסה ספציפית רק אם הספק ציין אותה במפורש.\n"
        "   השתמש רק בשם הקנוני המדויק כפי שהוא מופיע ברשימה לעיל.\n"
        "2. אם אינך בטוח לגבי מוצר מסוים — הכנס אותו ב-'unmatched' עם הטקסט המקורי.\n"
        "3. עבור יחידה בחר אחת מ: קג, גרם, יחידה, ארגז, אגודה, חבילה. ברירת מחדל: קג.\n"
        "4. לכל פריט ב-'items' — כלול גם 'original' (הטקסט המדויק שהספק כתב עבור המוצר הזה) "
        "וגם 'confidence':\n"
        "   • 'exact' — הטקסט של הספק זהה לשם הקנוני, או שונה ממנו רק ברבים/יחיד או ה\"א הידיעה "
        "(למשל 'מלפפונים'→'מלפפון', 'עגבניות'→'עגבנייה').\n"
        "   • 'fuzzy' — נדרשה החלטה: הושמט/שונה תיאור, דרגה, זן או צבע (למשל 'עגבנייה סוג א'→'עגבנייה', "
        "'בצל' בלי תיאור→'בצל יבש'), או שההתאמה פחות ודאית.\n"
        "5. אם הספק כותב שמוצר מסוים לא זמין כרגע — נגמר, אזל, אין לו, עונה נגמרה, אין מלאי וכו' "
        "(בלי מחיר) — אל תכניס אותו ל-'items'. הכנס אותו במקום זאת ל-'unavailable' עם 'original' "
        "(הטקסט המדויק) ו-'product_name' (השם הקנוני שהתאמת).\n\n"
        "החזר JSON בדיוק בפורמט:\n"
        "{\n"
        '  "items": [{"product_name": "<שם קנוני מדויק>", "price": "3.50", "unit": "קג", '
        '"original": "<טקסט הספק>", "confidence": "exact"}],\n'
        '  "unavailable": [{"product_name": "<שם קנוני מדויק>", "original": "<טקסט הספק>"}],\n'
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
        "unavailable": data.get("unavailable", []) if isinstance(data, dict) else [],
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


def _notify_admin_fuzzy_matches(supplier, items: list, original_message: str) -> None:
    """Send a WhatsApp alert to the admin about matches that weren't exact — worth a manual check."""
    from django.conf import settings
    admin_number = getattr(settings, "ADMIN_WHATSAPP_NUMBER", "")
    if not admin_number:
        logger.warning(
            "ADMIN_WHATSAPP_NUMBER לא מוגדר — לא נשלחה התראה על התאמות לא ודאיות"
        )
        return

    try:
        from apps.orders.whatsapp import send_whatsapp_message

        lines = [f"🔍 *התאמות לא ודאיות מספק {supplier.name}*"]
        lines.append(f"📞 טלפון: {supplier.phone}")
        lines.append("")
        lines.append("המחירים עודכנו, אבל כדאי לוודא שההתאמה נכונה:")
        for item in items:
            original = item.get("original") or item["product_name"]
            unit = item.get("unit") or 'ק"ג'
            lines.append(f"• '{original}' → {item['product_name']}: ₪{item['price']}/{unit}")
        lines.append("")
        lines.append("*ההודעה המקורית של הספק:*")
        lines.append(original_message)

        send_whatsapp_message(admin_number, "\n".join(lines))
        logger.info(
            "נשלחה התראה לאדמין על %d התאמות לא ודאיות מספק %s",
            len(items),
            supplier.name,
        )
    except Exception as exc:
        logger.error("שגיאה בשליחת התראה לאדמין: %s", exc)


def update_prices_from_message(supplier, message: str) -> dict:
    """
    Parses `message`, matches products to the catalog, updates SupplierProduct rows.
    Unmatched products are skipped and the admin is notified via WhatsApp.
    Matches the AI wasn't fully sure about ("fuzzy") are still applied, but also
    flagged to the admin via WhatsApp so they can double-check.

    Segments that resolve via the exact/alias dictionary (data/product_aliases.json)
    skip the AI entirely — only what's left over (grade suffixes, typos, unknown
    products, ambiguous defaults) goes to OpenAI. If everything resolves via the
    dictionary, no AI call is made at all.

    Products the supplier reports as out of stock (no price — "נגמר", "אין", "אזל"
    etc.) have their SupplierProduct row deleted, so future orders stop offering
    this supplier for that product. Sending a new price for it later restores it
    automatically (see the update_or_create below).

    Returns:
    {
        "updated": [{"product_name": str, "price": str, "unit": str}],
        "removed": [{"product_name": str}],
        "skipped": [{"product_name": str, "reason": str}],
        "needs_review": [{"product_name": str, "price": str, "unit": str, "original": str}],
    }
    """
    all_products = {p.name: p for p in Product.objects.all()}
    product_names = list(all_products.keys())

    dict_resolved, dict_unavailable, remaining_message = match_price_items(message, product_names)

    ai_items, ai_unavailable, ai_unmatched = [], [], []
    if remaining_message:
        try:
            parsed = _parse_with_ai(remaining_message, product_names)
        except Exception as exc:
            logger.error("OpenAI price parsing failed: %s", exc)
            raise ValueError(f"שגיאה בעיבוד ההודעה עם AI: {exc}")
        ai_items = parsed.get("items", [])
        ai_unavailable = parsed.get("unavailable", [])
        ai_unmatched = parsed.get("unmatched", [])

    updated = []
    removed = []
    skipped = []
    needs_review = []
    unmatched_for_admin = list(ai_unmatched)

    for entry in dict_unavailable + ai_unavailable:
        name = entry.get("product_name", "").strip()
        product = all_products.get(name)
        if not product:
            continue
        deleted, _ = SupplierProduct.objects.filter(supplier=supplier, product=product).delete()
        if deleted:
            removed.append({"product_name": name})

    for entry in dict_resolved + ai_items:
        name = entry.get("product_name", "").strip()
        price_raw = str(entry.get("price", "")).strip()
        original = entry.get("original", "").strip()
        confidence = entry.get("confidence", "exact")

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
            # AI returned a name that is not in the catalog (hallucination, or it used
            # a placeholder like "unmatched" instead of the top-level "unmatched" list).
            # Report the supplier's actual text, not the (possibly meaningless) product_name.
            unmatched_for_admin.append({"original": original or name, "price": price_raw})
            continue

        SupplierProduct.objects.update_or_create(
            supplier=supplier,
            product=product,
            defaults={"price_per_unit": price},
        )
        item_info = {
            "product_name": name,
            "price": str(price),
            "unit": product.get_unit_display(),
        }
        updated.append(item_info)
        if confidence == "fuzzy":
            needs_review.append({**item_info, "original": original or name})

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

    # Alert admin once for all uncertain-but-applied matches in this message
    if needs_review:
        _notify_admin_fuzzy_matches(supplier, needs_review, message)

    return {"updated": updated, "removed": removed, "skipped": skipped, "needs_review": needs_review}
