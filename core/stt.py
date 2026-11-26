"""
Speech-to-text for WhatsApp voice messages, chosen in .env like the AI model (see core/llm.py):

    STT_PROVIDER=openai     (default)  key: STT_API_KEY, else OPENAI_API_KEY   model gpt-4o-transcribe
    STT_MODEL=...           optional, overrides the provider's default model

Callers use transcribe() and never see which provider answered. To try another provider, add its
function to _PROVIDERS and its default model to DEFAULT_MODELS: nothing else changes. Restart the
web and worker containers after a change in .env.

The audio is never stored: it is downloaded into memory, sent for transcription and dropped.
"""
import logging
import os
import struct
import time

logger = logging.getLogger(__name__)

OPENAI = "openai"
DEFAULT_MODELS = {OPENAI: "gpt-4o-transcribe"}

# USD per minute of audio. An ESTIMATE for the usage page, not an invoice: check the provider's
# price list and correct here. A model missing from the table is recorded with cost 0.
PRICE_PER_MINUTE = {"gpt-4o-transcribe": 0.006}

# Longest voice message accepted (seconds). Longer ones are refused before they are sent anywhere.
DEFAULT_MAX_SECONDS = 180
# Size guard for audio whose length cannot be read (anything but ogg/opus): ~3 minutes of a
# typical compressed voice note is well under this, and the API refuses files over 25 MB anyway.
MAX_BYTES = 8 * 1024 * 1024
# How much of the product list goes in as a hint (characters). The whole catalog should fit: names
# the hint leaves out (קולורבי, שמיר, פלפל כתום) were the ones heard wrongly. The API took prompts
# of 8000 characters without complaint; if the catalog outgrows this, the products customers order
# most come first (see voice.hint_names) and the rarest are the ones left out.
HINT_MAX_CHARS = 3000


class TranscriptionError(Exception):
    """The audio could not be turned into text (provider down, bad key, unreadable file)."""


def max_seconds() -> int:
    try:
        return int(os.environ.get("STT_MAX_SECONDS", DEFAULT_MAX_SECONDS))
    except ValueError:
        return DEFAULT_MAX_SECONDS


def provider() -> str:
    name = os.environ.get("STT_PROVIDER", OPENAI).strip().lower()
    if name not in DEFAULT_MODELS:
        raise ValueError(f"STT_PROVIDER לא מוכר: {name!r} ({' / '.join(DEFAULT_MODELS)})")
    return name


def model() -> str:
    return os.environ.get("STT_MODEL", "").strip() or DEFAULT_MODELS[provider()]


def _api_key() -> str:
    key = os.environ.get("STT_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise TranscriptionError("no API key for speech-to-text (STT_API_KEY / OPENAI_API_KEY)")
    return key


def ogg_opus_seconds(data: bytes):
    """
    Length of an ogg/opus recording (what WhatsApp voice notes are), read from the last page's
    granule position (48 kHz samples) without decoding. None when the data is not readable ogg.
    """
    if data[:4] != b"OggS":
        return None
    last = data.rfind(b"OggS")
    if last < 0 or len(data) < last + 14:
        return None
    granule = struct.unpack_from("<q", data, last + 6)[0]
    if granule <= 0:
        return None
    return granule / 48000.0


def audio_seconds(data: bytes):
    """Length in seconds when it can be known, else None."""
    return ogg_opus_seconds(data)


def build_hint(product_names) -> str:
    """A short Hebrew prompt naming the catalog products, so brand and produce names come out right."""
    names = []
    total = 0
    for name in product_names:
        total += len(name) + 2
        if total > HINT_MAX_CHARS:
            break
        names.append(name)
    if not names:
        return ""
    return "הזמנת ירקות ופירות. שמות מוצרים אפשריים: " + ", ".join(names) + "."


def _transcribe_openai(audio: bytes, filename: str, content_type: str, hint: str) -> str:
    from openai import OpenAI

    client = OpenAI(api_key=_api_key(), timeout=90, max_retries=1)
    kwargs = {"model": model(), "file": (filename, audio, content_type), "language": "he"}
    if hint:
        kwargs["prompt"] = hint
    result = client.audio.transcriptions.create(**kwargs)
    return (getattr(result, "text", None) or "").strip()


_PROVIDERS = {OPENAI: _transcribe_openai}


def _record_usage(started: float, seconds, ok: bool) -> None:
    """One AIUsage row per transcription (metadata only: never the audio or the text)."""
    try:
        from core.models import AIUsage

        model_name = model()
        minutes = (seconds or 0) / 60.0
        AIUsage.objects.create(
            kind="voice_transcribe", provider=provider(), model=model_name,
            # No tokens: audio is priced per minute, and the usage page adds tokens up as text.
            cost_usd=round(minutes * PRICE_PER_MINUTE.get(model_name, 0), 6),
            duration_ms=int((time.monotonic() - started) * 1000), ok=ok,
        )
    except Exception:  # recording must never break the call it measures
        logger.warning("could not record transcription usage", exc_info=True)


def transcribe(audio: bytes, filename: str = "voice.ogg", content_type: str = "audio/ogg", hint: str = "") -> str:
    """
    The text of a voice message, in Hebrew. "" when nothing intelligible was said.
    Raises TranscriptionError when the provider could not do it.
    """
    started = time.monotonic()
    seconds = audio_seconds(audio)
    try:
        text = _PROVIDERS[provider()](audio, filename, content_type, hint)
    except TranscriptionError:
        _record_usage(started, seconds, ok=False)
        raise
    except Exception as exc:
        _record_usage(started, seconds, ok=False)
        raise TranscriptionError(str(exc)) from exc
    _record_usage(started, seconds, ok=True)
    logger.info("STT %s %s: %.1fs audio, %d chars", provider(), model(), seconds or 0, len(text))
    return text
