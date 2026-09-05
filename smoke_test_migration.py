"""Pastikan database LAMA (skema sebelum fitur grup/cara-pakai) ter-upgrade
otomatis tanpa kehilangan data saat dibuka dengan kode baru.
python smoke_test_migration.py
"""
import asyncio
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("PAKASIR_PROJECT", "demo")
os.environ.setdefault("PAKASIR_API_KEY", "k")

DB_FILE = "bot_migrate_test.db"

OLD_SCHEMA = """
CREATE TABLE products (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT UNIQUE NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT DEFAULT '',
    price         INTEGER NOT NULL,
    delivery_type TEXT NOT NULL,
    file_payload  TEXT DEFAULT '',
    active        INTEGER NOT NULL DEFAULT 1
);
INSERT INTO products(code, name, description, price, delivery_type, file_payload, active)
VALUES ('lama1', 'Produk Lama', 'deskripsi lama', 25000, 'account', '', 1);
"""


def main():
    for f in (DB_FILE, DB_FILE + "-wal", DB_FILE + "-shm"):
        if os.path.exists(f):
            os.remove(f)
    conn = sqlite3.connect(DB_FILE)
    conn.executescript(OLD_SCHEMA)
    conn.commit()
    conn.close()
    print("OK  database lama (tanpa kolom baru) dibuat")

    os.environ["DB_PATH"] = DB_FILE
    # config.py membaca DB_PATH sekali saat import -> jalankan di subprocess terpisah
    # supaya nilainya kepakai bersih.
    import subprocess

    code = (
        "import asyncio, os\n"
        "from app import db\n"
        "async def main():\n"
        "    await db.connect()\n"
        "    p = await db.get_product_by_code('lama1')\n"
        "    assert p['name'] == 'Produk Lama'\n"
        "    assert p['group_code'] == ''\n"
        "    assert p['usage_note'] == ''\n"
        "    pid = await db.add_product('baru1', 'Produk Baru', 10000, 'voucher',\n"
        "        group_code='grp', group_name='Grup', variant_label='7 Hari', usage_note='pakai baik')\n"
        "    p2 = await db.get_product(pid)\n"
        "    assert p2['group_name'] == 'Grup' and p2['usage_note'] == 'pakai baik'\n"
        "    await db.close()\n"
        "    print('OK  kolom baru ter-migrasi & data lama utuh, produk baru bisa dibuat')\n"
        "asyncio.run(main())\n"
    )
    env = dict(os.environ)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
    print(r.stdout, end="")
    if r.returncode != 0:
        print(r.stderr)
        raise SystemExit(1)

    for f in (DB_FILE, DB_FILE + "-wal", DB_FILE + "-shm"):
        if os.path.exists(f):
            os.remove(f)
    print("\nSEMUA TEST MIGRASI LULUS")


if __name__ == "__main__":
    main()
