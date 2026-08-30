"""
AbacatePay gateway - lightweight Brazilian Pix API for digital shops.
Docs: https://docs.abacatepay.com
"""
from __future__ import annotations

import json

import aiohttp

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus

API_BASE = "https://api.abacatepay.com/v1"


class AbacatePayGateway(BaseGateway):
    key = "abacatepay"
    display_name = "AbacatePay (Pix)"
    supported_currencies = ("BRL",)
    homepage = "https://abacatepay.com"
    credential_fields = (
        GatewayField("api_key", "API Key", secret=True),
    )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials.get('api_key', '')}",
            "Content-Type": "application/json",
        }

    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        payload = {
            "amount": int(round(amount * 100)),
            "expiresIn": 1800,
            "description": description[:250],
            "externalId": str(order_id),
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/pixQrCode/create", headers=self._headers(),
                data=json.dumps(payload), timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"AbacatePay error: {data}")

        item = data.get("data", data)
        return ChargeResult(
            external_id=str(item.get("id")),
            qr_code_text=item.get("brCode"),
            qr_code_base64=item.get("brCodeBase64"),
            instructions="Scan the Pix QR code or copy the Pix code to pay.",
            raw=item,
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/pixQrCode/check?id={external_id}", headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return PaymentStatus.ERROR
        item = data.get("data", data)
        return {
            "PAID": PaymentStatus.PAID,
            "PENDING": PaymentStatus.PENDING,
            "EXPIRED": PaymentStatus.EXPIRED,
            "CANCELLED": PaymentStatus.CANCELLED,
        }.get(item.get("status", ""), PaymentStatus.PENDING)
