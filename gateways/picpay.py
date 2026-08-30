"""
PicPay gateway - Brazilian digital wallet checkout API.
Docs: https://ecommerce.picpay.com/doc/
"""
from __future__ import annotations

import json

import aiohttp

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus

API_BASE = "https://appws.picpay.com/ecommerce/public"


class PicPayGateway(BaseGateway):
    key = "picpay"
    display_name = "PicPay"
    supported_currencies = ("BRL",)
    homepage = "https://ecommerce.picpay.com"
    credential_fields = (
        GatewayField("x_picpay_token", "Seller Token (x-picpay-token)", secret=True),
    )

    def _headers(self) -> dict:
        return {
            "x-picpay-token": self.credentials.get("x_picpay_token", ""),
            "Content-Type": "application/json",
        }

    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        payload = {
            "referenceId": f"davix-{order_id}",
            "callbackUrl": "https://example.com/picpay-callback",  # overridden by admin via webhook setup
            "value": round(float(amount), 2),
            "expiresAt": None,
            "buyer": {
                "firstName": "Discord",
                "lastName": f"Buyer{buyer_id}",
                "document": "00000000000",
                "email": f"buyer{buyer_id}@davixsales.bot",
            },
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/payments", headers=self._headers(),
                data=json.dumps(payload), timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"PicPay error: {data}")

        return ChargeResult(
            external_id=str(data.get("referenceId")),
            checkout_url=data.get("paymentUrl"),
            qr_code_text=data.get("qrcode", {}).get("content") if isinstance(data.get("qrcode"), dict) else None,
            qr_code_base64=data.get("qrcode", {}).get("base64") if isinstance(data.get("qrcode"), dict) else None,
            instructions="Open the link or scan the QR code in the PicPay app.",
            raw=data,
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/payments/{external_id}/status", headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return PaymentStatus.ERROR
        return {
            "paid": PaymentStatus.PAID,
            "completed": PaymentStatus.PAID,
            "created": PaymentStatus.PENDING,
            "expired": PaymentStatus.EXPIRED,
            "refunded": PaymentStatus.REFUNDED,
            "cancelled": PaymentStatus.CANCELLED,
        }.get(str(data.get("status", "")).lower(), PaymentStatus.PENDING)
