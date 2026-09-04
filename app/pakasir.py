"""Klien Pakasir Payment Gateway.

Docs: https://pakasir.com/p/docs
  POST {base}/api/transactioncreate/qris   -> buat QRIS dinamis
  GET  {base}/api/transactiondetail        -> cek status transaksi
  Webhook: Pakasir POST JSON ke URL kamu saat status 'completed'

Catatan keamanan: Pakasir TIDAK mengirim signature pada webhook, jadi payload
webhook WAJIB diverifikasi ulang lewat transactiondetail sebelum dipercaya.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import cfg


class PakasirError(RuntimeError):
    pass


@dataclass
class CreatedPayment:
    order_id: str
    amount: int
    fee: int
    total_payment: int
    payment_method: str
    payment_number: str
    expired_at: str
    raw: dict


@dataclass
class TransactionStatus:
    order_id: str
    amount: int
    status: str            # 'pending' | 'completed' | 'expired' | ...
    payment_method: str
    completed_at: str | None
    raw: dict

    @property
    def is_completed(self) -> bool:
        return self.status.lower() == "completed"


class PakasirClient:
    def __init__(self, timeout: float = 20.0) -> None:
        self._client = httpx.AsyncClient(base_url=cfg.pakasir_base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_qris(self, order_id: str, amount: int) -> CreatedPayment:
        payload = {
            "project": cfg.pakasir_project,
            "order_id": order_id,
            "amount": amount,
            "api_key": cfg.pakasir_api_key,
        }
        try:
            r = await self._client.post("/api/transactioncreate/qris", json=payload)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError as e:
            raise PakasirError(f"create_qris gagal: {e}") from e

        p = data.get("payment", data)
        return CreatedPayment(
            order_id=str(p.get("order_id", order_id)),
            amount=int(p.get("amount", amount)),
            fee=int(p.get("fee", 0)),
            total_payment=int(p.get("total_payment", p.get("amount", amount))),
            payment_method=str(p.get("payment_method", "qris")),
            payment_number=str(p.get("payment_number", "")),
            expired_at=str(p.get("expired_at", "")),
            raw=data,
        )

    async def transaction_detail(self, order_id: str, amount: int) -> TransactionStatus:
        params = {
            "project": cfg.pakasir_project,
            "order_id": order_id,
            "amount": amount,
            "api_key": cfg.pakasir_api_key,
        }
        try:
            r = await self._client.get("/api/transactiondetail", params=params)
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError as e:
            raise PakasirError(f"transaction_detail gagal: {e}") from e

        t = data.get("transaction", data)
        return TransactionStatus(
            order_id=str(t.get("order_id", order_id)),
            amount=int(t.get("amount", amount)),
            status=str(t.get("status", "unknown")),
            payment_method=str(t.get("payment_method", "")),
            completed_at=t.get("completed_at"),
            raw=data,
        )
