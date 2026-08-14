import os

from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID") or "0")

SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "10ydswzR7dzg6DQ5YvhHWaJ5Fa0xGlToFX21oWVkD3Mo")
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE", "service_account.json")
OUT_SHEET_NAME = os.getenv("OUT_SHEET_NAME", "out")
IN_SHEET_NAME = os.getenv("IN_SHEET_NAME", "in")

TIMEZONE = "Asia/Singapore"
DAILY_CHECK_HOUR = int(os.getenv("DAILY_CHECK_HOUR") or "9")
DAILY_CHECK_MINUTE = int(os.getenv("DAILY_CHECK_MINUTE") or "0")
WEEKLY_SUMMARY_HOUR = int(os.getenv("WEEKLY_SUMMARY_HOUR") or "9")
WEEKLY_SUMMARY_MINUTE = int(os.getenv("WEEKLY_SUMMARY_MINUTE") or "5")

CATEGORIES = [
    "Travel",
    "Allowance",
    "Shopping",
    "FNB",
    "Investment",
    "Hobbies",
    "Gifts",
    "Misc",
    "Alcohol",
    "Transport",
    "Entertainment",
    "Subscription",
]
