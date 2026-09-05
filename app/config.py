"""Konfigurasi dibaca dari environment / file .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Config:
    bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    admin_ids: set[int] = field(
        default_factory=lambda: {
            int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x
        }
    )

    pakasir_base_url: str = os.getenv("PAKASIR_BASE_URL", "https://app.pakasir.com").rstrip("/")
    pakasir_project: str = os.getenv("PAKASIR_PROJECT", "")
    pakasir_api_key: str = os.getenv("PAKASIR_API_KEY", "")

    webhook_host: str = os.getenv("WEBHOOK_HOST", "0.0.0.0")
    webhook_port: int = _int("WEBHOOK_PORT", 8080)
    webhook_path: str = os.getenv("PAKASIR_WEBHOOK_PATH", "/pakasir/webhook")
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

    order_expiry_minutes: int = _int("ORDER_EXPIRY_MINUTES", 15)
    db_path: str = os.getenv("DB_PATH", "bot.db")

    # Channel notifikasi penjualan (opsional). Isi dengan @username channel
    # atau chat id (mis. -1001234567890). Bot harus jadi admin di channel itu.
    sales_channel_id: str = os.getenv("SALES_CHANNEL_ID", "").strip()

    # Wajib join channel ini sebelum bisa pakai bot (opsional). Isi @username
    # (channel publik) ATAU id numerik seperti -1001234567890 (channel privat).
    # Bot harus jadi admin di channel itu.
    required_channel: str = os.getenv("REQUIRED_CHANNEL", "").strip()

    # Link yang dibuka tombol "Join Channel". Wajib diisi kalau REQUIRED_CHANNEL
    # berupa id numerik (channel privat) — pakai link invite-nya, mis.
    # https://t.me/+xxxxxxxxxxxx. Boleh dikosongkan kalau REQUIRED_CHANNEL sudah
    # berupa @username channel publik (link dibuat otomatis).
    required_channel_url_override: str = os.getenv("REQUIRED_CHANNEL_URL", "").strip()

    def validate(self) -> None:
        missing = [
            key
            for key, val in {
                "TELEGRAM_BOT_TOKEN": self.bot_token,
                "PAKASIR_PROJECT": self.pakasir_project,
                "PAKASIR_API_KEY": self.pakasir_api_key,
            }.items()
            if not val
        ]
        if missing:
            raise SystemExit(f"Env belum lengkap: {', '.join(missing)} (lihat .env.example)")
        if not self.admin_ids:
            raise SystemExit("ADMIN_IDS wajib diisi minimal satu ID.")

    @property
    def required_channel_url(self) -> str:
        """Link t.me untuk tombol 'Join Channel'."""
        if self.required_channel_url_override:
            return self.required_channel_url_override
        c = self.required_channel
        return f"https://t.me/{c.lstrip('@')}" if c.startswith("@") else ""

    def hosted_pay_url(self, order_id: str, amount: int) -> str:
        return (
            f"{self.pakasir_base_url}/pay/{self.pakasir_project}/{amount}"
            f"?order_id={order_id}&qris_only=1"
        )


cfg = Config()
