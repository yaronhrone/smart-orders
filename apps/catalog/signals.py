import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.cache_utils import bump_cache_version
from .models import Product, Supplier, SupplierProduct

logger = logging.getLogger(__name__)

# One shared version for both price-sensitive catalog endpoints
# (ProductCatalogView / SupplierPricesListView) — they're both driven by the
# same three tables, so there's no need for a separate version per endpoint.
CATALOG_CACHE_NAMESPACE = "catalog"
CATALOG_CACHE_KEY = "prices"


def _bump():
    try:
        bump_cache_version(CATALOG_CACHE_NAMESPACE, CATALOG_CACHE_KEY)
    except Exception as exc:
        logger.error("Failed to invalidate catalog price cache: %s", exc)


@receiver(post_save, sender=SupplierProduct)
@receiver(post_delete, sender=SupplierProduct)
def _invalidate_on_price_change(sender, instance, **kwargs):
    """A supplier's price for a product changed — every price listing is stale."""
    _bump()


@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
def _invalidate_on_product_change(sender, instance, **kwargs):
    """A product was added/renamed/removed — the product list itself changed."""
    _bump()


@receiver(post_save, sender=Supplier)
@receiver(post_delete, sender=Supplier)
def _invalidate_on_supplier_change(sender, instance, **kwargs):
    """A supplier's own fields (name, minimum_order, ...) changed."""
    _bump()
