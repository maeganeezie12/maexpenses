import asyncio
import logging
import sys
from datetime import time as time_type

import pytz
from telegram.ext import ApplicationBuilder, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from config import (
    ALLOWED_USER_ID,
    DAILY_CHECK_HOUR,
    DAILY_CHECK_MINUTE,
    EMAIL_POLL_INTERVAL_SECONDS,
    TIMEZONE,
    TOKEN,
    WEEKLY_SUMMARY_HOUR,
    WEEKLY_SUMMARY_MINUTE,
    YAHOO_APP_PASSWORD,
    YAHOO_EMAIL,
)
from handlers import (
    avgspend_callback,
    avgspend_command,
    budget_command,
    category_fix_callback,
    catspend_category_callback,
    catspend_command,
    catspend_period_callback,
    checksheet_command,
    daily_check_job,
    email_category_callback,
    email_poll_job,
    gsheet_command,
    history_command,
    history_granularity_callback,
    history_year_callback,
    income_command,
    last_category_callback,
    last_command,
    log_message,
    net_callback,
    net_command,
    redack_callback,
    salary_transfer_done_callback,
    setbudget_callback,
    setbudget_command,
    spending_callback,
    spending_command,
    start_command,
    summary_d_command,
    summary_m_command,
    summary_w_command,
    summary_y_command,
    undo_command,
    weekly_summary_job,
    whoami_command,
)

_SUNDAY = (0,)  # PTB v20+ JobQueue.run_daily: 0=Sunday .. 6=Saturday

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("expense_bot.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


async def post_init(application):
    if not ALLOWED_USER_ID:
        logger.warning(
            "ALLOWED_USER_ID is not set in .env — the bot will respond to ANYONE who messages it!\n"
            "  1. Message the bot with /whoami\n"
            "  2. Paste the ID into .env as ALLOWED_USER_ID=<id>\n"
            "  3. Restart the bot"
        )
    else:
        logger.info("Bot ready — restricted to user ID %s", ALLOWED_USER_ID)

    tz = pytz.timezone(TIMEZONE)
    check_time = time_type(hour=DAILY_CHECK_HOUR, minute=DAILY_CHECK_MINUTE, tzinfo=tz)
    application.job_queue.run_daily(daily_check_job, time=check_time, name="daily_sheet_check")
    logger.info("Daily sheet check scheduled for %02d:%02d %s", DAILY_CHECK_HOUR, DAILY_CHECK_MINUTE, TIMEZONE)

    weekly_time = time_type(hour=WEEKLY_SUMMARY_HOUR, minute=WEEKLY_SUMMARY_MINUTE, tzinfo=tz)
    application.job_queue.run_daily(weekly_summary_job, time=weekly_time, days=_SUNDAY, name="weekly_summary")
    logger.info(
        "Weekly summary scheduled for Sundays %02d:%02d %s", WEEKLY_SUMMARY_HOUR, WEEKLY_SUMMARY_MINUTE, TIMEZONE
    )

    if YAHOO_EMAIL and YAHOO_APP_PASSWORD:
        application.job_queue.run_repeating(email_poll_job, interval=EMAIL_POLL_INTERVAL_SECONDS, first=10, name="email_poll")
        logger.info("Email alert polling scheduled every %ds", EMAIL_POLL_INTERVAL_SECONDS)
    else:
        logger.warning(
            "YAHOO_EMAIL/YAHOO_APP_PASSWORD not set in .env — PayLah!/PayNow email alerts are disabled."
        )


def main():
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("undo", undo_command))
    app.add_handler(CommandHandler("income", income_command))
    app.add_handler(CommandHandler("day", summary_d_command))
    app.add_handler(CommandHandler("week", summary_w_command))
    app.add_handler(CommandHandler("month", summary_m_command))
    app.add_handler(CommandHandler("year", summary_y_command))
    app.add_handler(CommandHandler("checksheet", checksheet_command))
    app.add_handler(CommandHandler("spending", spending_command))
    app.add_handler(CommandHandler("avgspend", avgspend_command))
    app.add_handler(CommandHandler("catspend", catspend_command))
    app.add_handler(CommandHandler("setbudget", setbudget_command))
    app.add_handler(CommandHandler("budget", budget_command))
    app.add_handler(CommandHandler("history", history_command))
    app.add_handler(CommandHandler("net", net_command))
    app.add_handler(CommandHandler("gsheet", gsheet_command))
    app.add_handler(CommandHandler("last", last_command))

    app.add_handler(CallbackQueryHandler(category_fix_callback, pattern=r"^cat:"))
    app.add_handler(CallbackQueryHandler(redack_callback, pattern=r"^redack:"))
    app.add_handler(CallbackQueryHandler(spending_callback, pattern=r"^spend:"))
    app.add_handler(CallbackQueryHandler(avgspend_callback, pattern=r"^avgspend:"))
    app.add_handler(CallbackQueryHandler(catspend_category_callback, pattern=r"^catspendcat:"))
    app.add_handler(CallbackQueryHandler(catspend_period_callback, pattern=r"^catspendperiod:"))
    app.add_handler(CallbackQueryHandler(setbudget_callback, pattern=r"^setbudget:"))
    app.add_handler(CallbackQueryHandler(history_granularity_callback, pattern=r"^histgran:"))
    app.add_handler(CallbackQueryHandler(history_year_callback, pattern=r"^histyear:"))
    app.add_handler(CallbackQueryHandler(net_callback, pattern=r"^net:"))
    app.add_handler(CallbackQueryHandler(last_category_callback, pattern=r"^lastcat:"))
    app.add_handler(CallbackQueryHandler(salary_transfer_done_callback, pattern=r"^salarydone$"))
    app.add_handler(CallbackQueryHandler(email_category_callback, pattern=r"^emailcat:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_message))

    logger.info("Expense bot starting...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
