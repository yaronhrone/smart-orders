import logging
from celery import shared_task
from .price_parser import update_prices_from_message

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=3, default_retry_delay=300)
def fetch_market_prices_task(self):

    from django.conf import settings
    from apps.catalog.models import Product, MarketPrice
    from apps.catalog.market_scraper import fetch_vegetable_prices

    url = getattr(settings, "PLANT_COUNCIL_PRICES_URL", "")
    if not url:
        logger.error("fetch_market_prices_task: PLANT_COUNCIL_PRICES_URL לא מוגדר ב-settings")
        return {"error": "PLANT_COUNCIL_PRICES_URL not configured"}

    try:
        rows = fetch_vegetable_prices(url)
    except Exception as exc:
        logger.error("שגיאה באחזור מחירי שוק: %s", exc)
        raise self.retry(exc=exc)

    updated = skipped = 0
    for row in rows:
        try:
            product = Product.objects.get(name=row["name"])
        except Product.DoesNotExist:
            logger.debug("מוצר לא קיים בקטלוג, דילוג: %s", row["name"])
            skipped += 1
            continue

        price_grade_a = row["price_grade_a"]
        price_premium = row["price_premium"]
        primary_price = price_grade_a if price_grade_a is not None else price_premium

        if primary_price is None:
            logger.debug("אין נתוני מחיר למוצר: %s", row["name"])
            skipped += 1
            continue

        MarketPrice.objects.update_or_create(
            product=product,
            defaults={
                "price_per_unit": primary_price,
                "price_grade_a": price_grade_a,
                "price_premium": price_premium,
                "market_date": row["market_date"],
                "source": "מועצת הצמחים",
            },
        )
        updated += 1

    logger.info("עדכון מחירי שוק הסתיים: %d עודכנו, %d דולגו", updated, skipped)
    return {"updated": updated, "skipped": skipped}


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def update_supplier_prices_task(self, supplier_id: int, message: str):
    """
    Celery task: parse a supplier's price message and update DB prices.
    Called manually via the API or scheduled daily.
    """
    from apps.catalog.models import Supplier

    try:
        supplier = Supplier.objects.get(pk=supplier_id)
    except Supplier.DoesNotExist:
        logger.error("update_supplier_prices_task: supplier %s not found", supplier_id)
        return

    try:
        result = update_prices_from_message(supplier, message)
        logger.info(
            "Supplier %s prices updated: %d updated, %d skipped",
            supplier.name, len(result["updated"]), len(result["skipped"]),
        )
        return result
    except Exception as exc:
        logger.error("Price update failed for supplier %s: %s", supplier_id, exc)
        raise self.retry(exc=exc)
