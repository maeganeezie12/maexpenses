import json
import os

import sheets
from config import IN_SHEET_NAME, OUT_SHEET_NAME

_SEEN_RED_FILE = os.path.join(os.path.dirname(__file__), "seen_red_rows.json")

# (sheet name, column count) for every tab the daily audit scans.
SHEET_SPECS = [(OUT_SHEET_NAME, 4), (IN_SHEET_NAME, 3)]


def _load_seen() -> dict:
    if not os.path.exists(_SEEN_RED_FILE):
        return {}
    with open(_SEEN_RED_FILE) as f:
        return json.load(f)


def _save_seen(seen: dict):
    with open(_SEEN_RED_FILE, "w") as f:
        json.dump(seen, f)


def run_daily_check():
    """Scans every tab in SHEET_SPECS and returns (gap_rows, missing_category_rows, new_red_rows).

    gap_rows: rows where column A (date) is blank but B or C has data — these
    are at risk of being silently overwritten by the next bot write.

    missing_category_rows: rows (in tabs with a category column, i.e. "out")
    where B or C has data but the category (column D) is blank.

    new_red_rows: rows with red text (an Apps Script automated addition) that
    haven't been reported before. Rows drop out of tracking once acknowledged
    (turned black) or edited back to black by hand.
    """
    seen = _load_seen()
    gap_rows = []
    missing_category_rows = []
    new_red_rows = []

    for sheet_name, num_cols in SHEET_SPECS:
        details = sheets.get_row_details(sheet_name, num_cols)
        already_seen = set(seen.get(sheet_name, []))
        still_red = []

        for d in details:
            values = d["values"]
            b_or_c_filled = any(v for v in values[1:3])

            if not values[0] and b_or_c_filled:
                gap_rows.append({"sheet": sheet_name, "row": d["row"], "values": values})

            if num_cols >= 4 and b_or_c_filled and not values[3]:
                missing_category_rows.append({"sheet": sheet_name, "row": d["row"], "values": values})

            if d["is_red"]:
                still_red.append(d["row"])
                if d["row"] not in already_seen:
                    new_red_rows.append({"sheet": sheet_name, "row": d["row"], "values": values})

        seen[sheet_name] = still_red

    _save_seen(seen)
    return gap_rows, missing_category_rows, new_red_rows
