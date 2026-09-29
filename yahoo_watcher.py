import email as email_lib
import imaplib
import json
import logging
import os
import re
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser

import pytz

from config import IBANKING_SENDER, PAYLAH_SENDER, TIMEZONE, YAHOO_APP_PASSWORD, YAHOO_EMAIL
from paylah_parser import parse_paylah_body

logger = logging.getLogger(__name__)

_SEEN_FILE = os.path.join(os.path.dirname(__file__), "seen_yahoo_ids.json")
_TZ = pytz.timezone(TIMEZONE)

# "via PayNow"/"via FAST transfer"/etc. — DBS uses several transfer rail
# names for an incoming credit; the sender + "received SGD X" is what matters.
_PAYNOW_AMOUNT_RE = re.compile(r"received SGD\s*([\d,]+\.\d{2})", re.IGNORECASE)
_PAYNOW_FROM_RE = re.compile(r"^From:\s*(.+)$", re.MULTILINE)


class _HTMLTextExtractor(HTMLParser):
    """Converts DBS's alert HTML (a mix of <table>/<td> rows and <br>-separated
    paragraphs) into the same line-per-field plain text a mail client would
    show when copy-pasting the rendered email — which is the shape both
    paylah_parser and the PayNow regexes above expect."""

    _NEWLINE_TAGS = {"tr", "br", "p", "div"}
    _SKIP_TAGS = {"style", "script"}

    def __init__(self):
        super().__init__()
        self._chunks = []
        self._skip_tag = None

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_tag = tag
        elif tag in self._NEWLINE_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag):
        if tag == self._skip_tag:
            self._skip_tag = None
        elif tag == "td":
            self._chunks.append(" ")

    def handle_data(self, data):
        if not self._skip_tag:
            self._chunks.append(data)

    def get_text(self) -> str:
        text = unescape("".join(self._chunks))
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n[ \t]*", "\n", text)
        text = re.sub(r"\n{2,}", "\n", text)
        return text.strip()


def _html_to_text(html: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    return parser.get_text()


def _load_seen() -> list:
    if not os.path.exists(_SEEN_FILE):
        return []
    with open(_SEEN_FILE) as f:
        return json.load(f)


def _save_seen(ids: list):
    with open(_SEEN_FILE, "w") as f:
        json.dump(ids[-500:], f)


def _decode_payload(part) -> str:
    payload = part.get_payload(decode=True)
    return payload.decode(part.get_content_charset() or "utf-8", errors="replace") if payload else ""


def _extract_text(msg) -> str:
    """DBS's alert emails are HTML-only (no text/plain part) — text/plain is
    preferred when present, otherwise the text/html part is converted."""
    parts = list(msg.walk()) if msg.is_multipart() else [msg]

    for part in parts:
        if part.get_content_type() == "text/plain":
            text = _decode_payload(part)
            if text:
                return text

    for part in parts:
        if part.get_content_type() == "text/html":
            html = _decode_payload(part)
            if html:
                return _html_to_text(html)

    return ""


def _received_date(msg):
    date_header = msg.get("Date")
    if date_header:
        try:
            dt = parsedate_to_datetime(date_header)
            if dt.tzinfo is None:
                dt = pytz.UTC.localize(dt)
            return dt.astimezone(_TZ).date()
        except (TypeError, ValueError):
            pass
    return datetime.now(_TZ).date()


def _parse_paynow_body(body: str):
    amount_m = _PAYNOW_AMOUNT_RE.search(body)
    from_m = _PAYNOW_FROM_RE.search(body)
    if not amount_m or not from_m:
        return None
    return {"amount": float(amount_m.group(1).replace(",", "")), "sender": from_m.group(1).strip()}


def poll_alerts() -> dict:
    """Polls the configured Yahoo Mail inbox for both PayLah! spend alerts and
    PayNow-received alerts. Returns {"paylah": [...], "paynow": [...]}:
      - paylah entries: {"type": "outgoing", "amount", "item", "date"} for a
        detected spend, or {"type": "unparsed", "snippet"} if the email matched
        the sender but not the expected shape. Incoming (top-up) alerts are
        matched but dropped.
      - paynow entries: {"amount", "sender"} — FYI-only, never written to a
        sheet and never need acknowledgement.
    """
    empty = {"paylah": [], "paynow": []}
    if not YAHOO_EMAIL or not YAHOO_APP_PASSWORD:
        return empty

    try:
        conn = imaplib.IMAP4_SSL("imap.mail.yahoo.com")
    except Exception:
        logger.exception("Could not connect to Yahoo Mail IMAP")
        return empty

    seen = _load_seen()
    seen_set = set(seen)
    new_seen = list(seen)
    paylah_results = []
    paynow_results = []

    try:
        conn.login(YAHOO_EMAIL, YAHOO_APP_PASSWORD)
        conn.select("INBOX")
        since = (datetime.now(_TZ) - timedelta(days=2)).strftime("%d-%b-%Y")

        uids = set()
        for sender in (PAYLAH_SENDER, IBANKING_SENDER):
            status, data = conn.search(None, f'(FROM "{sender}" SINCE {since})')
            if status == "OK" and data and data[0]:
                uids.update(data[0].split())

        for uid in uids:
            # Cheap header-only check first — most uids in the window are
            # ones already broadcast on an earlier poll, so only messages
            # that are new since the last interval pay for a full fetch.
            status, header_data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID)])")
            if status != "OK" or not header_data or not header_data[0]:
                continue
            message_id = email_lib.message_from_bytes(header_data[0][1]).get("Message-ID") or uid.decode()
            if message_id in seen_set:
                continue
            new_seen.append(message_id)
            seen_set.add(message_id)

            status, msg_data = conn.fetch(uid, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            msg = email_lib.message_from_bytes(msg_data[0][1])

            from_header = (msg.get("From") or "").lower()
            body = _extract_text(msg)

            if PAYLAH_SENDER in from_header:
                parsed = parse_paylah_body(body, _received_date(msg))
                if parsed is None:
                    paylah_results.append({"type": "unparsed", "snippet": body[:200]})
                elif parsed["outgoing"]:
                    paylah_results.append({"type": "outgoing", **parsed})
            elif IBANKING_SENDER in from_header:
                parsed = _parse_paynow_body(body)
                if parsed:
                    paynow_results.append(parsed)
    except Exception:
        logger.exception("Yahoo Mail poll failed")
    finally:
        try:
            conn.logout()
        except Exception:
            pass

    _save_seen(new_seen)
    return {"paylah": paylah_results, "paynow": paynow_results}
