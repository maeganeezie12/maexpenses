import re
from datetime import date as date_type

from utils import month_number

# All PayLah! alert templates (local transfer, overseas transaction, ...) share
# this key: value table — parsing off these lines generalizes across templates
# we haven't seen yet, instead of branching on the "Transfer"/"payment"/
# "Overseas transaction" wording of the intro sentence.
_SG_DATE_RE = re.compile(r"^SG Date & Time:\s*(\d{1,2})\s+([A-Za-z]+)", re.MULTILINE)
_DATE_RE = re.compile(r"^Date & Time:\s*(\d{1,2})\s+([A-Za-z]+)", re.MULTILINE)
_SGD_EQUIV_RE = re.compile(r"^SGD Equivalent:\s*SGD\s*([\d,]+\.\d{2})", re.MULTILINE)
_AMOUNT_RE = re.compile(r"^Amount(?:\s*Paid)?:\s*[A-Z]{3}\s*([\d,]+\.\d{2})", re.MULTILINE)
_FROM_RE = re.compile(r"^From:\s*(.+)$", re.MULTILINE)
_TO_RE = re.compile(r"^To:\s*(.+)$", re.MULTILINE)
_MOBILE_SUFFIX_RE = re.compile(r"\s*\(Mobile ending \d+\)\s*$")


def parse_paylah_body(body: str, received_date: date_type):
    """Returns None if the email doesn't match the expected PayLah! alert shape,
    {"outgoing": False} for money moving INTO the wallet (ignored — not a spend),
    or the full outgoing-transaction dict otherwise."""
    from_m = _FROM_RE.search(body)
    to_m = _TO_RE.search(body)
    if not from_m or not to_m:
        return None

    if "paylah! wallet" not in from_m.group(1).strip().lower():
        return {"outgoing": False}

    amount_m = _SGD_EQUIV_RE.search(body) or _AMOUNT_RE.search(body)
    if not amount_m:
        return None

    item = _MOBILE_SUFFIX_RE.sub("", to_m.group(1).strip()) or "PayLah! transaction"

    entry_date = received_date
    date_m = _SG_DATE_RE.search(body) or _DATE_RE.search(body)
    if date_m:
        month = month_number(date_m.group(2))
        if month:
            try:
                candidate = date_type(received_date.year, month, int(date_m.group(1)))
                # Handles polling a Dec 31 email just after midnight on Jan 1
                entry_date = candidate if candidate <= received_date else date_type(
                    received_date.year - 1, month, int(date_m.group(1))
                )
            except ValueError:
                pass

    return {
        "outgoing": True,
        "amount": float(amount_m.group(1).replace(",", "")),
        "item": item,
        "date": entry_date,
    }
