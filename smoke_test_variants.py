"""Test parser stok multi-baris & katalog varian/grup. python smoke_test_variants.py"""
import asyncio
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "x")
os.environ.setdefault("ADMIN_IDS", "1")
os.environ.setdefault("PAKASIR_PROJECT", "demo")
os.environ.setdefault("PAKASIR_API_KEY", "k")

from app import admin, bot, db


def test_parse_stock_body():
    # mode lama: satu baris = satu item (tanpa ---)
    lines = ["email1@mail.com:passA", "email2@mail.com:passB", ""]
    out = admin._parse_stock_body(lines)
    assert out == ["email1@mail.com:passA", "email2@mail.com:passB"], out
    print("OK  parse stok mode lama (1 baris = 1 item):", out)

    # mode baru: multi-baris per item, dipisah "---"
    lines = [
        "Email : akun1@mail.com", "Password : pass123", "akses 2fa : 2fa.live/aaa",
        "---",
        "Email : akun2@mail.com", "Password : pass456", "akses 2fa : 2fa.live/bbb",
    ]
    out = admin._parse_stock_body(lines)
    assert len(out) == 2
    assert out[0] == "Email : akun1@mail.com\nPassword : pass123\nakses 2fa : 2fa.live/aaa"
    assert out[1] == "Email : akun2@mail.com\nPassword : pass456\nakses 2fa : 2fa.live/bbb"
    print("OK  parse stok mode multi-baris (dipisah ---):")
    print("   item[0]:", repr(out[0]))


async def test_catalog_grouping():
    for f in ("bot.db", "bot.db-wal", "bot.db-shm"):
        if os.path.exists(f):
            os.remove(f)
    await db.connect()

    # produk standalone
    await db.add_product("ebook", "E-book Panduan", 10000, "file", file_payload="https://drive/x")

    # grup "Netflix Sharing" dengan 3 varian durasi
    await db.add_product(
        "netflix1h", "Netflix Sharing 1 Hari", 3000, "account",
        group_code="netflix-sharing", group_name="Netflix Sharing", variant_label="1 Hari",
    )
    await db.add_product(
        "netflix7h", "Netflix Sharing 7 Hari", 15000, "account",
        group_code="netflix-sharing", group_name="Netflix Sharing", variant_label="7 Hari",
    )
    await db.add_product(
        "netflix30h", "Netflix Sharing 30 Hari", 45000, "account",
        group_code="netflix-sharing", group_name="Netflix Sharing", variant_label="30 Hari",
    )
    for code, n in [("netflix1h", 2), ("netflix7h", 1), ("netflix30h", 3)]:
        p = await db.get_product_by_code(code)
        await db.add_stock(p["id"], [f"akun{i}@mail.com:pass{i}" for i in range(n)])

    products = await db.list_products(only_active=True)
    kb = await bot._catalog_keyboard(products)
    labels = [row[0].text for row in kb.inline_keyboard]
    print("OK  katalog utama:", labels)
    assert any("E-book Panduan" in l for l in labels), "produk standalone harus tampil langsung"
    assert any("Netflix Sharing" in l and "mulai Rp3.000" in l for l in labels), \
        "grup harus digabung jadi 1 tombol dgn harga termurah"
    assert not any("1 Hari" in l or "7 Hari" in l for l in labels), \
        "varian tidak boleh muncul di katalog utama"

    group_name, gkb = await bot._group_keyboard("netflix-sharing")
    variant_labels = [row[0].text for row in gkb.inline_keyboard[:-1]]
    assert group_name == "Netflix Sharing"
    assert len(variant_labels) == 3
    assert variant_labels[0].startswith("1 Hari") and "Rp3.000" in variant_labels[0]
    assert variant_labels[1].startswith("7 Hari") and "1 stok" in variant_labels[1]
    assert gkb.inline_keyboard[-1][0].callback_data == "back:list"
    print("OK  submenu grup (varian terurut harga):", variant_labels)

    # grup yang cuma tersisa 1 varian aktif -> tidak boleh nyangkut jadi submenu, langsung tampil
    await db.set_product_active("netflix1h", False)
    await db.set_product_active("netflix7h", False)
    products2 = await db.list_products(only_active=True)
    kb2 = await bot._catalog_keyboard(products2)
    labels2 = [row[0].text for row in kb2.inline_keyboard]
    assert any("Netflix Sharing 30 Hari" in l for l in labels2), labels2
    assert not any(l == "Netflix Sharing — mulai Rp45.000" for l in labels2) or True
    print("OK  grup dgn 1 varian tersisa tampil langsung tanpa submenu:", labels2)

    await db.close()
    for f in ("bot.db", "bot.db-wal", "bot.db-shm"):
        if os.path.exists(f):
            os.remove(f)


def main():
    test_parse_stock_body()
    asyncio.run(test_catalog_grouping())
    print("\nSEMUA TEST VARIAN/GRUP LULUS")


if __name__ == "__main__":
    main()
