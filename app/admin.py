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
_ITEM_SEP = re.compile(r"^(-{3,}|={3,})$")  # baris pemisah item: --- ATAU ===


def _parse_stock_body(lines: list[str]) -> list[str]:
    """Ubah baris-baris jadi list payload.

    - Kalau ADA baris pemisah "---" atau "===": tiap blok di antara pemisah = SATU
      item (boleh berisi banyak baris, mis. "Email: x\\nPassword: y").
    - Kalau TIDAK ADA baris pemisah: kembali ke perilaku lama — satu baris = satu item.
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
            "Format multi-baris rapi (pisahkan tiap item dengan baris <code>---</code> atau <code>===</code>):\n"
            "<code>/addstok KODE</code>\n"
            "Email : akun1@mail.com\nPassword : pass123\n===\n"
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


def _parse_code_and_body(raw_text: str) -> tuple[str, str] | None:
    """Parse "/perintah KODE\\nbaris1\\nbaris2..." -> (kode, teks). None kalau kode tak ada."""
    lines = (raw_text or "").split("\n")
    head = lines[0].split()
    if len(head) < 2:
        return None
    code = head[1].lower()
    rest_of_head = " ".join(head[2:])
    body = "\n".join([rest_of_head] + lines[1:]).strip() if rest_of_head else "\n".join(lines[1:]).strip()
    return code, ("" if body == "-" else body)


# ── cara pakai & deskripsi ───────────────────────────────
async def cmd_setcarapakai(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    parsed = _parse_code_and_body(update.message.text)
    if parsed is None:
        await update.message.reply_text(
            "Format:\n<code>/setcarapakai KODE</code>\nbaris cara pakai 1\nbaris cara pakai 2\n...\n\n"
            "Ketik <code>/setcarapakai KODE -</code> untuk mengosongkan.",
            parse_mode=ParseMode.HTML,
        )
        return
    code, text = parsed
    if not await db.set_usage_note(code, text):
        await update.message.reply_text(f"Produk '{code}' tidak ada. Cek /produkadmin.")
        return
    await update.message.reply_text("✅ Cara pakai dikosongkan." if not text else "✅ Cara pakai disimpan.")


async def cmd_editdeskripsi(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    parsed = _parse_code_and_body(update.message.text)
    if parsed is None:
        await update.message.reply_text(
            "Format:\n<code>/editdeskripsi KODE</code>\ndeskripsi baru di sini...\n\n"
            "Ketik <code>/editdeskripsi KODE -</code> untuk mengosongkan.",
            parse_mode=ParseMode.HTML,
        )
        return
    code, text = parsed
    if not await db.set_description(code, text):
        await update.message.reply_text(f"Produk '{code}' tidak ada. Cek /produkadmin.")
        return
    await update.message.reply_text("✅ Deskripsi dikosongkan." if not text else "✅ Deskripsi disimpan.")


# ── lihat / hapus stok ───────────────────────────────────
def _stock_preview(payload: str, width: int = 42) -> str:
    first_line = payload.split("\n", 1)[0]
    return first_line if len(first_line) <= width else first_line[: width - 1] + "…"


async def cmd_lihatstok(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Format: /lihatstok <kode>")
        return
    code = context.args[0].lower()
    product = await db.get_product_by_code(code)
    if product is None:
        await update.message.reply_text(f"Produk '{code}' tidak ada.")
        return
    items = await db.list_stock(product["id"])
    if not items:
        await update.message.reply_text(f"Stok '{code}' kosong.")
        return
    out = [f"<b>Stok {esc(product['name'])}</b> ({len(items)} tersedia)"]
    for i, it in enumerate(items, start=1):
        out.append(f"{i}. <code>{esc(_stock_preview(it['payload']))}</code>")
    out.append(f"\nHapus salah satu: <code>/hapusstok {code} &lt;nomor&gt;</code>")
    await update.message.reply_text("\n".join(out), parse_mode=ParseMode.HTML)


async def cmd_hapusstok(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    if len(context.args) < 2 or not context.args[1].isdigit():
        await update.message.reply_text("Format: /hapusstok <kode> <nomor>  (lihat nomornya di /lihatstok <kode>)")
        return
    code, nomor = context.args[0].lower(), int(context.args[1])
    product = await db.get_product_by_code(code)
    if product is None:
        await update.message.reply_text(f"Produk '{code}' tidak ada.")
        return
    items = await db.list_stock(product["id"])
    if nomor < 1 or nomor > len(items):
        await update.message.reply_text(f"Nomor tidak valid. Ada {len(items)} item (1–{len(items)}).")
        return
    ok = await db.delete_stock_item(items[nomor - 1]["id"])
    total = await db.available_stock(product["id"])
    await update.message.reply_text(
        f"✅ Item #{nomor} dihapus. Sisa stok: {total}." if ok else "Gagal menghapus (item mungkin sudah terjual)."
    )


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


# ── panel admin (tombol, tanpa perlu hafal perintah) ─────
_TYPE_LABEL = {"account": "📧 Akun Sharing", "voucher": "🎫 Voucher/Lisensi", "file": "📁 File/Link"}
_PAGE = 8


async def _admin_list_view() -> tuple[str, InlineKeyboardMarkup]:
    products = await db.list_products(only_active=False)
    if not products:
        return "Belum ada produk. Ketik /tambahproduk untuk membuat produk pertama.", InlineKeyboardMarkup([])
    rows = [
        [InlineKeyboardButton(
            f"{'🟢' if p['active'] else '🔴'} {p['name']} — {rupiah(p['price'])}",
            callback_data=f"adm:p:{p['id']}",
        )]
        for p in products
    ]
    text = (
        f"⚙️ <b>Panel Admin</b> — {len(products)} produk\n"
        "Tap produk untuk kelola (stok, deskripsi, cara pakai, aktif/nonaktif, hapus).\n"
        "Produk baru: /tambahproduk"
    )
    return text, InlineKeyboardMarkup(rows)


async def _render_product_detail(p) -> tuple[str, InlineKeyboardMarkup]:
    pid = p["id"]
    flag = "🟢 Aktif" if p["active"] else "🔴 Nonaktif"
    grup_line = f"\n🔗 Grup: {esc(p['group_name'])} · {esc(p['variant_label'])}" if p["group_code"] else ""
    has_stock_system = p["delivery_type"] in ("account", "voucher")
    if has_stock_system:
        avail, total = await db.stock_counts(pid)
        stok_line = f"\n📦 Stok: {avail} tersedia (dari {total} pernah ditambah)"
    elif p["file_payload"]:
        stok_line = "\n📦 Stok: tak terbatas (link/teks tetap)"
    else:
        stok_line = "\n📦 Stok: (belum ada link — edit lewat /tambahproduk baru)"
    desc_line = f"\n📝 Deskripsi: {esc(p['description'])}" if p["description"] else "\n📝 Deskripsi: (kosong)"
    usage_line = (
        f"\n📖 Cara pakai: {esc(_stock_preview(p['usage_note'], 70))}"
        if p["usage_note"] else "\n📖 Cara pakai: (kosong)"
    )
    text = (
        f"<b>{esc(p['name'])}</b> — {flag}\n"
        f"Kode: <code>{esc(p['code'])}</code>\n"
        f"Harga: {rupiah(p['price'])} · {_TYPE_LABEL[p['delivery_type']]}"
        f"{grup_line}{stok_line}{desc_line}{usage_line}"
    )
    rows = []
    if has_stock_system:
        rows.append([
            InlineKeyboardButton("📋 Lihat/Hapus Stok", callback_data=f"adm:st:{pid}:0"),
            InlineKeyboardButton("➕ Tambah Stok", callback_data=f"adm:as:{pid}"),
        ])
    rows.append([
        InlineKeyboardButton("✏️ Edit Deskripsi", callback_data=f"adm:ed:{pid}"),
        InlineKeyboardButton("✏️ Edit Cara Pakai", callback_data=f"adm:eu:{pid}"),
    ])
    rows.append([
        InlineKeyboardButton("🔴 Nonaktifkan" if p["active"] else "🟢 Aktifkan", callback_data=f"adm:tg:{pid}"),
        InlineKeyboardButton("🗑 Hapus Produk", callback_data=f"adm:pdc:{pid}"),
    ])
    rows.append([InlineKeyboardButton("⬅️ Daftar Produk", callback_data="adm:list")])
    return text, InlineKeyboardMarkup(rows)


async def _render_stock_list(q, p, offset: int) -> None:
    items = await db.list_stock(p["id"], limit=_PAGE, offset=offset)
    avail, _ = await db.stock_counts(p["id"])
    rows = [
        [InlineKeyboardButton(f"🗑 {_stock_preview(it['payload'], 34)}", callback_data=f"adm:sdc:{p['id']}:{it['id']}")]
        for it in items
    ]
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton("⬅️ Sebelumnya", callback_data=f"adm:st:{p['id']}:{max(0, offset - _PAGE)}"))
    if offset + _PAGE < avail:
        nav.append(InlineKeyboardButton("Berikutnya ➡️", callback_data=f"adm:st:{p['id']}:{offset + _PAGE}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("⬅️ Kembali ke produk", callback_data=f"adm:p:{p['id']}")])
    header = (
        f"📋 <b>Stok {esc(p['name'])}</b> — {avail} tersedia\nTap item untuk hapus."
        if items else f"📋 <b>Stok {esc(p['name'])}</b>\nStok kosong. Tambah lewat tombol ➕ Tambah Stok."
    )
    await q.edit_message_text(header, reply_markup=InlineKeyboardMarkup(rows), parse_mode=ParseMode.HTML)


async def cmd_adminpanel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_admin(update):
        return
    text, kb = await _admin_list_view()
    await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def cb_admin_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not _is_admin(update):
        await q.answer("Khusus admin.", show_alert=True)
        return
    await q.answer()
    parts = q.data.split(":")
    action = parts[1]

    if action == "list":
        text, kb = await _admin_list_view()
        await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

    pid = int(parts[2])
    p = await db.get_product(pid)
    if p is None:
        await q.edit_message_text("Produk sudah tidak ada.")
        return

    if action == "p":
        text, kb = await _render_product_detail(p)
        await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    elif action == "tg":
        await db.set_product_active(p["code"], not p["active"])
        text, kb = await _render_product_detail(await db.get_product(pid))
        await q.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)

    elif action == "ed":
        context.user_data["adm_pending"] = {"type": "ed", "pid": pid}
        await q.edit_message_text(
            f"✏️ Kirim <b>deskripsi baru</b> untuk <b>{esc(p['name'])}</b> (atau <code>-</code> untuk kosongkan).\n"
            "Ketik /batal untuk membatalkan.", parse_mode=ParseMode.HTML,
        )

    elif action == "eu":
        context.user_data["adm_pending"] = {"type": "eu", "pid": pid}
        await q.edit_message_text(
            f"✏️ Kirim <b>cara pakai baru</b> untuk <b>{esc(p['name'])}</b> (atau <code>-</code> untuk kosongkan). "
            "Boleh beberapa baris.\nKetik /batal untuk membatalkan.", parse_mode=ParseMode.HTML,
        )

    elif action == "as":
        if p["delivery_type"] not in ("account", "voucher"):
            await q.answer("Produk jenis ini tidak pakai sistem stok.", show_alert=True)
            return
        context.user_data["adm_pending"] = {"type": "as", "pid": pid}
        await q.edit_message_text(
            f"➕ Kirim data stok untuk <b>{esc(p['name'])}</b> sekarang.\n"
            "Satu baris satu item, ATAU pisahkan tiap item multi-baris dengan "
            "<code>---</code> / <code>===</code>.\nKetik /batal untuk membatalkan.",
            parse_mode=ParseMode.HTML,
        )

    elif action == "st":
        await _render_stock_list(q, p, int(parts[3]))

    elif action == "sdc":
        sid = int(parts[3])
        item = await db.get_stock_item(sid)
        preview = esc(_stock_preview(item["payload"], 300)) if item else "(item tidak ditemukan)"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Ya, hapus", callback_data=f"adm:sd:{pid}:{sid}")],
            [InlineKeyboardButton("⬅️ Batal", callback_data=f"adm:st:{pid}:0")],
        ])
        await q.edit_message_text(f"Hapus item ini?\n\n<code>{preview}</code>", reply_markup=kb, parse_mode=ParseMode.HTML)

    elif action == "sd":
        await db.delete_stock_item(int(parts[3]))
        await _render_stock_list(q, p, 0)

    elif action == "pdc":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Ya, hapus produk ini", callback_data=f"adm:pd:{pid}")],
            [InlineKeyboardButton("⬅️ Batal", callback_data=f"adm:p:{pid}")],
        ])
        await q.edit_message_text(
            f"⚠️ Yakin hapus <b>{esc(p['name'])}</b>? Kalau produk ini pernah ada order, "
            "produk akan dinonaktifkan saja (riwayat order tidak boleh hilang).",
            reply_markup=kb, parse_mode=ParseMode.HTML,
        )

    elif action == "pd":
        res = await db.delete_product(p["code"])
        if res == "deleted":
            text, kb = await _admin_list_view()
            await q.edit_message_text(f"🗑️ '{esc(p['name'])}' dihapus.\n\n{text}", reply_markup=kb, parse_mode=ParseMode.HTML)
        else:
            text, kb = await _render_product_detail(await db.get_product(pid))
            await q.edit_message_text(
                f"Produk punya riwayat order — dinonaktifkan saja.\n\n{text}", reply_markup=kb, parse_mode=ParseMode.HTML,
            )


async def on_admin_pending_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tangkap balasan teks setelah admin tap Edit Deskripsi / Edit Cara Pakai / Tambah Stok di panel."""
    pending = context.user_data.get("adm_pending")
    if not pending or not _is_admin(update):
        return
    context.user_data.pop("adm_pending", None)
    pid = pending["pid"]
    p = await db.get_product(pid)
    if p is None:
        await update.message.reply_text("Produk sudah tidak ada.")
        return
    txt = update.message.text.strip()

    if pending["type"] == "ed":
        await db.set_description(p["code"], "" if txt == "-" else txt)
        await update.message.reply_text("✅ Deskripsi dikosongkan." if txt == "-" else "✅ Deskripsi disimpan.")
    elif pending["type"] == "eu":
        await db.set_usage_note(p["code"], "" if txt == "-" else txt)
        await update.message.reply_text("✅ Cara pakai dikosongkan." if txt == "-" else "✅ Cara pakai disimpan.")
    elif pending["type"] == "as":
        payloads = _parse_stock_body(update.message.text.split("\n"))
        if not payloads:
            await update.message.reply_text("Tidak ada data stok yang terbaca. Coba lagi lewat tombol ➕ Tambah Stok.")
            return
        n = await db.add_stock(pid, payloads)
        total = await db.available_stock(pid)
        await update.message.reply_text(f"✅ +{n} stok. Total tersedia: {total}.")

    text, kb = await _render_product_detail(await db.get_product(pid))
    await update.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)


async def cmd_batal_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.pop("adm_pending", None):
        await update.message.reply_text("Dibatalkan.")


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
    app.add_handler(CommandHandler("lihatstok", cmd_lihatstok))
    app.add_handler(CommandHandler("hapusstok", cmd_hapusstok))
    app.add_handler(CommandHandler("setcarapakai", cmd_setcarapakai))
    app.add_handler(CommandHandler("editdeskripsi", cmd_editdeskripsi))
    app.add_handler(CommandHandler("produkadmin", cmd_produkadmin))
    app.add_handler(CommandHandler(["aktif", "nonaktif"], cmd_toggle))
    app.add_handler(CommandHandler("hapusproduk", cmd_hapusproduk))

    # panel admin (tombol)
    app.add_handler(CommandHandler("admin", cmd_adminpanel))
    app.add_handler(CallbackQueryHandler(cb_admin_router, pattern=r"^adm:"))
    app.add_handler(CommandHandler("batal", cmd_batal_admin))
    # ditaruh di group=1: hanya diproses kalau tidak ada handler group=0 yang cocok
    # (mis. saat wizard /tambahproduk sedang aktif, ConversationHandler yang menang).
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.User(user_id=list(cfg.admin_ids)),
            on_admin_pending_text,
        ),
        group=1,
    )
