"""Test panel admin (tombol): lihat/hapus stok, edit deskripsi/cara pakai,
tambah stok via tombol, toggle aktif, hapus produk, pemisah === .
python smoke_test_adminpanel.py
"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("PAKASIR_PROJECT", "demo")
os.environ.setdefault("PAKASIR_API_KEY", "k")

from app import admin, db

ADMIN_ID = 1


class FakeUser:
    def __init__(self, uid):
        self.id = uid


class FakeQuery:
    def __init__(self, data, uid=ADMIN_ID):
        self.data = data
        self.from_user = FakeUser(uid)
        self.last_text = None
        self.last_markup = None

    async def answer(self, *a, **kw):
        pass

    async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
        self.last_text = text
        self.last_markup = reply_markup


class FakeMessage:
    def __init__(self, text, uid=ADMIN_ID):
        self.text = text
        self.replies = []

    async def reply_text(self, text, reply_markup=None, parse_mode=None):
        self.replies.append(text)


class FakeUpdate:
    def __init__(self, uid=ADMIN_ID, query=None, message=None):
        self.effective_user = FakeUser(uid)
        self.callback_query = query
        self.message = message


class FakeContext:
    def __init__(self):
        self.user_data = {}
        self.args = []


def buttons(markup):
    return [b for row in markup.inline_keyboard for b in row]


async def click(data, ctx, uid=ADMIN_ID):
    q = FakeQuery(data, uid)
    await admin.cb_admin_router(FakeUpdate(uid, query=q), ctx)
    return q


async def main():
    for f in ("bot.db", "bot.db-wal", "bot.db-shm"):
        if os.path.exists(f):
            os.remove(f)
    await db.connect()
    ctx = FakeContext()

    pid = await db.add_product("netflix1h", "Netflix Sharing 1 Hari", 3000, "account")
    await db.add_stock(pid, ["akun1@mail.com:pass1", "akun2@mail.com:pass2"])
    pid_file = await db.add_product("ebook", "E-book Panduan", 10000, "file", file_payload="https://drive/x")

    # 1) daftar produk
    text, kb = await admin._admin_list_view()
    labels = [b.text for b in buttons(kb)]
    assert any("Netflix Sharing 1 Hari" in l for l in labels)
    assert any("E-book Panduan" in l for l in labels)
    print("OK  daftar produk panel:", labels)

    # 2) detail produk (account) -> ada tombol stok, tanpa tombol stok utk produk file
    q = await click(f"adm:p:{pid}", ctx)
    assert "Netflix Sharing 1 Hari" in q.last_text and "2 tersedia" in q.last_text
    assert any("Lihat/Hapus Stok" in b.text for b in buttons(q.last_markup))
    q2 = await click(f"adm:p:{pid_file}", ctx)
    assert not any("Lihat/Hapus Stok" in b.text for b in buttons(q2.last_markup))
    print("OK  detail produk (tombol stok cuma utk akun/voucher)")

    # 3) toggle aktif/nonaktif
    p_before = await db.get_product(pid)
    assert p_before["active"] == 1
    q = await click(f"adm:tg:{pid}", ctx)
    p_after = await db.get_product(pid)
    assert p_after["active"] == 0
    assert "Nonaktif" in q.last_text
    await click(f"adm:tg:{pid}", ctx)  # nyalakan lagi
    print("OK  toggle aktif/nonaktif")

    # 4) edit deskripsi via tombol + pending text
    q = await click(f"adm:ed:{pid}", ctx)
    assert ctx.user_data["adm_pending"] == {"type": "ed", "pid": pid}
    msg = FakeMessage("Deskripsi baru yang rapi")
    await admin.on_admin_pending_text(FakeUpdate(ADMIN_ID, message=msg), ctx)
    assert "adm_pending" not in ctx.user_data
    p = await db.get_product(pid)
    assert p["description"] == "Deskripsi baru yang rapi"
    assert "Deskripsi disimpan" in msg.replies[0]
    print("OK  edit deskripsi via panel:", p["description"])

    # 5) edit cara pakai via tombol
    await click(f"adm:eu:{pid}", ctx)
    msg = FakeMessage("1. Login\n2. Jangan share")
    await admin.on_admin_pending_text(FakeUpdate(ADMIN_ID, message=msg), ctx)
    p = await db.get_product(pid)
    assert p["usage_note"] == "1. Login\n2. Jangan share"
    print("OK  edit cara pakai via panel")

    # 6) tambah stok via tombol, format pemisah ===
    await click(f"adm:as:{pid}", ctx)
    assert ctx.user_data["adm_pending"]["type"] == "as"
    stok_text = "Email : akun3@mail.com\nPassword : pass3\n===\nEmail : akun4@mail.com\nPassword : pass4"
    msg = FakeMessage(stok_text)
    await admin.on_admin_pending_text(FakeUpdate(ADMIN_ID, message=msg), ctx)
    avail, total = await db.stock_counts(pid)
    assert avail == 4 and total == 4, (avail, total)
    assert "+2 stok" in msg.replies[0]
    print("OK  tambah stok via panel dgn pemisah === :", avail, "tersedia")

    # 7) lihat & hapus stok lewat panel
    q = await click(f"adm:st:{pid}:0", ctx)
    stock_buttons = buttons(q.last_markup)
    del_btn = next(b for b in stock_buttons if b.callback_data.startswith("adm:sdc:"))
    q_confirm = await click(del_btn.callback_data, ctx)
    assert "Hapus item ini?" in q_confirm.last_text
    sid = int(del_btn.callback_data.split(":")[-1])
    await click(f"adm:sd:{pid}:{sid}", ctx)
    avail2, _ = await db.stock_counts(pid)
    assert avail2 == 3
    print("OK  hapus 1 item stok lewat panel, sisa:", avail2)

    # 8) perintah teks /lihatstok /hapusstok tetap jalan
    msg = FakeMessage("/lihatstok netflix1h")
    upd = FakeUpdate(ADMIN_ID, message=msg)
    ctx2 = FakeContext()
    ctx2.args = ["netflix1h"]
    await admin.cmd_lihatstok(upd, ctx2)
    assert "3 tersedia" in msg.replies[0] or "Stok Netflix" in msg.replies[0]
    print("OK  /lihatstok :", msg.replies[0].splitlines()[0])

    msg2 = FakeMessage("/hapusstok netflix1h 1")
    ctx3 = FakeContext()
    ctx3.args = ["netflix1h", "1"]
    await admin.cmd_hapusstok(FakeUpdate(ADMIN_ID, message=msg2), ctx3)
    avail3, _ = await db.stock_counts(pid)
    assert avail3 == 2
    print("OK  /hapusstok teks:", msg2.replies[0])

    # 9) hapus produk (belum pernah order) -> benar2 dihapus
    q = await click(f"adm:pdc:{pid_file}", ctx)
    assert "Yakin hapus" in q.last_text
    q = await click(f"adm:pd:{pid_file}", ctx)
    assert await db.get_product_by_code("ebook") is None
    assert "dihapus" in q.last_text
    print("OK  hapus produk lewat panel (confirm -> execute)")

    # 10) non-admin ditolak
    q = FakeQuery(f"adm:list", uid=999)
    await admin.cb_admin_router(FakeUpdate(999, query=q), ctx)
    assert q.last_text is None  # tidak pernah edit_message_text, cuma q.answer(alert)
    print("OK  non-admin ditolak panel")

    await db.close()
    for f in ("bot.db", "bot.db-wal", "bot.db-shm"):
        if os.path.exists(f):
            os.remove(f)
    print("\nSEMUA TEST PANEL ADMIN LULUS")


if __name__ == "__main__":
    asyncio.run(main())
