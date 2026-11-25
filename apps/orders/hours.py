"""
Operating hours, in Israel time (Asia/Jerusalem; the project's TIME_ZONE is UTC, so every check
converts explicitly, and daylight saving time takes care of itself).

    suppliers update their prices   03:00 - 10:00   (open at 03:00, closed from 10:00)
    customers order, change orders  10:00 - 22:00   (open at 10:00, closed from 22:00)

Between 22:00 and 03:00 both are closed. The two windows never overlap.

The times are in .env so they can change without code (HH:MM):
    SUPPLIER_OPEN / SUPPLIER_CLOSE, CUSTOMER_OPEN / CUSTOMER_CLOSE
and HOURS_LIMIT_ENABLED=0 turns the whole limit off in an emergency (restart web and worker).

Staff (admins) are never limited: callers check `user.is_staff` before turning anyone away.
Shabbat and holidays are not handled yet (a decision for later): this is the one place to add them.
"""
import os
from datetime import time
from zoneinfo import ZoneInfo

from django.utils import timezone

IL_TZ = ZoneInfo("Asia/Jerusalem")

DEFAULTS = {
    "SUPPLIER_OPEN": time(3, 0),
    "SUPPLIER_CLOSE": time(10, 0),
    "CUSTOMER_OPEN": time(10, 0),
    "CUSTOMER_CLOSE": time(22, 0),
}


def _configured(name: str) -> time:
    raw = os.environ.get(name, "").strip()
    if raw:
        try:
            hour, minute = raw.split(":")
            return time(int(hour), int(minute))
        except ValueError:
            pass  # a typo in .env falls back to the default rather than breaking every message
    return DEFAULTS[name]


def enabled() -> bool:
    return os.environ.get("HOURS_LIMIT_ENABLED", "1").strip() != "0"


def customer_window() -> tuple:
    return _configured("CUSTOMER_OPEN"), _configured("CUSTOMER_CLOSE")


def supplier_window() -> tuple:
    return _configured("SUPPLIER_OPEN"), _configured("SUPPLIER_CLOSE")


def _inside(window: tuple, now=None) -> bool:
    start, end = window
    local = (now or timezone.now()).astimezone(IL_TZ).time()
    if start <= end:
        return start <= local < end
    return local >= start or local < end  # a window that crosses midnight


def customers_open(now=None) -> bool:
    return not enabled() or _inside(customer_window(), now)


def suppliers_open(now=None) -> bool:
    return not enabled() or _inside(supplier_window(), now)


def label(value: time) -> str:
    return value.strftime("%H:%M")


def customer_closed_text() -> str:
    start, end = customer_window()
    return (
        f"ההזמנות פתוחות בין {label(start)} ל-{label(end)}. "
        f"אנא שלח את ההזמנה בשעות האלה ולא לפני. ההודעה לא נקלטה."
    )


def supplier_closed_text() -> str:
    start, end = supplier_window()
    return (
        f"עדכון מחירים אפשרי רק בין {label(start)} ל-{label(end)}. "
        f"אנא אל תשלח עדכוני מחירים מחוץ לשעות האלה. המחירים לא עודכנו."
    )
