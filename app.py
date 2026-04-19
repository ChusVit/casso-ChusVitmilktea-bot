"""Entry point gộp cho Railway: chạy cả Telegram bot (polling) và PayOS webhook (FastAPI) trong 1 process."""
import asyncio
import logging
import os
import uvicorn

from main import bot, dp
from database.db_handler import init_db
from webhook_link import app as webhook_app


async def run_bot():
    logging.info("Bot Chủ Quán đang khởi động polling...")
    await dp.start_polling(bot)


async def run_webhook():
    port = int(os.environ.get("PORT", 8000))
    config = uvicorn.Config(webhook_app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    logging.info(f"Webhook PayOS đang listen trên port {port}...")
    await server.serve()


async def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    await asyncio.gather(run_bot(), run_webhook())


if __name__ == "__main__":
    asyncio.run(main())
