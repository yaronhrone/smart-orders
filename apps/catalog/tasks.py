import logging
from celery import shared_task
from .price_parser import update_prices_from_message

logger = logging.getLogger(__name__)


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
