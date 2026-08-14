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
    TIMEZONE,
    TOKEN,
    WEEKLY_SUMMARY_HOUR,
    WEEKLY_SUMMARY_MINUTE,
)
from handlers import (
    category_fix_callback,
    checksheet_command,
    daily_check_job,
    gsheet_command,
    income_command,
    log_message,
    redack_callback,
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

_MONDAY = (1,)  # PTB v20+ JobQueue.run_daily: 0=Sunday .. 6=Saturday

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
    application.job_queue.run_daily(weekly_summary_job, time=weekly_time, days=_MONDAY, name="weekly_summary")
    logger.info(
        "Weekly summary scheduled for Mondays %02d:%02d %s", WEEKLY_SUMMARY_HOUR, WEEKLY_SUMMARY_MINUTE, TIMEZONE
    )


def main():
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", start_command))
    app.add_handler(CommandHandler("whoami", whoami_command))
    app.add_handler(CommandHandler("undo", undo_command))
    app.add_handler(CommandHandler("income", income_command))
    app.add_handler(CommandHandler("summary_d", summary_d_command))
    app.add_handler(CommandHandler("summary_w", summary_w_command))
    app.add_handler(CommandHandler("summary_m", summary_m_command))
    app.add_handler(CommandHandler("summary_y", summary_y_command))
    app.add_handler(CommandHandler("checksheet", checksheet_command))
    app.add_handler(CommandHandler("spending", spending_command))
    app.add_handler(CommandHandler("gsheet", gsheet_command))

    app.add_handler(CallbackQueryHandler(category_fix_callback, pattern=r"^cat:"))
    app.add_handler(CallbackQueryHandler(redack_callback, pattern=r"^redack:"))
    app.add_handler(CallbackQueryHandler(spending_callback, pattern=r"^spend:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_message))

    logger.info("Expense bot starting...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
