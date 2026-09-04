# Bot Telegram — Produk Digital + Pakasir QRIS

Bot Telegram untuk jualan produk digital otomatis. Pembeli pilih produk → bot
buatkan **QRIS dinamis via Pakasir** → begitu pembayaran terverifikasi, produk
(akun / voucher / link file) dikirim otomatis ke chat.

## Alur

```
/produk → pilih → [Beli]
   └─ bot reserve 1 stok, buat order, POST /api/transactioncreate/qris ke Pakasir
   └─ kirim gambar QRIS + tombol "Buka halaman bayar" & "Cek status"
Pembeli bayar
   └─ Pakasir POST webhook → server verifikasi ulang via /api/transactiondetail
   └─ order = paid → stok = sold → produk dikirim ke pembeli + notif admin
Tidak dibayar dalam 15 menit → order expired, stok dilepas otomatis
```

## Struktur

| File | Isi |
|---|---|
| `main.py` | menjalankan bot (polling) + server webhook aiohttp bersama |
| `app/config.py` | baca `.env` |
| `app/db.py` | SQLite (products, stock_items, orders, users) |
| `app/pakasir.py` | klien API Pakasir (create QRIS, cek status) |
| `app/service.py` | buat order, verifikasi, kirim produk (idempoten) |
| `app/bot.py` | handler Telegram + perintah admin |
| `app/webhook.py` | endpoint `POST /pakasir/webhook` |
| `seed.py` | contoh produk & stok |
| `deploy/` | systemd unit + contoh nginx |

## Setup lokal

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # lalu isi TOKEN, ADMIN_IDS, PAKASIR_*
python seed.py                   # opsional: produk contoh
python main.py
```

Ambil `TELEGRAM_BOT_TOKEN` dari [@BotFather](https://t.me/BotFather), `ADMIN_IDS`
dari [@userinfobot](https://t.me/userinfobot). `PAKASIR_PROJECT` & `PAKASIR_API_KEY`
dari dashboard Pakasir → Project → Settings.

## Deploy di VPS

1. `git clone` ke `/opt/telegram-bot-pakasir`, buat venv, `pip install -r requirements.txt`, isi `.env`.
2. Pasang domain + TLS (`certbot`), pakai `deploy/nginx.conf.example`.
3. Di **dashboard Pakasir → Webhook**, isi: `https://domain-kamu.com/pakasir/webhook`
4. `sudo cp deploy/bot-pakasir.service /etc/systemd/system/` → sesuaikan `User`/path →
   `sudo systemctl enable --now bot-pakasir` → `journalctl -u bot-pakasir -f`.
5. Tes: `curl https://domain-kamu.com/health` → `{"ok": true}`.

## Perintah

**User:** `/start` · `/produk` · `/order <ID>`

**Admin (semua lewat Telegram, tanpa sentuh server):**

| Perintah | Fungsi |
|---|---|
| `/tambahproduk` | wizard tanya-jawab: kode → nama → harga → jenis (akun/voucher/file) → link/deskripsi |
| `/addstok <kode>` | tambah stok — tempel data, **satu baris satu item** |
| `/produkadmin` | daftar semua produk + status |
| `/aktif <kode>` · `/nonaktif <kode>` | tampilkan / sembunyikan produk dari katalog |
| `/hapusproduk <kode>` | hapus (otomatis jadi *nonaktif* bila sudah pernah ada order) |
| `/stok` · `/orders` | ringkasan stok · 15 order terakhir |

Contoh isi stok:

```
/addstok netflix1p
email1@mail.com:passA
email2@mail.com:passB
KODE-VOUCHER-123
```

Untuk produk jenis **file** dengan satu link untuk semua pembeli, link-nya
dimasukkan saat `/tambahproduk` — tidak perlu `/addstok`.

## Catatan penting

- **Verifikasi ganda.** Pakasir tidak menandatangani webhook, jadi tiap notifikasi
  dicek ulang ke `transactiondetail` sebelum produk dikirim. Jangan hapus langkah ini.
- **Idempoten.** `deliver_order()` aman dipanggil berkali-kali (webhook + tombol "Cek status"
  bisa datang bersamaan) — produk hanya dikirim sekali.
- **Amount.** Yang dicocokkan adalah `amount` (harga × qty), bukan `total_payment`.
  Pakasir menambah fee di atasnya; pembeli membayar `total_payment`.
- **Cadangan.** Kalau webhook gagal masuk, pembeli bisa tekan "Cek status pembayaran"
  atau kirim `/order <ID>` untuk memicu verifikasi manual.
- **Backup** file `bot.db` secara berkala (berisi stok & riwayat order).
- **Skala.** SQLite cukup untuk ratusan order/hari. Kalau lebih, migrasi ke Postgres.
