import json
import os
from datetime import date as date_type

from google.oauth2 import service_account
from googleapiclient.discovery import build

from config import IN_SHEET_NAME, OUT_SHEET_NAME, SERVICE_ACCOUNT_FILE, SPREADSHEET_ID

_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
_STATE_FILE = os.path.join(os.path.dirname(__file__), "last_entry.json")

_creds = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=_SCOPES)
_service = build("sheets", "v4", credentials=_creds)
_sheets = _service.spreadsheets()

_sheet_props_cache = {}


def _get_sheet_props(title: str) -> dict:
    if title not in _sheet_props_cache:
        meta = _sheets.get(spreadsheetId=SPREADSHEET_ID).execute()
        for s in meta["sheets"]:
            _sheet_props_cache[s["properties"]["title"]] = s["properties"]
    return _sheet_props_cache[title]


def _get_sheet_id(title: str) -> int:
    return _get_sheet_props(title)["sheetId"]


def get_sheet_url(sheet_name: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/edit#gid={_get_sheet_id(sheet_name)}"


def _copy_row_format(sheet_name: str, from_row: int, to_row: int, num_cols: int):
    sheet_id = _get_sheet_id(sheet_name)
    _sheets.batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "requests": [
                {
                    "copyPaste": {
                        "source": {
                            "sheetId": sheet_id,
                            "startRowIndex": from_row - 1,
                            "endRowIndex": from_row,
                            "startColumnIndex": 0,
                            "endColumnIndex": num_cols,
                        },
                        "destination": {
                            "sheetId": sheet_id,
                            "startRowIndex": to_row - 1,
                            "endRowIndex": to_row,
                            "startColumnIndex": 0,
                            "endColumnIndex": num_cols,
                        },
                        "pasteType": "PASTE_FORMAT",
                    }
                }
            ]
        },
    ).execute()


def _next_row(sheet_name: str) -> int:
    # Some tabs (e.g. "out") have ARRAYFORMULA columns (month/week starting)
    # that spill placeholder values far past the real last row of data, which
    # confuses the append() endpoint's own table-detection. Column A (date)
    # only ever has real entries, so its length is the reliable anchor.
    result = _sheets.values().get(spreadsheetId=SPREADSHEET_ID, range=f"{sheet_name}!A:A").execute()
    return len(result.get("values", [])) + 1


def _row_is_occupied(sheet_name: str, row: int, num_cols: int) -> bool:
    # A row can have item/price (or income/desc) filled in by hand before a date
    # is set — e.g. a manual draft entry. Column A alone would call this row
    # "empty" and the bot would silently overwrite it. Treat any non-blank cell
    # in columns B.. as "occupied" so writes always skip it.
    end_col = chr(ord("A") + num_cols - 1)
    result = _sheets.values().get(spreadsheetId=SPREADSHEET_ID, range=f"{sheet_name}!B{row}:{end_col}{row}").execute()
    values = result.get("values", [[]])
    row_values = values[0] if values else []
    return any(cell not in ("", None) for cell in row_values)


def _next_write_row(sheet_name: str, num_cols: int, max_tries: int = 200) -> int:
    row = _next_row(sheet_name)
    for _ in range(max_tries):
        if not _row_is_occupied(sheet_name, row, num_cols):
            return row
        row += 1
    raise RuntimeError(f"Could not find a safe empty row to write to in '{sheet_name}' near row {row}")


def _write_row(sheet_name: str, row_values: list) -> int:
    row_number = _next_write_row(sheet_name, len(row_values))
    if row_number > 2:
        _copy_row_format(sheet_name, row_number - 1, row_number, len(row_values))
    end_col = chr(ord("A") + len(row_values) - 1)
    _sheets.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{sheet_name}!A{row_number}:{end_col}{row_number}",
        valueInputOption="USER_ENTERED",
        body={"values": [row_values]},
    ).execute()
    _save_last_entry(sheet_name, row_number)
    return row_number


def append_expense(entry_date: date_type, item: str, price: float, category: str) -> int:
    return _write_row(OUT_SHEET_NAME, [entry_date.isoformat(), item, price, category])


def append_income(entry_date: date_type, amount: float, description: str) -> int:
    return _write_row(IN_SHEET_NAME, [entry_date.isoformat(), amount, description])


def update_category(row: int, category: str):
    _sheets.values().update(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{OUT_SHEET_NAME}!D{row}",
        valueInputOption="USER_ENTERED",
        body={"values": [[category]]},
    ).execute()


def _save_last_entry(sheet_name: str, row: int):
    with open(_STATE_FILE, "w") as f:
        json.dump({"sheet": sheet_name, "row": row}, f)


def pop_last_entry():
    if not os.path.exists(_STATE_FILE):
        return None
    with open(_STATE_FILE) as f:
        data = json.load(f)
    os.remove(_STATE_FILE)
    return data


def get_out_rows() -> list:
    """Raw (unformatted) date-serial/item/price/category rows from the 'out' tab, header excluded."""
    last_row = _next_row(OUT_SHEET_NAME) - 1
    if last_row < 2:
        return []
    result = (
        _sheets.values()
        .get(
            spreadsheetId=SPREADSHEET_ID,
            range=f"{OUT_SHEET_NAME}!A2:D{last_row}",
            valueRenderOption="UNFORMATTED_VALUE",
        )
        .execute()
    )
    return result.get("values", [])


def _is_red(color: dict) -> bool:
    if not color:
        return False
    r, g, b = color.get("red", 0), color.get("green", 0), color.get("blue", 0)
    return r >= 0.6 and g <= 0.3 and b <= 0.3


def get_row_details(sheet_name: str, num_cols: int) -> list:
    """Formatted values + whether text color is red, for every row from 2 to the sheet's grid bound."""
    row_count = _get_sheet_props(sheet_name)["gridProperties"]["rowCount"]
    if row_count < 2:
        return []
    end_col = chr(ord("A") + num_cols - 1)
    resp = _sheets.get(
        spreadsheetId=SPREADSHEET_ID,
        ranges=[f"{sheet_name}!A2:{end_col}{row_count}"],
        includeGridData=True,
        fields="sheets(data(rowData(values(formattedValue,userEnteredFormat.textFormat.foregroundColor))))",
    ).execute()

    row_data = resp["sheets"][0]["data"][0].get("rowData", [])
    details = []
    for i, row in enumerate(row_data, start=2):
        cells = row.get("values", [])
        values = [c.get("formattedValue", "") for c in cells]
        values += [""] * (num_cols - len(values))
        is_red = any(_is_red(c.get("userEnteredFormat", {}).get("textFormat", {}).get("foregroundColor")) for c in cells)
        details.append({"row": i, "values": values[:num_cols], "is_red": is_red})
    return details


def set_row_black(sheet_name: str, row: int):
    # Full sheet width, not just the tracked data columns (A:D) — "out" also has
    # sub category/month/week starting columns that should turn black too.
    sheet_id = _get_sheet_id(sheet_name)
    end_col = _get_sheet_props(sheet_name)["gridProperties"]["columnCount"]
    _sheets.batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "requests": [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row - 1,
                            "endRowIndex": row,
                            "startColumnIndex": 0,
                            "endColumnIndex": end_col,
                        },
                        "cell": {"userEnteredFormat": {"textFormat": {"foregroundColor": {"red": 0, "green": 0, "blue": 0}}}},
                        "fields": "userEnteredFormat.textFormat.foregroundColor",
                    }
                }
            ]
        },
    ).execute()


def clear_row(sheet_name: str, row: int):
    # Clearing (rather than deleting the row dimension) avoids shifting rows
    # in a spreadsheet where other tabs (pivots, net_calc, query_out/in) may
    # reference fixed row ranges.
    _sheets.values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range=f"{sheet_name}!A{row}:D{row}",
        body={},
    ).execute()
