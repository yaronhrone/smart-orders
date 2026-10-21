"""In-memory drafts and the daily send quota. One process only (single
container); a restart drops open drafts, which just means asking again."""
import secrets
import threading
import time
from datetime import date

DRAFT_TTL_SECONDS = 15 * 60

_lock = threading.Lock()
_drafts: dict = {}
_sent: dict = {}


def _purge(now):
    for key in [k for k, d in _drafts.items() if d["expires"] < now]:
        del _drafts[key]


def create(owner: str, items: list) -> str:
    now = time.time()
    draft_id = secrets.token_urlsafe(9)
    with _lock:
        _purge(now)
        _drafts[draft_id] = {"owner": owner, "items": items, "expires": now + DRAFT_TTL_SECONDS}
    return draft_id


def take(owner: str, draft_id: str):
    """Remove and return a draft's items. None if it is missing, expired or someone else's.
    Taking it first is what makes a double confirm impossible."""
    now = time.time()
    with _lock:
        _purge(now)
        draft = _drafts.get(draft_id)
        if draft is None or draft["owner"] != owner:
            return None
        del _drafts[draft_id]
        return draft["items"]


def restore(owner: str, draft_id: str, items: list):
    """Put a draft back after a failure that sent nothing."""
    with _lock:
        _drafts[draft_id] = {"owner": owner, "items": items, "expires": time.time() + DRAFT_TTL_SECONDS}


def sent_today(owner: str) -> int:
    with _lock:
        return _sent.get((owner, date.today()), 0)


def record_sent(owner: str):
    with _lock:
        key = (owner, date.today())
        _sent[key] = _sent.get(key, 0) + 1
        for old in [k for k in _sent if k[1] != date.today()]:
            del _sent[old]
