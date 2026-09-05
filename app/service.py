"""Logika order & pengiriman produk — dipakai bersama oleh bot dan webhook."""
from __future__ import annotations

import io
import logging
import secrets
from datetime import datetime, timedelta, timezone

import qrcode
from telegram import LinkPreviewOptions
from telegram.constants import ParseMode
from telegram.ext import Application

from . import db
from .config import cfg
from .pakasir import PakasirClient

log = logging.getLogger(__name__)

_WIB = timezone(timedelta(hours=7))
_HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
_BULAN = [
    "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]


def gen_order_id() -> str:
    return datetime.now().strftime("%y%m%d") + secrets.token_hex(4).upper()


def rupiah(n: int) -> str:
    return "Rp" + f"{n:,}".replace(",", ".")


def format_tanggal_wib(epoch: int) -> str:
    """Format epoch UTC jadi 'Jumat, 04 September 2026 — 15:19 WIB'."""
    dt = datetime.fromtimestamp(epoch, _WIB)
    return f"{_HARI[dt.weekday()]}, {dt.day:02d} {_BULAN[dt.month]} {dt.year} — {dt.strftime('%H:%M')} WIB"


def buyer_label(user_id: int, username: str) -> str:
    return f"@{username}" if username else f"ID {user_id}"


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

    # Tandai terkirim SEBELUM kirim pesan, supaya stok sudah 'sold' saat kita
    # hitung sisa stok untuk notifikasi channel di bawah.
    await db.mark_order_delivered(order_id)

    tanggal = format_tanggal_wib(order["paid_at"] or db.now())
    detail_lines = "\n\n".join(f"<pre>{_esc(p)}</pre>" for p in payloads) or "(hubungi admin — data kosong)"
    usage_block = (
        f"━━━━━━━━━━━━━━━━\n📖 Cara pakai:\n{_esc(product['usage_note'])}\n"
        if product["usage_note"] else ""
    )

    buyer_text = (
        f"📦 <b>PRODUK KAMU SUDAH SIAP!</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🧾 Order: <code>{order_id}</code>\n"
        f"📦 Produk: {_esc(product['name'])}\n"
        f"💰 Harga: {rupiah(order['amount'])}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"🔑 Detail:\n{detail_lines}\n"
        f"{usage_block}"
        f"━━━━━━━━━━━━━━━━\n"
        f"📅 {tanggal}\n\n"
        f"Terima kasih! Simpan pesan ini baik-baik 🙏"
    )
    await app.bot.send_message(
        chat_id=order["user_id"], text=buyer_text, parse_mode=ParseMode.HTML,
        link_preview_options=LinkPreviewOptions(is_disabled=True),
    )

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

    await _notify_sales_channel(app, order, product, tanggal)
    return True


async def _notify_sales_channel(app: Application, order, product, tanggal: str) -> None:
    """Kirim notifikasi penjualan ke channel (kalau SALES_CHANNEL_ID diisi)."""
    if not cfg.sales_channel_id:
        return
    stok_line = ""
    needs_stock = product["delivery_type"] in ("account", "voucher")
    if needs_stock:
        available, total = await db.stock_counts(product["id"])
        stok_line = f"📦 Sisa Stok: {available} dari {total} pcs\n"

    text = (
        f"🎉 <b>Penjualan Baru</b> (QRIS Pakasir)\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👤 Pembeli: {_esc(buyer_label(order['user_id'], order['username']))} "
        f"(<code>{order['user_id']}</code>)\n"
        f"📦 Produk: {_esc(product['name'])}\n"
        f"🔢 Jumlah: {order['qty']}x\n"
        f"💰 Total: {rupiah(order['amount'])}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📅 {tanggal}\n"
        f"{stok_line}"
        f"🆔 Order ID: <code>{order['order_id']}</code>"
    )
    try:
        await app.bot.send_message(chat_id=cfg.sales_channel_id, text=text, parse_mode=ParseMode.HTML)
    except Exception:  # noqa: BLE001
        log.exception("Gagal kirim notifikasi ke SALES_CHANNEL_ID=%s", cfg.sales_channel_id)


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
