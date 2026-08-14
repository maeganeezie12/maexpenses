import re
from datetime import date as date_type
from datetime import datetime, timedelta

import pytz

from config import TIMEZONE

_TZ = pytz.timezone(TIMEZONE)


def now_local():
    return datetime.now(_TZ)


def today_local():
    return now_local().date()


# ── Deterministic date extraction (used when the LLM is unavailable) ──────────

_WEEKDAYS = {
    "monday": 0, "mon": 0,
    "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3,
    "friday": 4, "fri": 4,
    "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

_WEEKDAY_PATTERN = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))

# Ordered (regex, handler) pairs tried in turn; the first match wins. Every
# pattern is wrapped in \b...\b so it only matches whole words/tokens embedded
# anywhere in a free-text message (e.g. "yesterday lunch 3.9", "grab 12 on fri").
_DATE_PATTERNS = [
    (re.compile(r"\btoday\b"), lambda m, today: today),
    (re.compile(r"\byesterday\b"), lambda m, today: today - timedelta(days=1)),
    (re.compile(r"\btomorrow\b"), lambda m, today: today + timedelta(days=1)),
    (
        re.compile(r"\b(?:last\s+|this\s+)?(" + _WEEKDAY_PATTERN + r")\b"),
        lambda m, today: _most_recent_weekday(_WEEKDAYS[m.group(1)], today),
    ),
    (
        re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
        lambda m, today: _safe_date(int(m.group(3)), int(m.group(2)), int(m.group(1))),
    ),
    (
        re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2})\b"),
        lambda m, today: _safe_date(2000 + int(m.group(3)), int(m.group(2)), int(m.group(1))),
    ),
    (
        re.compile(r"\b(\d{1,2})/(\d{1,2})\b"),
        lambda m, today: _safe_date(today.year, int(m.group(2)), int(m.group(1))),
    ),
    (
        re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_PATTERN + r")\b"),
        lambda m, today: _safe_date(today.year, _MONTHS[m.group(2)], int(m.group(1))),
    ),
    (
        re.compile(r"\b(" + _MONTH_PATTERN + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b"),
        lambda m, today: _safe_date(today.year, _MONTHS[m.group(1)], int(m.group(2))),
    ),
]


def _safe_date(year, month, day):
    try:
        return date_type(year, month, day)
    except ValueError:
        return None


def _most_recent_weekday(target: int, today):
    days_back = (today.weekday() - target) % 7
    return today - timedelta(days=days_back)


def extract_date(text: str, today):
    """Finds a date phrase anywhere in free text (deterministic, no LLM needed).

    Returns (date_or_None, remaining_text) — remaining_text has the matched
    phrase removed so downstream price/item parsing isn't confused by it.
    """
    lowered = text.lower()
    for pattern, handler in _DATE_PATTERNS:
        m = pattern.search(lowered)
        if not m:
            continue
        result = handler(m, today)
        if result is None:
            continue
        remaining = (text[: m.start()] + text[m.end() :]).strip(" ,-")
        remaining = re.sub(r"\b(on|at)\s*$", "", remaining).strip(" ,-")
        remaining = re.sub(r"^\s*(on|at)\b", "", remaining).strip(" ,-")
        remaining = re.sub(r"\s{2,}", " ", remaining)
        return result, remaining
    return None, text
