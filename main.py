"""Entry point: menjalankan bot Telegram (polling) + server webhook Pakasir bersamaan."""
from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from app import db
from app.bot import build_application
from app.config import cfg
from app.webhook import build_web_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
log = logging.getLogger("main")


async def run() -> None:
    cfg.validate()
    await db.connect()

    tg_app = build_application()
    pakasir = tg_app.bot_data["pakasir"]

    await tg_app.initialize()
    await tg_app.start()
    await tg_app.updater.start_polling(drop_pending_updates=True)
    log.info("Bot Telegram jalan (polling).")

    web_app = build_web_app(tg_app, pakasir)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, cfg.webhook_host, cfg.webhook_port)
    await site.start()
    log.info("Webhook server: http://%s:%s%s", cfg.webhook_host, cfg.webhook_port, cfg.webhook_path)

    stop = asyncio.Event()
    try:
        await stop.wait()
    finally:
        log.info("Shutdown...")
        await runner.cleanup()
        await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        await pakasir.aclose()
        await db.close()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
