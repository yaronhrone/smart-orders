"""In-memory drafts and the daily send quotas. One process only (single
container); a restart drops open drafts, which just means asking again.

A draft has a kind ("order", "change" or "cancel"); a confirm tool only takes the
kinds it is meant for, so a draft of one kind can never be confirmed as another."""
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


def create(owner: str, payload, kind: str = "order") -> str:
    now = time.time()
    draft_id = secrets.token_urlsafe(9)
    with _lock:
        _purge(now)
        _drafts[draft_id] = {"owner": owner, "kind": kind, "payload": payload, "expires": now + DRAFT_TTL_SECONDS}
    return draft_id


def take(owner: str, draft_id: str, kinds=("order",)):
    """Remove and return (kind, payload). None if it is missing, expired, someone else's or of another kind.
    Taking it first is what makes a double confirm impossible; a refused take removes nothing."""
    now = time.time()
    with _lock:
        _purge(now)
        draft = _drafts.get(draft_id)
        if draft is None or draft["owner"] != owner or draft["kind"] not in kinds:
            return None
        del _drafts[draft_id]
        return draft["kind"], draft["payload"]


def restore(owner: str, draft_id: str, kind: str, payload):
    """Put a draft back after a failure that changed nothing."""
    with _lock:
        _drafts[draft_id] = {"owner": owner, "kind": kind, "payload": payload, "expires": time.time() + DRAFT_TTL_SECONDS}


def sent_today(owner: str, bucket: str = "order") -> int:
    with _lock:
        return _sent.get((owner, bucket, date.today()), 0)


def record_sent(owner: str, bucket: str = "order"):
    with _lock:
        key = (owner, bucket, date.today())
        _sent[key] = _sent.get(key, 0) + 1
        for old in [k for k in _sent if k[2] != date.today()]:
            del _sent[old]
