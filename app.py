import asyncio
import logging
import os
import uvicorn

# DEBUG: In env keys liên quan để verify Railway inject đúng
import openai as _openai_check
print(f"=== OPENAI SDK VERSION: {_openai_check.__version__} ===", flush=True)
print("=== RAILWAY CONTEXT ===", flush=True)
for key in ["RAILWAY_ENVIRONMENT_NAME", "RAILWAY_SERVICE_NAME", "RAILWAY_PROJECT_NAME",
            "RAILWAY_BETA_ENABLE_RUNTIME_V2", "RAILWAY_DEPLOYMENT_ID"]:
    print(f"  {key}: {os.environ.get(key)!r}", flush=True)
print("=== ENV CHECK ===", flush=True)
for key in ["PAYOS_CLIENT_ID", "PAYOS_API_KEY", "PAYOS_CHECKSUM_KEY",
            "TELEGRAM_BOT_TOKEN", "OPENAI_API_KEY", "PAYOS_BANK_BIN", "PAYOS_BANK_ACC"]:
    val = os.environ.get(key)
    status = f"SET (len={len(val)})" if val else f"MISSING ({val!r})"
    print(f"  {key}: {status}", flush=True)
print("=================", flush=True)

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
