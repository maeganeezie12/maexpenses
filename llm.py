import json
import logging
import re
from datetime import date as date_type
from decimal import Decimal, DivisionByZero, InvalidOperation, ROUND_CEILING

from google import genai
from google.genai import types

from config import CATEGORIES, GEMINI_API_KEY
from utils import extract_date

logger = logging.getLogger(__name__)

# We never pass tools/functions, so the SDK's one-time AFC advisory (logged via
# this logger name) is a false positive — silence it rather than clutter the log.
logging.getLogger("google_genai.models").setLevel(logging.ERROR)

_MODEL = "gemini-flash-lite-latest"

_EXPENSE_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {
            "type": "string",
            "nullable": True,
            "description": "YYYY-MM-DD if a date/day is mentioned in the message, else null",
        },
        "item": {"type": "string", "description": "short description of what was purchased"},
        "price": {"type": "number", "description": "amount spent, as a positive number"},
        "category": {"type": "string", "enum": CATEGORIES},
    },
    "required": ["date", "item", "price", "category"],
}

_INCOME_SCHEMA = {
    "type": "object",
    "properties": {
        "date": {
            "type": "string",
            "nullable": True,
            "description": "YYYY-MM-DD if a date/day is mentioned in the message, else null",
        },
        "amount": {"type": "number", "description": "amount received, as a positive number"},
        "description": {"type": "string", "description": "free text describing the income source"},
    },
    "required": ["date", "amount", "description"],
}

_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

def find_explicit_category(text: str):
    """If the user's raw text already names one of the fixed categories
    (any case, whole word), return its canonically-cased form. This overrides
    whatever the LLM/fallback would have guessed, and skips the confirm-category
    keyboard entirely since the user already said exactly what they meant."""
    for category in CATEGORIES:
        if re.search(r"\b" + re.escape(category) + r"\b", text, re.IGNORECASE):
            return category
    return None


_CATEGORY_KEYWORDS = {
    "FNB": ["lunch", "dinner", "breakfast", "brunch", "coffee", "food", "restaurant", "hawker", "snack",
            "bubble tea", "milk tea", "grocery", "groceries", "makan"],
    "Transport": ["grab", "mrt", "bus", "taxi", "gojek", "petrol", "parking", "ez-link", "ezlink", "cab"],
    "Travel": ["flight", "hotel", "airbnb", "trip", "vacation", "airasia", "scoot", "visa fee"],
    "Shopping": ["shopee", "lazada", "clothes", "shirt", "shoes", "amazon", "taobao", "bag"],
    "Subscription": ["netflix", "spotify", "youtube", "subscription", "icloud", "chatgpt", "claude"],
    "Entertainment": ["movie", "cinema", "concert", "game", "steam", "karaoke", "ktv"],
    "Alcohol": ["beer", "wine", "whisky", "whiskey", "cocktail", "bar", "liquor"],
    "Gifts": ["gift", "present", "ang bao", "angbao"],
    "Hobbies": ["hobby", "craft", "gym", "yoga", "class"],
    "Investment": ["stock", "crypto", "invest", "etf", "bitcoin"],
    "Allowance": ["allowance", "pocket money"],
}


def _fallback_category(item: str) -> str:
    lowered = item.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(re.search(r"\b" + re.escape(kw) + r"\b", lowered) for kw in keywords):
            return category
    return "Misc"


# Chain of numbers joined by +, -, / (e.g. "100/3", "50+20-5") — division
# binds tighter than +/- so "100/3+5" is (100/3)+5, matching normal maths.
_ARITHMETIC_CHAIN = re.compile(r"\b\d+(?:\.\d+)?(?:\s*[+\-/]\s*\d+(?:\.\d+)?)+\b")
_ARITHMETIC_TOKEN = re.compile(r"\d+(?:\.\d+)?|[+\-/]")


def _eval_arithmetic(expr: str) -> Decimal:
    tokens = _ARITHMETIC_TOKEN.findall(expr)
    terms, signs = [], []
    current = Decimal(tokens[0])
    i = 1
    while i < len(tokens):
        op, num = tokens[i], Decimal(tokens[i + 1])
        if op == "/":
            current = current / num
        else:
            terms.append(current)
            signs.append(op)
            current = num
        i += 2
    terms.append(current)

    total = terms[0]
    for sign, term in zip(signs, terms[1:]):
        total = total + term if sign == "+" else total - term
    return total


def _apply_arithmetic(text: str, today: date_type) -> str:
    """Replaces a standalone +/-// amount expression with its computed
    value, rounded UP to 2dp (e.g. "100/3 lunch" -> "33.34 lunch"), so
    downstream price parsing (Gemini or the regex fallback) just sees a
    plain number. A pure "/"-only chain of 2 or 3 whole numbers that also
    parses as a valid DD/MM or DD/MM/YY(YY) date (e.g. "15/3" or
    "12/3/2025") is left alone, since dates never use +/- and that's the
    only real ambiguity with this feature."""
    m = _ARITHMETIC_CHAIN.search(text)
    if not m:
        return text

    expr = m.group()
    parts = re.split(r"\s*[+\-/]\s*", expr)
    ops = re.findall(r"[+\-/]", expr)

    if all(op == "/" for op in ops) and len(parts) in (2, 3) and all("." not in p for p in parts):
        try:
            day, month = int(parts[0]), int(parts[1])
            year = today.year
            if len(parts) == 3:
                year_str = parts[2]
                if len(year_str) == 4:
                    year = int(year_str)
                elif len(year_str) == 2:
                    year = 2000 + int(year_str)
                else:
                    year = None
            if year is not None:
                date_type(year, month, day)
                return text  # looks like a DD/MM(/YY(YY)) date -- let extract_date handle it
        except ValueError:
            pass

    try:
        value = _eval_arithmetic(expr).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
    except (InvalidOperation, DivisionByZero, ZeroDivisionError, IndexError):
        return text

    return text[: m.start()] + str(value) + text[m.end() :]


def _split_amount_and_text(text: str):
    match = re.search(r"[\d,]*\d(\.\d+)?", text)
    if not match:
        return 0.0, text.strip()
    try:
        amount = float(match.group().replace(",", ""))
    except ValueError:
        return 0.0, text.strip()
    remainder = (text[: match.start()] + text[match.end() :]).strip(" -,:")
    return amount, remainder


def _fallback_parse_expense(text: str, today: date_type):
    date_found, remaining = extract_date(text, today)
    price, item = _split_amount_and_text(remaining)
    item = item or "expense"
    return {"date": date_found, "item": item, "price": price, "category": _fallback_category(item)}


def _fallback_parse_income(text: str, today: date_type):
    date_found, remaining = extract_date(text, today)
    amount, description = _split_amount_and_text(remaining)
    return {"date": date_found, "amount": amount, "description": description or "income"}


async def parse_expense(text: str, today: date_type):
    """Parse a free-text expense message into date/item/price/category.

    Tries Gemini first for flexible language understanding; falls back to a
    simple regex + keyword parser if Gemini isn't configured or errors. If the
    user's raw text already names a fixed category explicitly, that always
    wins over whatever Gemini/fallback guessed (result["category_explicit"]
    tells the caller so it can skip the confirm-category keyboard).

    A leading arithmetic amount (+, -, /, e.g. "100/3 lunch" to split a bill
    three ways) is resolved to a plain number up front, rounded UP to 2dp,
    before either parser sees the text.
    """
    text = _apply_arithmetic(text, today)
    explicit_category = find_explicit_category(text)

    if _client:
        try:
            response = await _client.aio.models.generate_content(
                model=_MODEL,
                contents=text,
                config=types.GenerateContentConfig(
                    system_instruction=(
                        f"Today's date is {today.isoformat()} ({today.strftime('%A')}). "
                        "The user is logging a personal expense in free text, e.g. '3.9 lunch' "
                        "or '25 grab to office yesterday'. Extract: "
                        "date (YYYY-MM-DD if a date/day is mentioned, else null), "
                        "item (short description of the purchase), "
                        "price (the amount spent, as a positive number), "
                        f"and category (pick the single best match from exactly this list: "
                        f"{', '.join(CATEGORIES)})."
                    ),
                    response_mime_type="application/json",
                    response_schema=_EXPENSE_SCHEMA,
                    temperature=0,
                ),
            )
            data = json.loads(response.text)
            date_str = data.get("date")
            category = data.get("category")
            result = {
                "date": date_type.fromisoformat(date_str) if date_str else None,
                "item": data["item"],
                "price": float(data["price"]),
                "category": category if category in CATEGORIES else _fallback_category(data["item"]),
            }
            result["category"] = explicit_category or result["category"]
            result["category_explicit"] = explicit_category is not None
            return result
        except Exception:
            logger.exception("Gemini call failed while parsing expense")

    result = _fallback_parse_expense(text, today)
    result["category"] = explicit_category or result["category"]
    result["category_explicit"] = explicit_category is not None
    return result


async def parse_income(text: str, today: date_type):
    """Parse a free-text income message into date/amount/description.

    Tries Gemini first; falls back to a simple regex parser if Gemini isn't
    configured or errors. If the raw text is nothing but an amount (and
    optionally a date phrase, e.g. '3200' or '3200 yesterday'), the
    description defaults to "salary" regardless of what either parser
    guessed — salary is the income /income gets used for bare-number-only.
    """
    _, remaining = extract_date(text, today)
    _, remaining = _split_amount_and_text(remaining)
    bare_amount_only = not remaining.strip()

    if _client:
        try:
            response = await _client.aio.models.generate_content(
                model=_MODEL,
                contents=text,
                config=types.GenerateContentConfig(
                    system_instruction=(
                        f"Today's date is {today.isoformat()} ({today.strftime('%A')}). "
                        "The user is logging personal income in free text, e.g. '1221 salary'. "
                        "Extract: date (YYYY-MM-DD if a date/day is mentioned, else null), "
                        "amount (the amount received, as a positive number), "
                        "and description (free text describing the income source)."
                    ),
                    response_mime_type="application/json",
                    response_schema=_INCOME_SCHEMA,
                    temperature=0,
                ),
            )
            data = json.loads(response.text)
            date_str = data.get("date")
            return {
                "date": date_type.fromisoformat(date_str) if date_str else None,
                "amount": float(data["amount"]),
                "description": "salary" if bare_amount_only else data["description"],
            }
        except Exception:
            logger.exception("Gemini call failed while parsing income")

    result = _fallback_parse_income(text, today)
    if bare_amount_only:
        result["description"] = "salary"
    return result
