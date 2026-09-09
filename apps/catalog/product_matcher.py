"""
Fast, non-AI product-name resolution using data/product_aliases.json.

Tries to resolve each comma/newline-separated segment of a free-text message
(supplier price list or customer order) to a canonical catalog product name via
exact/alias lookup, before falling back to the AI parser for anything left over
(grade suffixes, typos, unknown products, ambiguous defaults like bare "בצל").
"""
import json
import re
from functools import lru_cache
from pathlib import Path

ALIASES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "product_aliases.json"

_NUMBER = r"\d+(?:\.\d+)?"
_QTY_UNIT_WORDS = r"(?:ק\"ג|קג|קילו|גרם|יחיד(?:ה|ות)|חבילות?|אגוד(?:ה|ות)|ארג(?:ז|זים))"

_ORDER_SEGMENT_RE = re.compile(rf"^(?P<number>{_NUMBER})\s*{_QTY_UNIT_WORDS}?\s+(?P<name>.+?)\s*$")
# The unit word is also allowed to trail the number here ("עגבניה 20 יחידות") —
# without this, a unit word anywhere in "name number [unit]" phrasing (not just
# "יחידות": also "קילו", "אגודות", "חבילות"...) broke the match entirely and
# fell through to the AI parser, since the old pattern required the number to
# be the very last thing in the segment.
_PRICE_SEGMENT_RE = re.compile(rf"^(?P<name>.+?)\s+(?P<number>{_NUMBER})\s*{_QTY_UNIT_WORDS}?\s*$")

# Words a supplier uses to say a product is out of stock, e.g. "עגבניות אין" or "אין עגבניות".
MISSING_KEYWORDS = ["חסר", "אין", "נגמר", "אזל", "לא קיים"]
_KEYWORD_ALT = "|".join(re.escape(k) for k in MISSING_KEYWORDS)
_AVAILABILITY_SUFFIX_RE = re.compile(rf"^(?P<name>.+?)\s+(?:{_KEYWORD_ALT})\S*\s*$")
_AVAILABILITY_PREFIX_RE = re.compile(rf"^(?:{_KEYWORD_ALT})\S*\s+(?P<name>.+?)\s*$")


def _normalize(text: str) -> str:
    return " ".join(text.strip().casefold().split())


@lru_cache(maxsize=1)
def _load_alias_index() -> dict:
    """normalized alias (or canonical name itself) -> canonical name."""
    index = {}
    try:
        with open(ALIASES_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return index
    for canonical, aliases in data.get("aliases", {}).items():
        index[_normalize(canonical)] = canonical
        for alias in aliases:
            index[_normalize(alias)] = canonical
    return index


def resolve_alias(text: str, known_product_names) -> str | None:
    """
    Exact-match lookup only (no fuzzy matching) — this is what makes it safe to
    skip the AI for. Only returns a name that's actually in `known_product_names`,
    so a stale/out-of-sync alias file can't resurrect a deleted product.
    """
    canonical = _load_alias_index().get(_normalize(text))
    if canonical and canonical in known_product_names:
        return canonical
    return None


def _extract_name_and_number(segment: str):
    """Try 'qty [unit] name' then 'name qty' patterns. Returns (name, number) or None."""
    m = _ORDER_SEGMENT_RE.match(segment)
    if m:
        return m.group("name").strip(), m.group("number")
    m = _PRICE_SEGMENT_RE.match(segment)
    if m:
        return m.group("name").strip(), m.group("number")
    return None


def _split_segments(message: str) -> list[str]:
    return [s.strip() for s in re.split(r"[,\n]", message) if s.strip()]


def _extract_availability_name(segment: str) -> str | None:
    """'עגבניות אין' / 'אין עגבניות' -> 'עגבניות'. Only for segments with no number."""
    m = _AVAILABILITY_SUFFIX_RE.match(segment)
    if m:
        return m.group("name").strip()
    m = _AVAILABILITY_PREFIX_RE.match(segment)
    if m:
        return m.group("name").strip()
    return None


def match_price_items(message: str, known_product_names) -> tuple[list[dict], list[dict], str]:
    """
    Fast pre-AI pass for supplier price messages.
    Returns (resolved, unavailable, remaining_message): resolved items are ready to
    use as-is (confidence="exact"); unavailable items are products the supplier says
    are out of stock (no price); remaining_message is what's left for the AI to parse.
    """
    known = set(known_product_names)
    resolved, unavailable, leftover = [], [], []
    for segment in _split_segments(message):
        extracted = _extract_name_and_number(segment)
        if extracted:
            name_candidate, number = extracted
            canonical = resolve_alias(name_candidate, known)
            if canonical:
                resolved.append({
                    "product_name": canonical,
                    "price": number,
                    "original": name_candidate,
                    "confidence": "exact",
                })
            else:
                leftover.append(segment)
            continue

        name_candidate = _extract_availability_name(segment)
        if name_candidate:
            canonical = resolve_alias(name_candidate, known)
            if canonical:
                unavailable.append({"product_name": canonical, "original": name_candidate})
                continue

        leftover.append(segment)
    return resolved, unavailable, ", ".join(leftover)


def match_order_items(message: str, known_product_names) -> tuple[list[dict], str]:
    """
    Fast pre-AI pass for customer order messages.
    Returns (resolved, remaining_message).
    """
    known = set(known_product_names)
    resolved, leftover = [], []
    for segment in _split_segments(message):
        extracted = _extract_name_and_number(segment)
        if not extracted:
            leftover.append(segment)
            continue
        name_candidate, number = extracted
        canonical = resolve_alias(name_candidate, known)
        if canonical:
            resolved.append({"product_name": canonical, "quantity": number})
        else:
            leftover.append(segment)
    return resolved, ", ".join(leftover)
