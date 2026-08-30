"""
PushinPay gateway - a lightweight Pix-only payment processor popular with
Brazilian Discord shops. REST docs: https://pushinpay.com.br
"""
from __future__ import annotations

import json

import aiohttp

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus

API_BASE = "https://api.pushinpay.com.br/api"


class PushinPayGateway(BaseGateway):
    key = "pushinpay"
    display_name = "PushinPay (Pix)"
    supported_currencies = ("BRL",)
    homepage = "https://pushinpay.com.br"
    credential_fields = (
        GatewayField("api_token", "API Token", secret=True),
    )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials.get('api_token', '')}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        payload = {
            "value": int(round(amount * 100)),  # cents
            "webhook_url": None,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/pix/cashIn",
                headers=self._headers(),
                data=json.dumps(payload),
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"PushinPay error: {data}")

        return ChargeResult(
            external_id=str(data.get("id")),
            qr_code_text=data.get("qr_code"),
            qr_code_base64=data.get("qr_code_base64"),
            instructions="Scan the Pix QR code or copy the Pix code to pay.",
            raw=data,
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/transactions/{external_id}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return PaymentStatus.ERROR
        status = str(data.get("status", "")).lower()
        return {
            "paid": PaymentStatus.PAID,
            "created": PaymentStatus.PENDING,
            "expired": PaymentStatus.EXPIRED,
        }.get(status, PaymentStatus.PENDING)
