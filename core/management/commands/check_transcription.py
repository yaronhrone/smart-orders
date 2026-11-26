"""
Try the speech-to-text on real recordings, against the real provider, without WhatsApp.

    python manage.py check_transcription /samples                 # a folder of recordings (or one file)
    python manage.py check_transcription /samples --order         # also: what order would come out of it
    python manage.py check_transcription /samples --models gpt-4o-transcribe,whisper-1   # compare models

Next to each recording, `01.ogg`, an optional `01.txt` holds what was really said. With it the
command prints the word error rate; with --order it also checks the thing that matters more:
whether the transcript gives the SAME order as the true text (a spoken "חמישה" against a written
"5" counts as a word error, but still gives the right order).

It calls the paid API (OpenAI by default: cents per minute of audio; --order adds the order
parser's AI calls). Nothing is sent to WhatsApp and no order is created.
"""
import os
import re
import time
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from core import stt

AUDIO_TYPES = {
    ".ogg": "audio/ogg", ".opus": "audio/ogg", ".mp3": "audio/mpeg", ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4", ".wav": "audio/wav",
    ".webm": "audio/webm", ".flac": "audio/flac",
}
_PUNCTUATION = re.compile(r"[\s.,!?;:\"'()\[\]׳״־\-–—…]+")


def words(text: str) -> list:
    """A text as comparable words: no punctuation, lower case."""
    return [w for w in _PUNCTUATION.split(text.lower()) if w]


def word_error_rate(expected: str, heard: str) -> float:
    """Edits (replace, drop, add a word) to turn `heard` into `expected`, over the length of `expected`."""
    ref, hyp = words(expected), words(heard)
    if not ref:
        return 0.0 if not hyp else 1.0
    previous = list(range(len(hyp) + 1))
    for i, ref_word in enumerate(ref, 1):
        current = [i]
        for j, hyp_word in enumerate(hyp, 1):
            current.append(min(
                previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ref_word != hyp_word),
            ))
        previous = current
    return previous[-1] / len(ref)


def order_of(text: str, product_names: list):
    """
    What the order parser makes of a text, as something comparable:
    a set of (product, quantity), plus ("?", family) for a product it would ask about,
    or the string "not understood".
    """
    from apps.orders.order_parser import AmbiguousProductError, parse_customer_order

    try:
        items = parse_customer_order(text, product_names)
    except AmbiguousProductError as exc:
        return frozenset(
            [(i["product_name"], Decimal(str(i["quantity"]))) for i in exc.resolved]
            + [("?", a["query"]) for a in exc.ambiguous]
        )
    except ValueError:
        return "not understood"
    return frozenset((i["product_name"], Decimal(str(i["quantity"]))) for i in items)


def _show(order) -> str:
    if isinstance(order, str):
        return order
    return ", ".join(f"{name} x{format(qty.normalize(), 'f') if isinstance(qty, Decimal) else qty}" for name, qty in sorted(order, key=str))


class Command(BaseCommand):
    help = "Transcribe real recordings with the configured provider and report accuracy."

    def add_arguments(self, parser):
        parser.add_argument("path", help="a recording, or a folder of recordings (.ogg .opus .mp3 .m4a .wav ...)")
        parser.add_argument("--models", default="", help="comma-separated models to compare (default: the configured one)")
        parser.add_argument("--order", action="store_true", help="also parse the transcript into an order (uses the AI parser)")
        parser.add_argument("--no-hint", action="store_true", help="do not give the product names to the transcriber")

    def handle(self, *args, **options):
        files = self._files(Path(options["path"]))
        names = self._product_names(options)
        models = [m.strip() for m in options["models"].split(",") if m.strip()] or [stt.model()]

        self.stdout.write(f"{len(files)} recording(s), provider {stt.provider()}, models: {', '.join(models)}")
        if not names:
            self.stdout.write("(no product names: the transcriber gets no hint, and --order cannot match products)")
        original = os.environ.get("STT_MODEL")
        try:
            for model in models:
                os.environ["STT_MODEL"] = model
                self._run_model(model, files, names, options["order"], hint=not options["no_hint"])
        finally:
            if original is None:
                os.environ.pop("STT_MODEL", None)
            else:
                os.environ["STT_MODEL"] = original

    # ── input ──

    def _files(self, path: Path) -> list:
        if not path.exists():
            raise CommandError(f"{path} does not exist (when running in Docker, mount the folder: see the usage note)")
        found = [path] if path.is_file() else sorted(p for p in path.iterdir() if p.suffix.lower() in AUDIO_TYPES)
        if not found:
            raise CommandError(f"no recordings in {path}")
        return found

    def _product_names(self, options) -> list:
        if options["no_hint"] and not options["order"]:
            return []
        try:
            from apps.catalog.models import Product
            return list(Product.objects.values_list("name", flat=True))
        except Exception as exc:  # no database reachable: still useful without the catalog
            self.stderr.write(f"could not read the catalog ({type(exc).__name__}): continuing without product names")
            return []

    # ── one model over all files ──

    def _run_model(self, model, files, names, with_order, hint):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== {model} ==="))
        hint_text = stt.build_hint(names) if hint else ""
        results = []
        for path in files:
            results.append(self._one(path, names, with_order, hint_text, model))
        self._summary(model, results, with_order)

    def _one(self, path, names, with_order, hint_text, model) -> dict:
        audio = path.read_bytes()
        seconds = stt.audio_seconds(audio)
        content_type = AUDIO_TYPES[path.suffix.lower()]
        filename = "voice.ogg" if content_type == "audio/ogg" else f"voice{path.suffix.lower()}"
        expected_file = path.with_suffix(".txt")
        expected = expected_file.read_text(encoding="utf-8").strip() if expected_file.exists() else None
        result = {"name": path.name, "seconds": seconds, "expected": expected, "wer": None, "order_ok": None, "error": None}

        self.stdout.write(f"\n{path.name}" + (f"  ({seconds:.0f}s)" if seconds is not None else ""))
        if seconds is not None and seconds > stt.max_seconds():
            self.stdout.write(self.style.WARNING(f"  longer than the {stt.max_seconds()}s limit: the system would refuse it"))
        started = time.monotonic()
        try:
            heard = stt.transcribe(audio, filename=filename, content_type=content_type, hint=hint_text)
        except stt.TranscriptionError as exc:
            result["error"] = str(exc)
            self.stdout.write(self.style.ERROR(f"  FAILED: {exc}"))
            return result
        result["elapsed"] = time.monotonic() - started
        result["heard"] = heard
        self.stdout.write(f"  heard:    {heard or '(nothing)'}")
        if expected is not None:
            result["wer"] = word_error_rate(expected, heard)
            self.stdout.write(f"  expected: {expected}")
            self.stdout.write(f"  word errors: {result['wer']:.0%}   ({result['elapsed']:.1f}s)")
        else:
            self.stdout.write(f"  (no {expected_file.name} next to it: accuracy not measured)   ({result['elapsed']:.1f}s)")

        if with_order and heard:
            order = order_of(heard, names)
            self.stdout.write(f"  order from what was heard: {_show(order)}")
            if expected is not None:
                wanted = order_of(expected, names)
                result["order_ok"] = order == wanted
                mark = "SAME order" if result["order_ok"] else f"DIFFERENT; the true text gives: {_show(wanted)}"
                self.stdout.write((self.style.SUCCESS if result["order_ok"] else self.style.ERROR)(f"  {mark}"))
        return result

    def _summary(self, model, results, with_order):
        ok = [r for r in results if not r["error"]]
        measured = [r for r in ok if r["wer"] is not None]
        audio_seconds = sum(r["seconds"] or 0 for r in ok)
        cost = audio_seconds / 60 * stt.PRICE_PER_MINUTE.get(model, 0)
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n--- {model}: summary ---"))
        self.stdout.write(f"transcribed {len(ok)}/{len(results)}" + (f", {len(results) - len(ok)} failed" if len(ok) < len(results) else ""))
        if measured:
            self.stdout.write(f"average word errors: {sum(r['wer'] for r in measured) / len(measured):.0%} over {len(measured)} recordings with a .txt")
        if with_order:
            checked = [r for r in ok if r["order_ok"] is not None]
            if checked:
                self.stdout.write(f"same order as the true text: {sum(r['order_ok'] for r in checked)}/{len(checked)}")
        if ok:
            self.stdout.write(f"average time: {sum(r['elapsed'] for r in ok) / len(ok):.1f}s per recording")
        self.stdout.write(f"audio: {audio_seconds / 60:.1f} min, estimated cost ~${cost:.3f} (estimate, not an invoice)")
