import logging

from django.core.cache import cache as _cache
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt

from .cache import (
    CUTOFF_TTL,
    DecimalEncoder,
    DELIVERY_TTL,
    FALLBACK_TTL,
    SESSION_TTL,
    SUPPLIER_SESSION_TTL,
    _clear_delivery_state,
    _clear_fallback_state,
    _get_delivery_state,
    _get_fallback_state,
    _save_delivery_state,
    _save_fallback_state,
    save_pending_order,
    save_supplier_pending_order,
)
from .delivery_flow import _handle_delivery_flow
from .fallback_flow import (
    _auto_transfer_remaining,
    _execute_fallback_redirect,
    _handle_fallback_approval,
    _handle_missing_items,
    _recalculate_order_total,
    _remove_missing_items,
)
from .supplier_flow import (
    MISSING_KEYWORDS,
    _handle_supplier_flow,
    _handle_supplier_flow_inner,
    _handle_supplier_price_update,
    _parse_supplier_cutoff,
    _parse_supplier_reply,
    notify_suppliers_for_order,
    send_order_to_supplier,
)
from .user_flow import (
    _build_and_send_confirmed_order,
    _format_minimum_warning,
    _format_scenario,
    _handle_new_order,
    _handle_order_modification,
    _handle_user_flow,
)
from .validators import _normalize_phone, _validate_twilio_signature, send_whatsapp_message

logger = logging.getLogger(__name__)


@csrf_exempt
def whatsapp_webhook(request):
    if request.method != "POST":
        return HttpResponse(status=405)

    if not _validate_twilio_signature(request):
        logger.warning("Rejected request with invalid Twilio signature")
        return HttpResponse(status=403)

    message_sid = request.POST.get("MessageSid", "")
    if message_sid:
        dedup_key = f"twilio_msg:{message_sid}"
        if not _cache.add(dedup_key, 1, timeout=3600):
            return HttpResponse(status=200)

    body = request.POST.get("Body", "").strip()
    from_raw = request.POST.get("From", "")
    phone = _normalize_phone(from_raw.replace("whatsapp:", ""))

    from apps.catalog.models import Supplier
    supplier = Supplier.objects.filter(whatsapp_number=phone).first()
    if supplier:
        return _handle_supplier_flow(phone, supplier, body)

    return _handle_user_flow(phone, body)
