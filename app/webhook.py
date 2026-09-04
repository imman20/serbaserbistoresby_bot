"""Server aiohttp untuk menerima callback pembayaran dari Pakasir.

Daftarkan URL ini di dashboard Pakasir (Project > Webhook):
    {PUBLIC_BASE_URL}{PAKASIR_WEBHOOK_PATH}

Karena Pakasir tidak menandatangani webhook, setiap notifikasi diverifikasi
ulang ke endpoint transactiondetail sebelum produk dikirim (lihat service.settle_if_paid).
"""
from __future__ import annotations

import logging

from aiohttp import web
from telegram.ext import Application

from .config import cfg
from .pakasir import PakasirClient
from .service import settle_if_paid

log = logging.getLogger(__name__)


async def _health(_: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def _webhook(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        log.warning("webhook: body bukan JSON")
        return web.json_response({"ok": False}, status=400)

    order_id = str(data.get("order_id", "")).strip()
    project = str(data.get("project", ""))
    log.info("webhook masuk: order_id=%s project=%s status=%s", order_id, project, data.get("status"))

    # ACK cepat untuk payload yang jelas bukan milik kita — jangan bikin Pakasir retry selamanya.
    if not order_id or (project and project != cfg.pakasir_project):
        return web.json_response({"ok": True, "ignored": True})

    app: Application = request.app["tg_app"]
    pakasir: PakasirClient = request.app["pakasir"]
    result = await settle_if_paid(app, pakasir, order_id)
    log.info("webhook %s -> %s", order_id, result)

    # 200 selama sudah kita proses; 202 kalau masih pending supaya Pakasir retry.
    status = 200 if result in ("delivered", "expired", "not_found", "mismatch") else 202
    return web.json_response({"ok": True, "result": result}, status=status)


def build_web_app(tg_app: Application, pakasir: PakasirClient) -> web.Application:
    web_app = web.Application()
    web_app["tg_app"] = tg_app
    web_app["pakasir"] = pakasir
    web_app.router.add_get("/health", _health)
    web_app.router.add_post(cfg.webhook_path, _webhook)
    return web_app
