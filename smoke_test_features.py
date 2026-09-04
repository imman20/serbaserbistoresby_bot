"""Smoke test untuk fitur baru: notifikasi channel & wajib-join. python smoke_test_features.py"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("PAKASIR_PROJECT", "demo")
os.environ.setdefault("PAKASIR_API_KEY", "k")
os.environ["SALES_CHANNEL_ID"] = "@tokoku_channel"
os.environ["REQUIRED_CHANNEL"] = "@tokoku_channel"

from app import db, service
from app.pakasir import CreatedPayment, TransactionStatus


class FakePakasir:
    async def create_qris(self, order_id, amount):
        return CreatedPayment(order_id, amount, 250, amount + 250, "qris", "FAKE" + order_id, "2026", {})

    async def transaction_detail(self, order_id, amount):
        return TransactionStatus(order_id, amount, "completed", "qris", "2026-09-04", {})


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kw):
        self.sent.append((chat_id, text))

    async def send_photo(self, **kw):
        pass


class FakeApp:
    def __init__(self):
        self.bot = FakeBot()


async def main():
    for f in ("bot.db", "bot.db-wal", "bot.db-shm"):
        if os.path.exists(f):
            os.remove(f)
    await db.connect()

    # format_tanggal_wib / buyer_label
    text = service.format_tanggal_wib(1893826740)  # sanity: no crash, sensible shape
    assert " — " in text and "WIB" in text
    assert service.buyer_label(123, "budi") == "@budi"
    assert service.buyer_label(123, "") == "ID 123"
    print("OK  format_tanggal_wib/buyer_label:", text)

    pid = await db.add_product("t1", "Netflix Test", 25000, "account")
    await db.add_stock(pid, ["a@x.com:1", "b@x.com:2", "c@x.com:3"])

    order, err = await service.start_order(FakePakasir(), 999, "buyer", pid)
    assert err is None, err

    app = FakeApp()
    assert await service.settle_if_paid(app, FakePakasir(), order["order_id"]) == "delivered"

    buyer_msgs = [t for c, t in app.bot.sent if c == 999]
    assert len(buyer_msgs) == 1
    assert "PRODUK KAMU SUDAH SIAP" in buyer_msgs[0]
    assert "a@x.com:1" in buyer_msgs[0]
    print("OK  pesan pembeli format baru")

    channel_msgs = [t for c, t in app.bot.sent if c == "@tokoku_channel"]
    assert len(channel_msgs) == 1, "notifikasi channel tidak terkirim / terkirim >1x"
    ct = channel_msgs[0]
    assert "Penjualan Baru" in ct and "Netflix Test" in ct and "Sisa Stok: 2 dari 3 pcs" in ct
    assert "@buyer" in ct and order["order_id"] in ct
    print("OK  notifikasi sales channel:", repr(ct[:70]))

    # order kedua tanpa produk bertipe file->unlimited: pastikan tidak crash & tidak ada baris "Sisa Stok"
    pid2 = await db.add_product("t2", "Ebook Test", 10000, "file", file_payload="https://drive/x")
    order2, err2 = await service.start_order(FakePakasir(), 888, "budi", pid2)
    assert err2 is None
    app.bot.sent.clear()
    assert await service.settle_if_paid(app, FakePakasir(), order2["order_id"]) == "delivered"
    ct2 = [t for c, t in app.bot.sent if c == "@tokoku_channel"][0]
    assert "Sisa Stok" not in ct2, "produk 'file' seharusnya tidak menampilkan sisa stok"
    print("OK  notifikasi channel utk produk file (tanpa baris stok)")

    await db.close()
    for f in ("bot.db", "bot.db-wal", "bot.db-shm"):
        if os.path.exists(f):
            os.remove(f)
    print("\nSEMUA TEST FITUR BARU LULUS")


if __name__ == "__main__":
    asyncio.run(main())
