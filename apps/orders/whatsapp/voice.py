"""
Voice messages on WhatsApp: transcribed to text, then handled exactly like a typed message.

Voice is accepted only where a typed message would start or change an order, or update prices:
  - a customer's new order, or a change to today's open order;
  - a supplier's price update.
Everything that answers a question (approve, cancel, "arrived", "missing", which variant...) stays
on the buttons and typed words. So a voice message is turned away, before it is sent to anyone to be
transcribed, while the system is waiting for such an answer (see *_waiting_for_answer).

The road: webhook -> accept_voice_message (cheap checks, runs in the request) -> Celery task ->
process_voice_message (download, transcribe, then the same handlers as text). The audio is kept in
memory only; the transcript is kept for a few minutes so that a "which one did you mean" or a
"not understood" can say what was heard (heard_suffix) — never on an order that was understood.
"""
import logging
from urllib.parse import urlparse

import httpx
from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse

from core import stt

from . import validators

logger = logging.getLogger(__name__)

HEARD_TTL = 300
# Voice messages one phone may send per hour: the transcription costs money, so a stranger or a
# loop must not be able to run it up. Orders are a handful of messages a day.
VOICE_PER_HOUR = 30

_EXTENSIONS = {
    "audio/ogg": "ogg", "audio/opus": "ogg", "audio/mpeg": "mp3", "audio/mp3": "mp3",
    "audio/mp4": "m4a", "audio/m4a": "m4a", "audio/x-m4a": "m4a", "audio/wav": "wav",
    "audio/x-wav": "wav", "audio/webm": "webm", "audio/aac": "m4a", "audio/flac": "flac",
}

NOT_FOR_VOICE = "לשאלה הזו ענה בכפתור או בכתב. הודעות קוליות מתאימות להזמנות ולעדכוני מחירים."
COULD_NOT_HEAR = "לא הצלחתי לשמוע את ההקלטה. אפשר לשלוח שוב, או לכתוב."
NOTHING_HEARD = "לא שמעתי הזמנה בהקלטה. אפשר לשלוח שוב, או לכתוב."
TRY_AGAIN = "לא הצלחתי לעבד את ההודעה הקולית כרגע. אפשר לכתוב, או לנסות שוב בעוד רגע."


def _too_long_text() -> str:
    limit = stt.max_seconds()
    length = f"{limit // 60} דקות" if limit % 60 == 0 else f"{limit} שניות"
    return f"ההקלטה ארוכה מדי (עד {length}). אפשר לפצל לכמה הודעות קצרות, או לכתוב."


# ── What the message is ──────────────────────────────────────────────────────

def voice_media(post):
    """(url, content_type) of the first audio file Twilio attached, or None."""
    try:
        count = int(post.get("NumMedia", "0") or 0)
    except ValueError:
        return None
    for index in range(count):
        content_type = (post.get(f"MediaContentType{index}") or "").lower()
        url = post.get(f"MediaUrl{index}") or ""
        if content_type.startswith("audio/") and url:
            return url, content_type
    return None


# ── Is the system waiting for an answer? ─────────────────────────────────────

def customer_waiting_for_answer(phone: str) -> bool:
    """A question to this customer is open: their next message is an answer, not an order."""
    from .cache import (
        _get_delivery_state, _get_fallback_state, _get_reroute_grace_state,
        get_cancel_offer, get_merge_offer, get_modification_offer, get_pending_clarification,
    )

    return any((
        get_cancel_offer(phone), get_modification_offer(phone), get_merge_offer(phone),
        get_pending_clarification(phone), cache.get(f"whatsapp_order:{phone}"),
        _get_delivery_state(phone), _get_fallback_state(phone), _get_reroute_grace_state(phone),
    ))


def supplier_waiting_for_answer(phone: str) -> bool:
    """This supplier has a question or an order to answer: their next message is that answer."""
    from .cache import get_eta_request

    return any((
        get_eta_request(phone),
        cache.get(f"whatsapp_supplier_cancel_confirm:{phone}"),
        cache.get(f"whatsapp_supplier_pending:{phone}"),
    ))


def closed_hours_text(supplier, profile):
    """
    The "not now" message when this sender is outside their hours (suppliers: prices; customers:
    orders), else None. Checked before transcription, so a closed hour costs nothing.
    Staff are never turned away.
    """
    from apps.orders import hours

    if supplier is not None:
        return None if hours.suppliers_open() else "⏰ " + hours.supplier_closed_text()
    if profile is not None and not profile.user.is_staff and not hours.customers_open():
        return "⏰ " + hours.customer_closed_text()
    return None


# ── The transcript, for "what I heard" ───────────────────────────────────────

def _heard_key(phone: str) -> str:
    return f"voice_heard:{phone}"


def remember_transcript(phone: str, text: str) -> None:
    cache.set(_heard_key(phone), text, timeout=HEARD_TTL)


def forget_transcript(phone: str) -> None:
    """A typed message arrived: whatever was heard before is no longer what this is about."""
    cache.delete(_heard_key(phone))


def heard_suffix(phone: str) -> str:
    """'What I heard', to append to a message that asks for clarification or says something was not understood."""
    text = cache.get(_heard_key(phone))
    return f'\n\n🎤 שמעתי: "{text}"' if text else ""


# ── Entering from the webhook ────────────────────────────────────────────────

def _under_hourly_limit(phone: str) -> bool:
    key = f"voice_count:{phone}"
    cache.add(key, 0, timeout=3600)
    try:
        return cache.incr(key) <= VOICE_PER_HOUR
    except ValueError:  # the key expired between the two calls
        return True


def accept_voice_message(phone: str, supplier, media_url: str, content_type: str) -> HttpResponse:
    """
    Called by the webhook for a message with an audio attachment. Decides in the request (no audio
    is downloaded, nothing is paid for) whether voice is welcome here; if so, hands it to a worker,
    because downloading and transcribing take seconds and Twilio wants its answer fast.
    """
    from .user_flow import _resolve_profile

    profile = None if supplier else _resolve_profile(phone)
    if supplier is None and profile is None:
        validators.send_whatsapp_message(phone, "מספר הטלפון שלך לא רשום במערכת. פנה למנהל.")
        return HttpResponse(status=200)

    waiting = supplier_waiting_for_answer(phone) if supplier else customer_waiting_for_answer(phone)
    if waiting:
        validators.send_whatsapp_message(phone, NOT_FOR_VOICE)
        return HttpResponse(status=200)

    closed = closed_hours_text(supplier, profile)
    if closed:
        validators.send_whatsapp_message(phone, closed)
        return HttpResponse(status=200)

    if not _under_hourly_limit(phone):
        logger.warning("Voice message limit reached for %s", phone)
        validators.send_whatsapp_message(phone, "יותר מדי הודעות קוליות בשעה האחרונה. אפשר לכתוב.")
        return HttpResponse(status=200)

    try:
        from apps.orders.tasks import process_voice_message_task
        process_voice_message_task.delay(phone, supplier.id if supplier else None, media_url, content_type)
    except Exception as exc:
        logger.error("Could not schedule voice message processing for %s: %s", phone, exc)
        validators.send_whatsapp_message(phone, TRY_AGAIN)
    return HttpResponse(status=200)


# ── In the worker ────────────────────────────────────────────────────────────

def download_media(url: str) -> bytes:
    """
    The audio file from Twilio, into memory. Twilio's media needs the account's credentials, so
    only a twilio.com address over https is ever fetched (the URL arrives in a request body), and
    the credentials are not sent on to wherever Twilio redirects (httpx drops them on a new host).
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "twilio.com" or host.endswith(".twilio.com")):
        raise ValueError(f"not a Twilio media address: {host!r}")
    chunks, size = [], 0
    with httpx.stream(
        "GET", url, auth=(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN),
        follow_redirects=True, timeout=30,
    ) as response:
        response.raise_for_status()
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > stt.MAX_BYTES:
                raise ValueError("audio file too large")
            chunks.append(chunk)
    return b"".join(chunks)


HINT_NAMES_KEY = "voice_hint_names"
HINT_NAMES_TTL = 600


def hint_names() -> list:
    """
    The catalog's product names for the transcriber's hint: the most-ordered first, so that if the
    hint ever has to be cut it is the rarest products that fall off. Kept for a few minutes, since
    it counts the order lines and every voice message needs it.
    """
    from django.db.models import Count

    from apps.catalog.models import Product

    names = cache.get(HINT_NAMES_KEY)
    if names is None:
        names = list(
            Product.objects.annotate(ordered=Count("orderrequestproduct"))
            .order_by("-ordered", "name").values_list("name", flat=True)
        )
        cache.set(HINT_NAMES_KEY, names, timeout=HINT_NAMES_TTL)
    return names


def _filename(content_type: str) -> str:
    return "voice." + _EXTENSIONS.get(content_type.split(";")[0].strip(), "ogg")


def process_voice_message(phone: str, supplier_id, media_url: str, content_type: str) -> None:
    """Download, transcribe and hand the text to the same handler a typed message would reach."""
    try:
        audio = download_media(media_url)
    except ValueError as exc:
        logger.warning("Voice message from %s refused: %s", phone, exc)
        validators.send_whatsapp_message(phone, _too_long_text() if "large" in str(exc) else COULD_NOT_HEAR)
        return
    except Exception as exc:
        logger.error("Could not download voice message from %s: %s", phone, exc)
        validators.send_whatsapp_message(phone, COULD_NOT_HEAR)
        return

    seconds = stt.audio_seconds(audio)
    if seconds is not None and seconds > stt.max_seconds():
        validators.send_whatsapp_message(phone, _too_long_text())
        return

    try:
        text = stt.transcribe(
            audio, filename=_filename(content_type), content_type=content_type.split(";")[0].strip(),
            hint=stt.build_hint(hint_names()),
        )
    except stt.TranscriptionError as exc:
        logger.error("Transcription failed for %s: %s", phone, exc)
        validators.send_whatsapp_message(phone, TRY_AGAIN)
        return
    if not text.strip():
        validators.send_whatsapp_message(phone, NOTHING_HEARD)
        return

    supplier = None
    if supplier_id is not None:
        from apps.catalog.models import Supplier
        supplier = Supplier.objects.filter(id=supplier_id).first()
        if supplier is None:
            return

    # The seconds it took may have changed things: a question may have been asked meanwhile.
    waiting = supplier_waiting_for_answer(phone) if supplier else customer_waiting_for_answer(phone)
    if waiting:
        validators.send_whatsapp_message(phone, NOT_FOR_VOICE)
        return
    if supplier is None:
        from .user_flow import _resolve_profile
        closed = closed_hours_text(None, _resolve_profile(phone))
        if closed:  # the hour struck while it was being transcribed
            validators.send_whatsapp_message(phone, closed)
            return

    remember_transcript(phone, text)
    logger.info("Voice message from %s transcribed: %r", phone, text)
    try:
        if supplier:
            from .supplier_flow import _handle_supplier_price_update
            _handle_supplier_price_update(phone, supplier, text)
        else:
            from .user_flow import _handle_order_text
            _handle_order_text(phone, text)
    except Exception:
        logger.exception("Unhandled error processing transcribed voice message from %s: %r", phone, text)
        try:
            validators.send_whatsapp_message(phone, TRY_AGAIN)
        except Exception:
            pass
