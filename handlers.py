import logging
import re
from datetime import timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, Update
from telegram.ext import ContextTypes

import sheets
from analytics import (
    build_category_avg_table,
    build_summary,
    has_investment_this_month,
    render_category_avg_comparison_chart,
    render_history_chart,
    render_net_income_chart,
    render_spending_by_month,
)
from budget import build_budget_table, set_budget
from config import ALLOWED_USER_ID, CATEGORIES, OUT_SHEET_NAME
from integrity import run_daily_check
from llm import parse_expense, parse_income
from utils import today_local

logger = logging.getLogger(__name__)


def _authorized(update: Update) -> bool:
    if not ALLOWED_USER_ID:
        return True
    return update.effective_user and update.effective_user.id == ALLOWED_USER_ID


_CATEGORY_BUTTON_ORDER = ["FNB"] + [c for c in CATEGORIES if c != "FNB"]


def _category_keyboard(row: int):
    buttons = [InlineKeyboardButton(cat, callback_data=f"cat:{row}:{cat}") for cat in _CATEGORY_BUTTON_ORDER]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(rows)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Send me an expense like `3.9 lunch` and I'll log it to the *out* sheet.\n"
        "Send /income 1221 salary to log income to the *in* sheet.\n\n"
        "Commands:\n"
        "/income — log income, e.g. /income 1221 salary\n"
        "/undo — remove the last entry you logged\n"
        "/day — vs yesterday, by category, + last 7 days trend by category\n"
        "/week — week to date vs same days last week, by category, + last 4 weeks trend by category\n"
        "/month — month to date vs same days last month, by category, + last 3 months trend by category\n"
        "/year — year-to-date vs same period last year, by category\n"
        "/spending — stacked bar chart of every category by month (this/last/next year or all time)\n"
        "/avgspend — table of total/transactions/average per category (week/month/year), + chart vs all-time average\n"
        "/setbudget — set this month's budget from a save %, e.g. /setbudget 20; "
        "salary is your last recorded salary income in the *in* sheet, and category budgets follow last month's spend split\n"
        "/budget — this month's budget used vs remaining, by category\n"
        "/history — total spending by day/week/month (this/last/next year or all time)\n"
        "/net — net income by month, income minus expenses (this/last/next year or all time)\n"
        "/checksheet — check for missing dates, missing categories, or new automated rows\n"
        "/gsheet — get the link to the out sheet\n"
        "/whoami — show your Telegram user ID\n"
        "/help — show this message\n\n"
        "Every Monday you'll also get an automatic recap of the week just finished, "
        "and on the 25th a reminder if no Investment-category expense has been logged that month.",
        parse_mode="Markdown",
    )


async def whoami_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Your Telegram user ID: {update.effective_user.id}")


async def undo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    last = sheets.pop_last_entry()
    if not last:
        await update.message.reply_text("Nothing to undo.")
        return
    sheets.clear_row(last["sheet"], last["row"])
    await update.message.reply_text(f"Removed the last entry from the *{last['sheet']}* sheet.", parse_mode="Markdown")


async def gsheet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(sheets.get_sheet_url(OUT_SHEET_NAME))


async def income_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Usage: /income 1221 salary")
        return

    today = today_local()
    parsed = await parse_income(text, today)
    entry_date = parsed["date"] or today
    sheets.append_income(entry_date, parsed["amount"], parsed["description"])
    await update.message.reply_text(
        f"Logged income: {entry_date.strftime('%d %b %Y')} | ${parsed['amount']:.2f} | {parsed['description']}"
    )


async def setbudget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    usage = "Usage: /setbudget <save_percent>\ne.g. /setbudget 20"
    if len(context.args) < 1:
        await update.message.reply_text(usage)
        return

    try:
        save_pct = float(context.args[0])
    except ValueError:
        await update.message.reply_text(f"Save percent must be a number.\n{usage}")
        return

    if not (0 <= save_pct <= 100):
        await update.message.reply_text("Save percent must be between 0 and 100.")
        return

    today = today_local()
    state = set_budget(save_pct, today)
    if state is None:
        await update.message.reply_text(
            "No salary income found in the *in* sheet yet. Log it first, e.g. /income 3200 salary",
            parse_mode="Markdown",
        )
        return

    await update.message.reply_text(build_budget_table(today), parse_mode="Markdown")


async def budget_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(build_budget_table(today_local()), parse_mode="Markdown")


async def log_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    text = update.message.text.strip()
    today = today_local()

    parsed = await parse_expense(text, today)
    entry_date = parsed["date"] or today
    row = sheets.append_expense(entry_date, parsed["item"], parsed["price"], parsed["category"])

    summary_line = (
        f"Logged expense: {entry_date.strftime('%d %b %Y')} | {parsed['item']} | "
        f"${parsed['price']:.2f} | {parsed['category']}"
    )
    if parsed.get("category_explicit"):
        await update.message.reply_text(summary_line)
    else:
        await update.message.reply_text(
            f"{summary_line}\nWrong category?", reply_markup=_category_keyboard(row)
        )


async def _send_summary(send_text, send_photo, send_media_group, pie_chart, trend_chart, caption):
    photos = [p for p in (pie_chart, trend_chart) if p]
    if len(photos) > 1:
        media = [InputMediaPhoto(photos[0], caption=caption)] + [InputMediaPhoto(p) for p in photos[1:]]
        await send_media_group(media=media)
    elif photos:
        await send_photo(photo=photos[0], caption=caption)
    else:
        await send_text(caption)


async def _summary(update: Update, kind: str):
    if not _authorized(update):
        return
    today = today_local()
    pie_chart, trend_chart, insights = build_summary(kind, today)
    await _send_summary(
        update.message.reply_text, update.message.reply_photo, update.message.reply_media_group,
        pie_chart, trend_chart, insights,
    )


async def weekly_summary_job(context: ContextTypes.DEFAULT_TYPE):
    if not ALLOWED_USER_ID:
        return

    today = today_local()
    last_sunday = today - timedelta(days=(today.weekday() + 1) % 7)  # most recent Sunday on/before today
    pie_chart, trend_chart, insights = build_summary("w", last_sunday)

    async def send_text(text):
        await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=text)

    async def send_photo(photo, caption=None):
        await context.bot.send_photo(chat_id=ALLOWED_USER_ID, photo=photo, caption=caption)

    async def send_media_group(media):
        await context.bot.send_media_group(chat_id=ALLOWED_USER_ID, media=media)

    await _send_summary(send_text, send_photo, send_media_group, pie_chart, trend_chart, f"🗓️ Weekly recap\n\n{insights}")


async def summary_d_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _summary(update, "d")


async def summary_w_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _summary(update, "w")


async def summary_m_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _summary(update, "m")


async def summary_y_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _summary(update, "y")


def _year_option_keyboard(prefix: str, today, include_all: bool = True):
    """This Year / Last Year / Next Year / (All Time) — the standard order for
    every timeline-range picker in the bot."""
    buttons = [
        [InlineKeyboardButton(f"This Year ({today.year})", callback_data=f"{prefix}:this")],
        [InlineKeyboardButton(f"Last Year ({today.year - 1})", callback_data=f"{prefix}:last")],
        [InlineKeyboardButton(f"Next Year ({today.year + 1})", callback_data=f"{prefix}:next")],
    ]
    if include_all:
        buttons.append([InlineKeyboardButton("All Time", callback_data=f"{prefix}:all")])
    return InlineKeyboardMarkup(buttons)


async def spending_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        "Spending by category, by month — pick a range:", reply_markup=_year_option_keyboard("spend", today_local())
    )


async def spending_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _authorized(update):
        await query.answer()
        return

    await query.answer()
    _, option = query.data.split(":", 1)
    chart = render_spending_by_month(option, today_local())
    if chart:
        await query.message.reply_photo(photo=chart)
    else:
        await query.message.reply_text("No expense data for that range.")


def _avg_period_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Week", callback_data="avgspend:w"),
                InlineKeyboardButton("Month", callback_data="avgspend:m"),
                InlineKeyboardButton("Year", callback_data="avgspend:y"),
            ]
        ]
    )


async def avgspend_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        "Average spend by category — pick a period:", reply_markup=_avg_period_keyboard()
    )


async def avgspend_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _authorized(update):
        await query.answer()
        return

    await query.answer()
    _, kind = query.data.split(":", 1)
    today = today_local()
    table = build_category_avg_table(kind, today)
    await query.message.reply_text(table, parse_mode="Markdown")

    chart = render_category_avg_comparison_chart(kind, today)
    if chart:
        await query.message.reply_photo(photo=chart)


def _history_granularity_keyboard():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Day", callback_data="histgran:day"),
                InlineKeyboardButton("Week", callback_data="histgran:week"),
                InlineKeyboardButton("Month", callback_data="histgran:month"),
            ]
        ]
    )


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text("Total spending history — choose a granularity:", reply_markup=_history_granularity_keyboard())


async def history_granularity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _authorized(update):
        await query.answer()
        return

    await query.answer()
    _, granularity = query.data.split(":", 1)
    await query.edit_message_text(
        f"Granularity: {granularity}. Now pick a range:",
        reply_markup=_year_option_keyboard(f"histyear:{granularity}", today_local()),
    )


async def history_year_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _authorized(update):
        await query.answer()
        return

    await query.answer()
    _, granularity, option = query.data.split(":", 2)
    chart = render_history_chart(granularity, option, today_local())
    if chart:
        await query.message.reply_photo(photo=chart)
    else:
        await query.message.reply_text("No expense data for that range.")


async def net_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    await update.message.reply_text(
        "Net income by month (income minus expenses) — pick a range:",
        reply_markup=_year_option_keyboard("net", today_local()),
    )


async def net_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _authorized(update):
        await query.answer()
        return

    await query.answer()
    _, option = query.data.split(":", 1)
    chart = render_net_income_chart(option, today_local())
    if chart:
        await query.message.reply_photo(photo=chart)
    else:
        await query.message.reply_text("No income/expense data for that range.")


def _describe_row(values: list) -> str:
    return " | ".join(v for v in values if v) or "(no data)"


def _red_row_keyboard(sheet: str, row: int):
    return InlineKeyboardMarkup([[InlineKeyboardButton("Acknowledge", callback_data=f"redack:{sheet}:{row}")]])


async def _report_check_results(send_text):
    today = today_local()
    date_str = today.strftime("%d %b %Y")
    gap_rows, missing_category_rows, new_red_rows = run_daily_check()

    if gap_rows:
        lines = [f"Rows with a missing date ({date_str}) — fill these in before the bot reaches them:"]
        for g in gap_rows:
            lines.append(f"- {g['sheet']} row {g['row']}: {_describe_row(g['values'])}")
        await send_text("\n".join(lines))

    if missing_category_rows:
        lines = [f"Rows with a missing category ({date_str}):"]
        for m in missing_category_rows:
            lines.append(f"- {m['sheet']} row {m['row']}: {_describe_row(m['values'])}")
        await send_text("\n".join(lines))

    for r in new_red_rows:
        await send_text(
            f"New automated row detected ({date_str}) — {r['sheet']} row {r['row']}: {_describe_row(r['values'])}",
            reply_markup=_red_row_keyboard(r["sheet"], r["row"]),
        )

    investment_reminder_sent = False
    if today.day == 25 and not has_investment_this_month(today):
        await send_text(
            f"💰 {date_str}: no Investment-category expense has been logged this month — "
            "remember to invest to hit the multiplier!"
        )
        investment_reminder_sent = True

    return gap_rows, missing_category_rows, new_red_rows, investment_reminder_sent


async def checksheet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return

    async def send_text(text, reply_markup=None):
        await update.message.reply_text(text, reply_markup=reply_markup)

    gap_rows, missing_category_rows, new_red_rows, investment_reminder_sent = await _report_check_results(send_text)
    if not gap_rows and not missing_category_rows and not new_red_rows and not investment_reminder_sent:
        date_str = today_local().strftime("%d %b %Y")
        await update.message.reply_text(f"All clear ({date_str}) — no missing dates, missing categories, or new automated rows.")


async def daily_check_job(context: ContextTypes.DEFAULT_TYPE):
    if not ALLOWED_USER_ID:
        return

    async def send_text(text, reply_markup=None):
        await context.bot.send_message(chat_id=ALLOWED_USER_ID, text=text, reply_markup=reply_markup)

    await _report_check_results(send_text)


async def redack_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _authorized(update):
        await query.answer()
        return

    _, sheet, row_str = query.data.split(":", 2)
    row = int(row_str)
    sheets.set_row_black(sheet, row)
    await query.answer("Marked as reviewed")
    await query.edit_message_text(f"{query.message.text}\n\n✅ Acknowledged", reply_markup=None)


async def category_fix_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not _authorized(update):
        await query.answer()
        return

    _, row_str, category = query.data.split(":", 2)
    sheets.update_category(int(row_str), category)
    await query.answer(f"Category set to {category}")

    lines = query.message.text.split("\n")
    lines[0] = re.sub(r"\|\s*[^|]+$", f"| {category}", lines[0])
    await query.edit_message_text(lines[0], reply_markup=None)
