"""Smoke test end-to-end dengan Pakasir & Telegram palsu. Jalankan: python smoke_test.py"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("PAKASIR_PROJECT", "demo")
os.environ.setdefault("PAKASIR_API_KEY", "k")

from app import db, service
from app.pakasir import CreatedPayment, TransactionStatus


class FakePakasir:
    def __init__(self, completed=True):
        self.completed = completed

    async def create_qris(self, order_id, amount):
        return CreatedPayment(order_id, amount, 250, amount + 250, "qris", "00020101FAKE" + order_id, "2026", {})

    async def transaction_detail(self, order_id, amount):
        status = "completed" if self.completed else "pending"
        return TransactionStatus(order_id, amount, status, "qris", "2026-09-04", {})


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
    if os.path.exists("bot.db"):
        os.remove("bot.db")
    await db.connect()
    pid = await db.add_product("t1", "Test Netflix", 25000, "account")
    await db.add_stock(pid, ["a@x.com:1", "b@x.com:2"])

    # 1) order + QRIS
    order, err = await service.start_order(FakePakasir(), 999, "buyer", pid)
    assert err is None, err
    assert order["fee"] == 250 and order["total_payment"] == 25250
    assert await db.available_stock(pid) == 1, "harus 1 stok direservasi"
    print("OK  order", order["order_id"], "amount", order["amount"])

    # 2) pembayaran masuk -> kirim produk
    app = FakeApp()
    assert await service.settle_if_paid(app, FakePakasir(), order["order_id"]) == "delivered"
    o = await db.get_order(order["order_id"])
    assert o["status"] == "delivered"
    buyer_msgs = [t for c, t in app.bot.sent if c == 999]
    assert len(buyer_msgs) == 1 and "a@x.com:1" in buyer_msgs[0]
    print("OK  produk terkirim:", repr(buyer_msgs[0][:50]))

    # 3) idempoten: verifikasi ulang tidak mengirim lagi
    assert await service.settle_if_paid(app, FakePakasir(), order["order_id"]) == "delivered"
    assert len([t for c, t in app.bot.sent if c == 999]) == 1, "produk terkirim 2x!"
    print("OK  idempoten")

    # 4) order kedua, tidak dibayar -> expire melepas stok
    order2, err = await service.start_order(FakePakasir(completed=False), 888, "buyer2", pid)
    assert err is None
    assert await db.available_stock(pid) == 0, "stok terakhir direservasi"
    d = await db.connect()
    await d.execute("UPDATE orders SET expired_at=1 WHERE order_id=?", (order2["order_id"],))
    await d.commit()
    stale = await db.expire_stale_orders()
    assert len(stale) == 1
    assert await db.available_stock(pid) == 1, "stok harus kembali setelah expire"
    print("OK  expire melepas stok")

    # 5) stok habis
    await db.reserve_stock(pid, 1, "manual")
    order3, err = await service.start_order(FakePakasir(), 777, "buyer3", pid)
    assert order3 is None and "habis" in err.lower()
    print("OK  tolak saat stok habis")

    print("\nSEMUA SMOKE TEST LULUS")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        asyncio.run(db.close())
        for f in ("bot.db", "bot.db-wal", "bot.db-shm"):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
