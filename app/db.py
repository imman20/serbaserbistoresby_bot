"""Lapisan database (SQLite via aiosqlite).

Skema:
  products     — katalog produk digital
  stock_items  — stok per produk (akun / voucher / link), sekali pakai
  orders       — transaksi
  users        — pembeli yang pernah /start
"""
from __future__ import annotations

import time
from typing import Any, Optional

import aiosqlite

from .config import cfg

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT DEFAULT '',
    price         INTEGER NOT NULL,                       -- rupiah, tanpa fee
    delivery_type TEXT NOT NULL CHECK (delivery_type IN ('account','voucher','file')),
    file_payload  TEXT DEFAULT '',                        -- dipakai bila delivery_type='file' (link/teks, stok tak terbatas)
    active        INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS stock_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES products(id),
    payload    TEXT NOT NULL,                             -- "email:password" / "KODE-VOUCHER" / "https://..."
    status     TEXT NOT NULL DEFAULT 'available'
               CHECK (status IN ('available','reserved','sold')),
    order_id   TEXT,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id        TEXT PRIMARY KEY,
    user_id         INTEGER NOT NULL,
    username        TEXT DEFAULT '',
    product_id      INTEGER NOT NULL REFERENCES products(id),
    qty             INTEGER NOT NULL DEFAULT 1,
    amount          INTEGER NOT NULL,                     -- price * qty (nilai yang dicocokkan dgn webhook)
    fee             INTEGER NOT NULL DEFAULT 0,
    total_payment   INTEGER NOT NULL DEFAULT 0,           -- yang dibayar pembeli (amount + fee)
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','paid','delivered','expired','failed')),
    payment_number  TEXT DEFAULT '',
    expired_at      INTEGER,
    created_at      INTEGER NOT NULL,
    paid_at         INTEGER,
    delivered_at    INTEGER
);

CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT DEFAULT '',
    first_seen INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stock_product_status ON stock_items(product_id, status);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
"""

_conn: Optional[aiosqlite.Connection] = None


async def connect() -> aiosqlite.Connection:
    global _conn
    if _conn is None:
        _conn = await aiosqlite.connect(cfg.db_path)
        _conn.row_factory = aiosqlite.Row
        await _conn.execute("PRAGMA journal_mode=WAL;")
        await _conn.execute("PRAGMA foreign_keys=ON;")
        await _conn.executescript(SCHEMA)
        await _conn.commit()
    return _conn


async def close() -> None:
    global _conn
    if _conn is not None:
        await _conn.close()
        _conn = None


def now() -> int:
    return int(time.time())


# ── users ────────────────────────────────────────────────
async def upsert_user(user_id: int, username: str) -> None:
    db = await connect()
    await db.execute(
        "INSERT INTO users(user_id, username, first_seen) VALUES(?,?,?) "
        "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username",
        (user_id, username or "", now()),
    )
    await db.commit()


# ── products ─────────────────────────────────────────────
async def list_products(only_active: bool = True) -> list[aiosqlite.Row]:
    db = await connect()
    q = "SELECT * FROM products"
    if only_active:
        q += " WHERE active=1"
    q += " ORDER BY name"
    async with db.execute(q) as cur:
        return await cur.fetchall()


async def get_product(product_id: int) -> Optional[aiosqlite.Row]:
    db = await connect()
    async with db.execute("SELECT * FROM products WHERE id=?", (product_id,)) as cur:
        return await cur.fetchone()


async def get_product_by_code(code: str) -> Optional[aiosqlite.Row]:
    db = await connect()
    async with db.execute("SELECT * FROM products WHERE code=?", (code,)) as cur:
        return await cur.fetchone()


async def add_product(
    code: str, name: str, price: int, delivery_type: str,
    description: str = "", file_payload: str = "",
) -> int:
    db = await connect()
    cur = await db.execute(
        "INSERT INTO products(code,name,description,price,delivery_type,file_payload) "
        "VALUES(?,?,?,?,?,?)",
        (code, name, description, price, delivery_type, file_payload),
    )
    await db.commit()
    return cur.lastrowid


async def set_product_active(code: str, active: bool) -> bool:
    db = await connect()
    cur = await db.execute(
        "UPDATE products SET active=? WHERE code=?", (1 if active else 0, code)
    )
    await db.commit()
    return cur.rowcount == 1


async def delete_product(code: str) -> str:
    """Hapus produk. Bila ada stok terpakai / order, cukup nonaktifkan."""
    db = await connect()
    prod = await get_product_by_code(code)
    if prod is None:
        return "not_found"
    async with db.execute(
        "SELECT COUNT(*) FROM orders WHERE product_id=?", (prod["id"],)
    ) as cur:
        (n_orders,) = await cur.fetchone()
    if n_orders:
        await db.execute("UPDATE products SET active=0 WHERE id=?", (prod["id"],))
        await db.commit()
        return "deactivated"
    await db.execute("DELETE FROM stock_items WHERE product_id=?", (prod["id"],))
    await db.execute("DELETE FROM products WHERE id=?", (prod["id"],))
    await db.commit()
    return "deleted"


async def available_stock(product_id: int) -> int:
    db = await connect()
    async with db.execute(
        "SELECT COUNT(*) FROM stock_items WHERE product_id=? AND status='available'",
        (product_id,),
    ) as cur:
        (n,) = await cur.fetchone()
    return int(n)


async def add_stock(product_id: int, payloads: list[str]) -> int:
    db = await connect()
    ts = now()
    await db.executemany(
        "INSERT INTO stock_items(product_id,payload,status,created_at) VALUES(?,?,'available',?)",
        [(product_id, p, ts) for p in payloads],
    )
    await db.commit()
    return len(payloads)


# ── orders ───────────────────────────────────────────────
async def create_order(
    order_id: str, user_id: int, username: str, product_id: int,
    qty: int, amount: int, expired_at: int,
) -> None:
    db = await connect()
    await db.execute(
        "INSERT INTO orders(order_id,user_id,username,product_id,qty,amount,expired_at,created_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (order_id, user_id, username or "", product_id, qty, amount, expired_at, now()),
    )
    await db.commit()


async def get_order(order_id: str) -> Optional[aiosqlite.Row]:
    db = await connect()
    async with db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)) as cur:
        return await cur.fetchone()


async def set_order_payment(order_id: str, payment_number: str, fee: int, total_payment: int) -> None:
    db = await connect()
    await db.execute(
        "UPDATE orders SET payment_number=?, fee=?, total_payment=? WHERE order_id=?",
        (payment_number, fee, total_payment, order_id),
    )
    await db.commit()


async def reserve_stock(product_id: int, qty: int, order_id: str) -> list[str]:
    """Kunci `qty` item stok untuk sebuah order. Kembalikan payload-nya.

    Mengembalikan list kosong bila stok tidak cukup (tidak ada yang di-reserve).
    """
    db = await connect()
    await db.execute("BEGIN IMMEDIATE")
    try:
        async with db.execute(
            "SELECT id, payload FROM stock_items "
            "WHERE product_id=? AND status='available' ORDER BY id LIMIT ?",
            (product_id, qty),
        ) as cur:
            rows = await cur.fetchall()
        if len(rows) < qty:
            await db.execute("ROLLBACK")
            return []
        ids = [r["id"] for r in rows]
        await db.executemany(
            "UPDATE stock_items SET status='reserved', order_id=? WHERE id=?",
            [(order_id, i) for i in ids],
        )
        await db.execute("COMMIT")
        return [r["payload"] for r in rows]
    except Exception:
        await db.execute("ROLLBACK")
        raise


async def mark_order_paid(order_id: str) -> bool:
    """Transisi pending -> paid secara atomik. True bila kita yang mengubahnya."""
    db = await connect()
    cur = await db.execute(
        "UPDATE orders SET status='paid', paid_at=? WHERE order_id=? AND status='pending'",
        (now(), order_id),
    )
    await db.commit()
    return cur.rowcount == 1


async def mark_order_delivered(order_id: str) -> None:
    db = await connect()
    await db.execute(
        "UPDATE orders SET status='delivered', delivered_at=? WHERE order_id=?",
        (now(), order_id),
    )
    await db.execute(
        "UPDATE stock_items SET status='sold' WHERE order_id=? AND status='reserved'",
        (order_id,),
    )
    await db.commit()


async def fail_order(order_id: str) -> None:
    """Tandai order 'failed' & lepas stok reserved (dipakai bila create QRIS gagal)."""
    db = await connect()
    await db.execute(
        "UPDATE orders SET status='failed' WHERE order_id=? AND status='pending'", (order_id,)
    )
    await db.execute(
        "UPDATE stock_items SET status='available', order_id=NULL "
        "WHERE order_id=? AND status='reserved'",
        (order_id,),
    )
    await db.commit()


async def expire_stale_orders() -> list[aiosqlite.Row]:
    """Tandai order pending yang lewat waktu jadi 'expired' & lepas stok reserved."""
    db = await connect()
    ts = now()
    async with db.execute(
        "SELECT * FROM orders WHERE status='pending' AND expired_at IS NOT NULL AND expired_at < ?",
        (ts,),
    ) as cur:
        stale = await cur.fetchall()
    if not stale:
        return []
    ids = [o["order_id"] for o in stale]
    qmarks = ",".join("?" * len(ids))
    await db.execute(f"UPDATE orders SET status='expired' WHERE order_id IN ({qmarks})", ids)
    await db.execute(
        f"UPDATE stock_items SET status='available', order_id=NULL "
        f"WHERE order_id IN ({qmarks}) AND status='reserved'",
        ids,
    )
    await db.commit()
    return stale


async def recent_orders(limit: int = 15) -> list[aiosqlite.Row]:
    db = await connect()
    async with db.execute(
        "SELECT o.*, p.name AS product_name FROM orders o "
        "JOIN products p ON p.id=o.product_id ORDER BY o.created_at DESC LIMIT ?",
        (limit,),
    ) as cur:
        return await cur.fetchall()
