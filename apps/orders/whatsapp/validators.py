import logging

from django.conf import settings
from twilio.rest import Client

logger = logging.getLogger(__name__)


def send_whatsapp_message(to_number: str, body: str) -> str:
    """
    Send a WhatsApp message via Twilio Sandbox.
    to_number: Israeli format, e.g. "+972501234567"
    Returns the Twilio message SID.
    If WHATSAPP_OVERRIDE_NUMBER is set, all messages are redirected there (for testing).
    """
    override = getattr(settings, "WHATSAPP_OVERRIDE_NUMBER", None)
    actual_to = override if override else to_number
    if override and override != to_number:
        body = f"[בדיקה — מיועד ל: {to_number}]\n\n{body}"

    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    from_wa = f"whatsapp:{settings.TWILIO_WHATSAPP_NUMBER}"
    to_wa = f"whatsapp:{actual_to}"
    message = client.messages.create(body=body, from_=from_wa, to=to_wa)
    return message.sid


def _normalize_phone(phone: str) -> str:
    """Ensure phone is in +XXXXXXXXXXX format."""
    if phone.startswith("972") and not phone.startswith("+"):
        return "+" + phone
    return phone


def _validate_twilio_signature(request) -> bool:
    """Return True if the request came from Twilio (or TWILIO_SKIP_SIGNATURE_VALIDATION is on)."""
    if settings.TWILIO_SKIP_SIGNATURE_VALIDATION:
        return True
    try:
        from twilio.request_validator import RequestValidator
        validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
        signature = request.META.get("HTTP_X_TWILIO_SIGNATURE", "")
        url = request.build_absolute_uri()
        return validator.validate(url, request.POST, signature)
    except Exception as exc:
        logger.error("Twilio signature validation error: %s", exc)
        return False
