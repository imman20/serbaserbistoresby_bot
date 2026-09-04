"""Isi contoh produk + stok. Jalankan sekali: python seed.py"""
import asyncio

from app import db


async def main() -> None:
    await db.connect()

    if not await db.get_product_by_code("netflix1p"):
        pid = await db.add_product(
            code="netflix1p", name="Netflix Sharing 1 Bulan", price=25000,
            delivery_type="account", description="Profil pribadi, garansi 30 hari.",
        )
        await db.add_stock(pid, ["akun1@mail.com:pass123", "akun2@mail.com:pass456"])

    if not await db.get_product_by_code("canva-edu"):
        pid = await db.add_product(
            code="canva-edu", name="Canva Pro (Voucher Invite)", price=15000,
            delivery_type="voucher", description="Link invite, aktif 1 tahun.",
        )
        await db.add_stock(pid, ["https://canva.com/brand/join?token=AAA", "https://canva.com/brand/join?token=BBB"])

    if not await db.get_product_by_code("ebook-cuan"):
        await db.add_product(
            code="ebook-cuan", name="E-book Panduan Jualan Digital", price=10000,
            delivery_type="file", file_payload="https://drive.google.com/file/d/CONTOH/view",
            description="Link Google Drive, akses selamanya.",
        )

    print("Seed selesai.")
    for p in await db.list_products(only_active=False):
        print(f"  {p['code']:12} {p['name']:35} Rp{p['price']:,}  stok={await db.available_stock(p['id'])}")
    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
