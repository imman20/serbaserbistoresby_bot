"""Perintah admin: kelola produk & stok langsung dari Telegram.

Wizard tambah produk:  /tambahproduk  → tanya jawab langkah demi langkah
Tambah stok cepat:     /addstok <code> lalu tempel payload
                       (satu baris = satu item; ATAU pisahkan item multi-baris
                       dengan baris berisi --- , lihat cmd_addstok)
Cara pakai:            /setcarapakai <code> lalu tempel teksnya
Lihat / ubah:          /stok  /orders  /produkadmin
Aktif/nonaktif:        /aktif <code>   /nonaktif <code>
Hapus:                 /hapusproduk <code>
"""
from __future__ import annotations

import logging
import re

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

CODE, NAME, GROUP, PRICE, DTYPE, FILE_PAYLOAD, DESC, USAGE = range(8)
_TYPE_LABELS = {"akun": "account", "voucher": "voucher", "file": "file"}


def _slugify(text: str) -> str:
    return text.strip().lower().replace(" ", "-")


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
        "1/8 — Masukkan <b>kode unik</b> produk (tanpa spasi), contoh: <code>netflix1p</code>",
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
    await update.message.reply_text("2/8 — Nama produk (yang dilihat pembeli), contoh: <code>Netflix Sharing 7 Hari</code>", parse_mode=ParseMode.HTML)
    return NAME


async def add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["new_product"]["name"] = update.message.text.strip()
    await update.message.reply_text(
        "3/8 — Apakah produk ini salah satu pilihan <b>durasi/varian</b> dari sebuah grup "
        "(mis. Netflix punya pilihan 1 Hari / 3 Hari / 7 Hari)?\n\n"
        "Kalau YA, ketik: <code>nama grup | label varian ini</code>\n"
        "Contoh: <code>Netflix Sharing | 7 Hari</code>\n"
        "(pakai <b>nama grup yang SAMA PERSIS</b> tiap kali menambah varian baru di grup itu)\n\n"
        "Kalau produk ini berdiri sendiri (tidak ada varian), ketik <code>-</code>",
        parse_mode=ParseMode.HTML,
    )
    return GROUP


async def add_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    np = context.user_data["new_product"]
    if txt == "-":
        np["group_code"] = np["group_name"] = np["variant_label"] = ""
    elif "|" in txt:
        group_name, variant_label = (p.strip() for p in txt.split("|", 1))
        if not group_name or not variant_label:
            await update.message.reply_text("Format kurang lengkap. Ulangi: <code>nama grup | label varian</code>", parse_mode=ParseMode.HTML)
            return GROUP
        np["group_code"] = _slugify(group_name)
        np["group_name"] = group_name
        np["variant_label"] = variant_label
    else:
        await update.message.reply_text(
            "Formatnya harus ada tanda <code>|</code> di antara nama grup dan label varian, "
            "atau ketik <code>-</code> kalau tidak ada varian.", parse_mode=ParseMode.HTML,
        )
        return GROUP
    await update.message.reply_text("4/8 — Harga dalam rupiah (angka saja), contoh: <code>25000</code>", parse_mode=ParseMode.HTML)
    return PRICE


async def add_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip().replace(".", "").replace(",", "").replace("rp", "").replace("Rp", "")
    if not raw.isdigit() or int(raw) <= 0:
        await update.message.reply_text("Harga tidak valid. Masukkan angka, contoh 25000.")
        return PRICE
    context.user_data["new_product"]["price"] = int(raw)
    await update.message.reply_text(
        "5/8 — Jenis pengiriman produk:",
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
            "6/8 — Kirim <b>link/teks file</b> yang dikirim ke SEMUA pembeli "
            "(stok tak terbatas), contoh link Google Drive.\n"
            "Atau ketik <code>-</code> jika mau pakai sistem stok (kirim stok lewat /addstok).",
            parse_mode=ParseMode.HTML,
            reply_markup=ReplyKeyboardRemove(),
        )
        return FILE_PAYLOAD
    await update.message.reply_text(
        "6/8 — (lewati) Stok akan diisi lewat /addstok setelah produk dibuat.\n"
        "Lanjut ke deskripsi — kirim deskripsi singkat (tampil di katalog), atau ketik <code>-</code> untuk kosong.",
        parse_mode=ParseMode.HTML,
        reply_markup=ReplyKeyboardRemove(),
    )
    return DESC


async def add_file_payload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    context.user_data["new_product"]["file_payload"] = "" if txt == "-" else txt
    await update.message.reply_text(
        "7/8 — Deskripsi singkat (tampil di katalog), atau ketik <code>-</code> untuk kosong.",
        parse_mode=ParseMode.HTML,
    )
    return DESC


async def add_desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    context.user_data["new_product"]["description"] = "" if txt == "-" else txt
    await update.message.reply_text(
        "8/8 — <b>Cara pakai</b> (opsional): instruksi yang dikirim ke pembeli SETELAH bayar, "
        "terpisah dari deskripsi. Boleh beberapa baris. Ketik <code>-</code> untuk kosong.\n\n"
        "Contoh:\n<code>1. Login pakai email &amp; password di atas\n"
        "2. Jangan ganti profil orang lain\n"
        "3. Kalau logout sendiri, chat admin</code>",
        parse_mode=ParseMode.HTML,
    )
    return USAGE


async def add_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    txt = update.message.text.strip()
    np = context.user_data["new_product"]
    np["usage_note"] = "" if txt == "-" else update.message.text.strip()
    pid = await db.add_product(
        code=np["code"], name=np["name"], price=np["price"],
        delivery_type=np["delivery_type"], description=np.get("description", ""),
        file_payload=np.get("file_payload", ""),
        group_code=np.get("group_code", ""), group_name=np.get("group_name", ""),
        variant_label=np.get("variant_label", ""), usage_note=np.get("usage_note", ""),
    )
    hint = (
        "Produk pakai link tetap — langsung aktif & siap dijual."
        if np.get("file_payload")
        else f"Sekarang isi stok:\n<code>/addstok {np['code']}</code> lalu tempel data."
    )
    grup_line = f"• grup: {esc(np['group_name'])} — {esc(np['variant_label'])}\n" if np.get("group_code") else ""
    await update.message.reply_text(
        f"✅ Produk <b>#{pid}</b> dibuat:\n"
        f"• kode: <code>{esc(np['code'])}</code>\n"
        f"• {esc(np['name'])} — {rupiah(np['price'])}\n"
        f"• jenis: {np['delivery_type']}\n"
        f"{grup_line}\n{hint}",
        parse_mode=ParseMode.HTML,
    )
    context.user_data.pop("new_product", None)
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("new_product", None)
    await update.message.reply_text("Dibatalkan.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ── stok cepat ───────────────────────────────────────────
_ITEM_SEP = re.compile(r"^-{3,}$")


def _parse_stock_body(lines: list[str]) -> list[str]:
    """Ubah baris-baris jadi list payload.

    - Kalau ADA baris pemisah "---": tiap blok di antara pemisah = SATU item
      (boleh berisi banyak baris, mis. "Email: x\\nPassword: y").
    - Kalau TIDAK ADA baris "---": kembali ke perilaku lama — satu baris = satu item.
    """
    if any(_ITEM_SEP.match(ln.strip()) for ln in lines):
        blocks, current = [], []
        for ln in lines:
            if _ITEM_SEP.match(ln.strip()):
                blocks.append(current)
                current = []
            else:
                current.append(ln)
        blocks.append(current)
        return [
            "\n".join(b).strip("\n") for b in blocks if "\n".join(b).strip()
        ]
    return [ln.strip() for ln in lines if ln.strip()]


async def cmd_addstok(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    lines = (update.message.text or "").split("\n")
    head = lines[0].split()
    if len(head) < 2:
        await update.message.reply_text(
            "Format sederhana (satu baris = satu item):\n"
            "<code>/addstok KODE</code>\nemail1:pass1\nemail2:pass2\n\n"
            "Format multi-baris rapi (pisahkan tiap item dengan baris <code>---</code>):\n"
            "<code>/addstok KODE</code>\n"
            "Email : akun1@mail.com\nPassword : pass123\n---\n"
            "Email : akun2@mail.com\nPassword : pass456",
            parse_mode=ParseMode.HTML,
        )
        return
    code = head[1].lower()
    body_lines = lines[1:]
    if len(head) > 2:  # dukung sisa di baris pertama: /addstok kode email:pass
        body_lines = [" ".join(head[2:])] + body_lines
    payloads = _parse_stock_body(body_lines)
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


# ── cara pakai ───────────────────────────────────────────
async def cmd_setcarapakai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    lines = (update.message.text or "").split("\n")
    head = lines[0].split()
    if len(head) < 2:
        await update.message.reply_text(
            "Format:\n<code>/setcarapakai KODE</code>\nbaris cara pakai 1\nbaris cara pakai 2\n...\n\n"
            "Ketik <code>/setcarapakai KODE -</code> untuk mengosongkan.",
            parse_mode=ParseMode.HTML,
        )
        return
    code = head[1].lower()
    rest_of_head = " ".join(head[2:])
    body = "\n".join([rest_of_head] + lines[1:]).strip() if rest_of_head else "\n".join(lines[1:]).strip()
    text = "" if body == "-" else body
    ok = await db.set_usage_note(code, text)
    if not ok:
        await update.message.reply_text(f"Produk '{code}' tidak ada. Cek /produkadmin.")
        return
    await update.message.reply_text("✅ Cara pakai dikosongkan." if not text else "✅ Cara pakai disimpan.")


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
        grup = f" [grup: {esc(p['group_name'])} · {esc(p['variant_label'])}]" if p["group_code"] else ""
        out.append(
            f"{flag} <code>{esc(p['code'])}</code> — {esc(p['name'])} · {rupiah(p['price'])} · "
            f"{p['delivery_type']} · stok {s}{grup}"
        )
    out.append("\n/aktif &lt;kode&gt; · /nonaktif &lt;kode&gt; · /hapusproduk &lt;kode&gt; · /setcarapakai &lt;kode&gt;")
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
            GROUP: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_group)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_price)],
            DTYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_dtype)],
            FILE_PAYLOAD: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_file_payload)],
            DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_desc)],
            USAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_usage)],
        },
        fallbacks=[CommandHandler("batal", add_cancel)],
        conversation_timeout=300,
    )
    app.add_handler(wizard)
    app.add_handler(CommandHandler("addstok", cmd_addstok))
    app.add_handler(CommandHandler("setcarapakai", cmd_setcarapakai))
    app.add_handler(CommandHandler("produkadmin", cmd_produkadmin))
    app.add_handler(CommandHandler(["aktif", "nonaktif"], cmd_toggle))
    app.add_handler(CommandHandler("hapusproduk", cmd_hapusproduk))
