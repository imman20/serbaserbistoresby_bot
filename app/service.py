"""Logika order & pengiriman produk — dipakai bersama oleh bot dan webhook."""
from __future__ import annotations

import io
import logging
import secrets
from datetime import datetime

import qrcode
from telegram import LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.ext import Application

from . import db
from .config import cfg
from .pakasir import PakasirClient

log = logging.getLogger(__name__)


def gen_order_id() -> str:
    return datetime.now().strftime("%y%m%d") + secrets.token_hex(4).upper()


def rupiah(n: int) -> str:
    return "Rp" + f"{n:,}".replace(",", ".")


def qris_png(payload: str) -> io.BytesIO:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image(fill_color="black", back_color="white").save(buf, format="PNG")
    buf.seek(0)
    buf.name = "qris.png"
    return buf


async def start_order(
    pakasir: PakasirClient, user_id: int, username: str, product_id: int, qty: int = 1,
) -> tuple[db.aiosqlite.Row, str | None]:
    """Buat order + QRIS. Return (order_row, error_message)."""
    product = await db.get_product(product_id)
    if product is None or not product["active"]:
        return None, "Produk tidak tersedia."

    needs_stock = product["delivery_type"] in ("account", "voucher") or (
        product["delivery_type"] == "file" and not product["file_payload"]
    )
    if needs_stock and await db.available_stock(product_id) < qty:
        return None, "Stok habis. Coba lagi nanti atau hubungi admin."

    amount = int(product["price"]) * qty
    order_id = gen_order_id()
    expired_at = db.now() + cfg.order_expiry_minutes * 60

    reserved: list[str] = []
    if needs_stock:
        reserved = await db.reserve_stock(product_id, qty, order_id)
        if not reserved:
            return None, "Stok habis saat diproses. Coba lagi."

    await db.create_order(order_id, user_id, username, product_id, qty, amount, expired_at)

    try:
        pay = await pakasir.create_qris(order_id, amount)
    except Exception as e:  # noqa: BLE001
        log.exception("create_qris gagal untuk %s", order_id)
        await db.fail_order(order_id)  # tandai failed + lepas stok reserved
        return None, f"Gagal membuat QRIS: {e}"

    await db.set_order_payment(order_id, pay.payment_number, pay.fee, pay.total_payment)
    return await db.get_order(order_id), None


async def deliver_order(app: Application, order_id: str) -> bool:
    """Kirim produk ke pembeli. Idempoten & aman dipanggil berkali-kali."""
    order = await db.get_order(order_id)
    if order is None:
        log.warning("deliver_order: order %s tidak ada", order_id)
        return False
    if order["status"] == "delivered":
        return True
    if order["status"] != "paid":
        log.warning("deliver_order: order %s status=%s (bukan paid)", order_id, order["status"])
        return False

    product = await db.get_product(order["product_id"])
    conn = await db.connect()
    async with conn.execute(
        "SELECT payload FROM stock_items WHERE order_id=? AND status='reserved'", (order_id,)
    ) as cur:
        rows = await cur.fetchall()
    payloads = [r["payload"] for r in rows]
    if not payloads and product["delivery_type"] == "file" and product["file_payload"]:
        payloads = [product["file_payload"]]

    body = "\n".join(f"<code>{_esc(p)}</code>" for p in payloads) or "(hubungi admin — data kosong)"
    text = (
        f"✅ <b>Pembayaran diterima</b>\n"
        f"Order <code>{order_id}</code> · {_esc(product['name'])}\n\n"
        f"Berikut produk kamu:\n{body}\n\n"
        f"Terima kasih! Simpan pesan ini."
    )
    await app.bot.send_message(
        chat_id=order["user_id"], text=text, parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )
    await db.mark_order_delivered(order_id)

    for admin_id in cfg.admin_ids:
        try:
            await app.bot.send_message(
                chat_id=admin_id,
                text=(
                    f"💰 Order terbayar & terkirim\n"
                    f"{order_id} · {product['name']} · {rupiah(order['amount'])}\n"
                    f"user: {order['user_id']} @{order['username']}"
                ),
            )
        except Exception:  # noqa: BLE001
            pass
    return True


async def settle_if_paid(app: Application, pakasir: PakasirClient, order_id: str) -> str:
    """Verifikasi ke Pakasir; jika lunas -> tandai paid + kirim produk.

    Return: 'delivered' | 'pending' | 'expired' | 'not_found' | 'mismatch'
    """
    order = await db.get_order(order_id)
    if order is None:
        return "not_found"
    if order["status"] in ("delivered",):
        return "delivered"
    if order["status"] == "expired":
        return "expired"

    try:
        st = await pakasir.transaction_detail(order_id, int(order["amount"]))
    except Exception as e:  # noqa: BLE001
        log.warning("verifikasi %s gagal: %s", order_id, e)
        return "pending"

    if int(st.amount) != int(order["amount"]):
        log.error("AMOUNT MISMATCH order=%s db=%s pakasir=%s", order_id, order["amount"], st.amount)
        return "mismatch"
    if not st.is_completed:
        return "pending"

    if await db.mark_order_paid(order_id) or order["status"] == "paid":
        await deliver_order(app, order_id)
    return "delivered"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
