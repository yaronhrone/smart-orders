import logging

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from core.cache_utils import bump_cache_version
from .models import OrderRequest, OrderRequestProduct

logger = logging.getLogger(__name__)


@receiver(post_save, sender=OrderRequest)
@receiver(post_delete, sender=OrderRequest)
def _invalidate_on_order_change(sender, instance, **kwargs):
    """Any create/status-change/delete of an order stales that user's cached order views."""
    try:
        bump_cache_version("orders", instance.user_id)
    except Exception as exc:
        logger.error("Failed to invalidate orders cache for user %s: %s", instance.user_id, exc)


@receiver(post_save, sender=OrderRequestProduct)
@receiver(post_delete, sender=OrderRequestProduct)
def _invalidate_on_item_change(sender, instance, **kwargs):
    """Line-item changes (quantity/supplier updates, fallback redirects) also affect order detail."""
    try:
        user_id = OrderRequest.objects.only("user_id").get(id=instance.order_request_id).user_id
        bump_cache_version("orders", user_id)
    except OrderRequest.DoesNotExist:
        pass  # parent order already gone (cascade delete) — its own signal already invalidated
    except Exception as exc:
        logger.error("Failed to invalidate orders cache for order %s: %s", instance.order_request_id, exc)
