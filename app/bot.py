"""Handler Telegram (python-telegram-bot v21, mode polling)."""
from __future__ import annotations

import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

from . import admin, db
from .config import cfg
from .pakasir import PakasirClient
from .service import deliver_order, qris_png, rupiah, settle_if_paid, start_order

log = logging.getLogger(__name__)


def _pakasir(context: ContextTypes.DEFAULT_TYPE) -> PakasirClient:
    return context.application.bot_data["pakasir"]


def esc(s: str) -> str:
    """Escape teks buatan pengguna sebelum ditaruh di pesan parse_mode=HTML."""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _is_admin(update: Update) -> bool:
    return update.effective_user and update.effective_user.id in cfg.admin_ids


# ── perintah user ────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    u = update.effective_user
    await db.upsert_user(u.id, u.username or "")
    text = (
        f"Halo {u.first_name}! 👋\n\n"
        "Selamat datang di toko produk digital.\n"
        "Ketik /produk untuk melihat katalog, atau /order [ID] untuk cek status order."
    )
    if u.id in cfg.admin_ids:
        text += (
            "\n\nAdmin:\n"
            "/tambahproduk — wizard tambah produk\n"
            "/addstok [kode] — tempel stok (1 baris = 1 item)\n"
            "/produkadmin — daftar & kelola produk\n"
            "/stok · /orders\n"
            "/aktif [kode] · /nonaktif [kode] · /hapusproduk [kode]"
        )
    await update.message.reply_text(text)


async def cmd_produk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    products = await db.list_products(only_active=True)
    if not products:
        await update.message.reply_text("Belum ada produk. Cek lagi nanti ya.")
        return
    rows = []
    for p in products:
        stock = await db.available_stock(p["id"])
        label = f"{p['name']} — {rupiah(p['price'])}"
        if p["delivery_type"] in ("account", "voucher"):
            label += f" ({stock} stok)" if stock else " (habis)"
        rows.append([InlineKeyboardButton(label, callback_data=f"view:{p['id']}")])
    await update.message.reply_text(
        "🛒 <b>Katalog Produk</b>\nPilih produk:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )


async def cb_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split(":")[1])
    p = await db.get_product(pid)
    if p is None or not p["active"]:
        await q.edit_message_text("Produk tidak tersedia.")
        return
    stock = await db.available_stock(pid)
    sold_out = p["delivery_type"] in ("account", "voucher") and stock <= 0
    desc = f"\n\n{esc(p['description'])}" if p["description"] else ""
    text = (
        f"<b>{esc(p['name'])}</b>\n"
        f"Harga: {rupiah(p['price'])}\n"
        f"{'Stok: ' + str(stock) if p['delivery_type'] != 'file' else 'Stok: tersedia'}"
        f"{desc}"
    )
    kb = [[InlineKeyboardButton("⬅️ Kembali", callback_data="back:list")]]
    if not sold_out:
        kb.insert(0, [InlineKeyboardButton(f"💳 Beli — {rupiah(p['price'])}", callback_data=f"buy:{pid}")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.HTML)


async def cb_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    products = await db.list_products(only_active=True)
    rows = []
    for p in products:
        stock = await db.available_stock(p["id"])
        label = f"{p['name']} — {rupiah(p['price'])}"
        if p["delivery_type"] in ("account", "voucher"):
            label += f" ({stock} stok)" if stock else " (habis)"
        rows.append([InlineKeyboardButton(label, callback_data=f"view:{p['id']}")])
    await q.edit_message_text(
        "🛒 <b>Katalog Produk</b>\nPilih produk:",
        reply_markup=InlineKeyboardMarkup(rows),
        parse_mode=ParseMode.HTML,
    )


async def cb_buy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split(":")[1])
    u = update.effective_user
    await q.edit_message_text("⏳ Membuat QRIS...")

    order, err = await start_order(_pakasir(context), u.id, u.username or "", pid, qty=1)
    if err:
        await q.edit_message_text(f"❌ {err}")
        return

    product = await db.get_product(pid)
    caption = (
        f"🧾 <b>Order {order['order_id']}</b>\n"
        f"{esc(product['name'])}\n"
        f"Nominal: {rupiah(order['amount'])}\n"
        f"Biaya admin: {rupiah(order['fee'])}\n"
        f"<b>Total bayar: {rupiah(order['total_payment'])}</b>\n\n"
        f"Scan QRIS di bawah (berlaku {cfg.order_expiry_minutes} menit).\n"
        f"Produk otomatis dikirim setelah pembayaran terverifikasi."
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Buka halaman bayar", url=cfg.hosted_pay_url(order["order_id"], order["amount"]))],
        [InlineKeyboardButton("🔄 Cek status pembayaran", callback_data=f"check:{order['order_id']}")],
    ])

    if order["payment_number"]:
        await context.bot.send_photo(
            chat_id=u.id, photo=qris_png(order["payment_number"]),
            caption=caption, parse_mode=ParseMode.HTML, reply_markup=kb,
        )
    else:
        await context.bot.send_message(
            chat_id=u.id, text=caption + "\n\nQR tidak tersedia — gunakan tombol halaman bayar.",
            parse_mode=ParseMode.HTML, reply_markup=kb,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    try:
        await q.delete_message()
    except Exception:  # noqa: BLE001
        pass


async def cb_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    order_id = q.data.split(":")[1]
    res = await settle_if_paid(context.application, _pakasir(context), order_id)
    msg = {
        "delivered": "✅ Pembayaran terverifikasi — produk sudah dikirim ke chat ini.",
        "pending": "⏳ Belum ada pembayaran masuk. Coba lagi beberapa saat setelah bayar.",
        "expired": "⌛ Order sudah kedaluwarsa. Silakan buat order baru.",
        "not_found": "Order tidak ditemukan.",
        "mismatch": "⚠️ Nominal tidak cocok. Hubungi admin.",
    }[res]
    await q.answer(msg, show_alert=True)


async def cmd_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/order <order_id> — cek status order milik sendiri."""
    args = context.args
    if not args:
        await update.message.reply_text("Format: /order <ID_ORDER>")
        return
    order = await db.get_order(args[0].strip())
    if order is None or order["user_id"] != update.effective_user.id:
        await update.message.reply_text("Order tidak ditemukan.")
        return
    if order["status"] in ("pending", "paid"):
        res = await settle_if_paid(context.application, _pakasir(context), order["order_id"])
        await update.message.reply_text(f"Status: {res}")
    else:
        await update.message.reply_text(f"Status order {order['order_id']}: {order['status']}")


# ── perintah admin ───────────────────────────────────────
async def cmd_stok(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    products = await db.list_products(only_active=False)
    if not products:
        await update.message.reply_text("Belum ada produk.")
        return
    out = ["<b>Stok</b>"]
    for p in products:
        s = await db.available_stock(p["id"])
        flag = "" if p["active"] else " [nonaktif]"
        out.append(f"• {esc(p['code'])} — {esc(p['name'])}: {s}{flag}")
    await update.message.reply_text("\n".join(out), parse_mode=ParseMode.HTML)


async def cmd_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    rows = await db.recent_orders(15)
    if not rows:
        await update.message.reply_text("Belum ada order.")
        return
    out = ["<b>15 order terakhir</b>"]
    for o in rows:
        out.append(
            f"<code>{o['order_id']}</code> {o['status']} · {esc(o['product_name'])} · "
            f"{rupiah(o['amount'])} · @{esc(o['username'])}"
        )
    await update.message.reply_text("\n".join(out), parse_mode=ParseMode.HTML)


# ── jobs ─────────────────────────────────────────────────
async def job_expire(context: ContextTypes.DEFAULT_TYPE) -> None:
    stale = await db.expire_stale_orders()
    for o in stale:
        try:
            await context.bot.send_message(
                chat_id=o["user_id"],
                text=f"⌛ Order {o['order_id']} kedaluwarsa (belum dibayar). Silakan order ulang.",
            )
        except Exception:  # noqa: BLE001
            pass


def build_application() -> Application:
    app = Application.builder().token(cfg.bot_token).build()
    app.bot_data["pakasir"] = PakasirClient()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("produk", cmd_produk))
    app.add_handler(CommandHandler("order", cmd_order))
    app.add_handler(CommandHandler("stok", cmd_stok))
    app.add_handler(CommandHandler("orders", cmd_orders))

    admin.register(app)  # /tambahproduk /addstok /produkadmin /aktif /nonaktif /hapusproduk

    app.add_handler(CallbackQueryHandler(cb_view, pattern=r"^view:"))
    app.add_handler(CallbackQueryHandler(cb_back, pattern=r"^back:"))
    app.add_handler(CallbackQueryHandler(cb_buy, pattern=r"^buy:"))
    app.add_handler(CallbackQueryHandler(cb_check, pattern=r"^check:"))

    app.job_queue.run_repeating(job_expire, interval=120, first=60)
    return app
