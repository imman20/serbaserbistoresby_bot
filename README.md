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

### Panel Admin — `/admin`

Cara termudah mengelola toko: kirim `/admin`, tap produk, lalu tap tombol yang
diinginkan — **tidak perlu hafal perintah sama sekali**:

- 📋 Lihat/Hapus Stok — daftar item stok, tap salah satu untuk hapus (ada konfirmasi)
- ➕ Tambah Stok — bot minta kamu tempel data stok berikutnya
- ✏️ Edit Deskripsi / ✏️ Edit Cara Pakai — bot minta teks baru
- 🟢/🔴 Aktifkan / Nonaktifkan — satu tap
- 🗑 Hapus Produk — ada konfirmasi; otomatis jadi *nonaktif saja* bila produk itu
  pernah punya order (riwayat transaksi tidak boleh hilang)

Ketik `/batal` kapan saja untuk membatalkan aksi yang sedang berjalan (mis. batal isi deskripsi).

### Perintah cepat (opsional — semua ini juga bisa lewat panel di atas)

| Perintah | Fungsi |
|---|---|
| `/tambahproduk` | wizard tanya-jawab: kode → nama → grup/varian → harga → jenis (akun/voucher/file) → link/deskripsi → cara pakai |
| `/addstok <kode>` | tambah stok — lihat 2 format di bawah |
| `/lihatstok <kode>` | lihat daftar stok tersedia (bernomor) |
| `/hapusstok <kode> <nomor>` | hapus 1 item stok berdasarkan nomor dari `/lihatstok` |
| `/editdeskripsi <kode>` | ubah deskripsi (tampil di katalog sebelum beli) |
| `/setcarapakai <kode>` | atur/ubah instruksi "cara pakai" (dikirim setelah bayar) |
| `/produkadmin` | daftar semua produk + status + info grup |
| `/aktif <kode>` · `/nonaktif <kode>` | tampilkan / sembunyikan produk dari katalog |
| `/hapusproduk <kode>` | hapus (otomatis jadi *nonaktif* bila sudah pernah ada order) |
| `/stok` · `/orders` | ringkasan stok · 15 order terakhir |

### Produk dengan pilihan durasi/varian (mis. Netflix 1/3/7 Hari)

Saat `/tambahproduk` ditanya soal grup, jawab dengan `nama grup | label varian`
(pakai **nama grup yang sama persis** untuk tiap varian), misal:

```
Netflix Sharing | 1 Hari
Netflix Sharing | 7 Hari
Netflix Sharing | 30 Hari
```

Ketiganya otomatis digabung jadi **satu tombol "Netflix Sharing"** di katalog;
pembeli tap tombol itu dulu, baru muncul pilihan durasinya. Produk tanpa grup
(ketik `-` saat ditanya) tetap tampil langsung seperti biasa.

### Format `/addstok` — dua pilihan

**Sederhana** (satu baris = satu item):
```
/addstok netflix1hari
email1@mail.com:passA
email2@mail.com:passB
KODE-VOUCHER-123
```

**Rapi / multi-baris per item** (pisahkan tiap item dengan baris `---` **atau** `===`),
cocok untuk format seperti "Email / Password / akses 2FA":
```
/addstok netflix1hari
Email : akun1@mail.com
Password : pass123
akses untuk 2fa : 2fa.live/xxx
---
Email : akun2@mail.com
Password : pass456
akses untuk 2fa : 2fa.live/yyy
```
Tiap blok akan dikirim ke pembeli persis seperti itu (rapi, gampang di-copy).

Untuk produk jenis **file** dengan satu link untuk semua pembeli, link-nya
dimasukkan saat `/tambahproduk` — tidak perlu `/addstok`.

### Cara pakai (terpisah dari deskripsi)

Deskripsi tampil di **katalog** (sebelum beli); cara pakai dikirim ke pembeli
**setelah bayar**, bersama detail akun. Atur lewat wizard, atau kapan saja:
```
/setcarapakai netflix1hari
1. Login pakai email & password di atas
2. Jangan ganti profil orang lain
3. Kalau logout sendiri, chat admin
```
Kirim `/setcarapakai <kode> -` untuk mengosongkan.

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
- **Migrasi otomatis.** Kolom baru (grup/varian, cara pakai, dst) ditambahkan otomatis ke
  `bot.db` yang sudah ada saat bot dijalankan (`db._ensure_columns`) — data lama tidak hilang,
  tidak perlu hapus/buat ulang database saat update kode.
- **Skala.** SQLite cukup untuk ratusan order/hari. Kalau lebih, migrasi ke Postgres.
