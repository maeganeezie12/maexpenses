import json
import os
from datetime import date

_STATE_FILE = os.path.join(os.path.dirname(__file__), "salary_reminder.json")

_REMINDER_START_DAY = 25


def _load() -> dict:
    if not os.path.exists(_STATE_FILE):
        return {}
    with open(_STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(state: dict):
    with open(_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def salary_transfer_due(today: date) -> bool:
    """True from the 25th through the end of the month, unless this month's
    transfer has already been marked done — in which case it stays quiet
    until the 25th rolls around again next month."""
    if today.day < _REMINDER_START_DAY:
        return False
    state = _load()
    return state.get("acked_month") != today.strftime("%Y-%m")


def acknowledge_salary_transfer(today: date):
    state = _load()
    state["acked_month"] = today.strftime("%Y-%m")
    _save(state)
