"""Perintah admin: kelola produk & stok langsung dari Telegram.

Wizard tambah produk:  /tambahproduk  → tanya jawab langkah demi langkah
Tambah stok cepat:     /addstok <code> lalu tempel payload (1 per baris)
Lihat / ubah:          /stok  /orders  /produkadmin
Aktif/nonaktif:        /aktif <code>   /nonaktif <code>
Hapus:                 /hapusproduk <code>
"""
from __future__ import annotations

import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from . import db
from .config import cfg
from .service import rupiah

log = logging.getLogger(__name__)

CODE, NAME, PRICE, DTYPE, FILE_PAYLOAD, DESC = range(6)
_TYPE_LABELS = {"akun": "account", "voucher": "voucher", "file": "file"}


def _is_admin(update: Update) -> bool:
    return bool(update.effective_user) and update.effective_user.id in cfg.admin_ids


def esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ── wizard tambah produk ─────────────────────────────────
async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if not _is_admin(update):
        return ConversationHandler.END
    context.user_data["new_product"] = {}
    await update.message.reply_text(
        "🆕 <b>Tambah produk</b> (ketik /batal untuk berhenti)\n\n"
        "1/6 — Masukkan <b>kode unik</b> produk (tanpa spasi), contoh: <code>netflix1p</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return CODE


async def add_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    code = update.message.text.strip().lower().replace(" ", "-")
    if await db.get_product_by_code(code):
        await update.message.reply_text(f"Kode '{code}' sudah dipakai. Masukkan kode lain.")
        return CODE
    context.user_data["new_product"]["code"] = code
    await update.message.reply_text("2/6 — Nama produk (yang dilihat pembeli):")
    return NAME


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_product"]["name"] = update.message.text.strip()
    await update.message.reply_text("3/6 — Harga dalam rupiah (angka saja), contoh: <code>25000</code>", parse_mode=ParseMode.HTML)
    return PRICE


async def add_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip().replace(".", "").replace(",", "").replace("rp", "").replace("Rp", "")
    if not raw.isdigit() or int(raw) <= 0:
        await update.message.reply_text("Harga tidak valid. Masukkan angka, contoh 25000.")
        return PRICE
    context.user_data["new_product"]["price"] = int(raw)
    await update.message.reply_text(
        "4/6 — Jenis pengiriman produk:",
        reply_markup=ReplyKeyboardMarkup(
            [["akun", "voucher", "file"]], one_time_keyboard=True, resize_keyboard=True
        ),
    )
    return DTYPE


async def add_dtype(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text.strip().lower()
    if choice not in _TYPE_LABELS:
        await update.message.reply_text("Pilih: akun, voucher, atau file.")
        return DTYPE
    context.user_data["new_product"]["delivery_type"] = _TYPE_LABELS[choice]
    if choice == "file":
        await update.message.reply_text(
            "5/6 — Kirim <b>link/teks file</b> yang dikirim ke SEMUA pembeli "
            "(stok tak terbatas), contoh link Google Drive.\n"
            "Atau ketik <code>-</code> jika mau pakai sistem stok (kirim stok lewat /addstok).",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return FILE_PAYLOAD
    await update.message.reply_text(
        "5/6 — (lewati) Stok akan diisi lewat /addstok setelah produk dibuat.\n"
        "Lanjut ke deskripsi — kirim deskripsi singkat, atau ketik <code>-</code> untuk kosong.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return DESC


async def add_file_payload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    context.user_data["new_product"]["file_payload"] = "" if txt == "-" else txt
    await update.message.reply_text("6/6 — Deskripsi singkat, atau ketik <code>-</code> untuk kosong.", parse_mode=ParseMode.HTML)
    return DESC


async def add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    np = context.user_data["new_product"]
    np["description"] = "" if txt == "-" else txt
    pid = await db.add_product(
        code=np["code"], name=np["name"], price=np["price"],
        delivery_type=np["delivery_type"], description=np.get("description", ""),
        file_payload=np.get("file_payload", ""),
    )
    hint = (
        "Produk pakai link tetap — langsung aktif & siap dijual."
        if np.get("file_payload")
        else f"Sekarang isi stok:\n<code>/addstok {np['code']}</code> lalu tempel data (1 per baris)."
    )
    await update.message.reply_text(
        f"✅ Produk <b>#{pid}</b> dibuat:\n"
        f"• kode: <code>{esc(np['code'])}</code>\n"
        f"• {esc(np['name'])} — {rupiah(np['price'])}\n"
        f"• jenis: {np['delivery_type']}\n\n{hint}",
        parse_mode=ParseMode.HTML,
    )
    context.user_data.pop("new_product", None)
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_product", None)
    await update.message.reply_text("Dibatalkan.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ── stok cepat ───────────────────────────────────────────
async def cmd_addstok(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    lines = (update.message.text or "").split("\n")
    head = lines[0].split()
    if len(head) < 2:
        await update.message.reply_text(
            "Format:\n<code>/addstok KODE</code>\nbaris1\nbaris2\n...", parse_mode=ParseMode.HTML
        )
        return
    code = head[1].lower()
    payloads = [ln.strip() for ln in lines[1:] if ln.strip()]
    # dukung juga sisa di baris pertama: /addstok kode email:pass
    if len(head) > 2:
        payloads.insert(0, " ".join(head[2:]))
    if not payloads:
        await update.message.reply_text("Tidak ada baris stok yang diberikan.")
        return
    product = await db.get_product_by_code(code)
    if product is None:
        await update.message.reply_text(f"Produk '{code}' tidak ada. Cek /produkadmin.")
        return
    n = await db.add_stock(product["id"], payloads)
    total = await db.available_stock(product["id"])
    await update.message.reply_text(f"✅ +{n} stok '{code}'. Total tersedia: {total}.")


# ── lihat / ubah ─────────────────────────────────────────
async def cmd_produkadmin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    products = await db.list_products(only_active=False)
    if not products:
        await update.message.reply_text("Belum ada produk. Buat dengan /tambahproduk.")
        return
    out = ["<b>Semua produk</b>"]
    for p in products:
        s = await db.available_stock(p["id"])
        flag = "🟢" if p["active"] else "🔴"
        out.append(
            f"{flag} <code>{esc(p['code'])}</code> — {esc(p['name'])} · {rupiah(p['price'])} · "
            f"{p['delivery_type']} · stok {s}"
        )
    out.append("\n/aktif &lt;kode&gt; · /nonaktif &lt;kode&gt; · /hapusproduk &lt;kode&gt;")
    await update.message.reply_text("\n".join(out), parse_mode=ParseMode.HTML)


async def cmd_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    active = update.message.text.lstrip("/").split()[0] == "aktif"
    if not context.args:
        await update.message.reply_text(f"Format: /{'aktif' if active else 'nonaktif'} <kode>")
        return
    ok = await db.set_product_active(context.args[0].lower(), active)
    await update.message.reply_text(
        f"{'Diaktifkan' if active else 'Dinonaktifkan'}: {context.args[0]}" if ok else "Kode tidak ditemukan."
    )


async def cmd_hapusproduk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Format: /hapusproduk <kode>")
        return
    res = await db.delete_product(context.args[0].lower())
    await update.message.reply_text(
        {
            "not_found": "Kode tidak ditemukan.",
            "deleted": f"🗑️ Produk '{context.args[0]}' dihapus.",
            "deactivated": f"Produk '{context.args[0]}' punya riwayat order — dinonaktifkan (tidak dihapus).",
        }[res]
    )


def register(app: Application) -> None:
    wizard = ConversationHandler(
        entry_points=[CommandHandler("tambahproduk", add_start)],
        states={
            CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_code)],
            NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_name)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_price)],
            DTYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_dtype)],
            FILE_PAYLOAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_file_payload)],
            DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_desc)],
        },
        fallbacks=[CommandHandler("batal", add_cancel)],
        conversation_timeout=300,
    )
    app.add_handler(wizard)
    app.add_handler(CommandHandler("addstok", cmd_addstok))
    app.add_handler(CommandHandler("produkadmin", cmd_produkadmin))
    app.add_handler(CommandHandler(["aktif", "nonaktif"], cmd_toggle))
    app.add_handler(CommandHandler("hapusproduk", cmd_hapusproduk))
