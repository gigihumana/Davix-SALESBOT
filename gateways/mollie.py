"""
Mollie gateway - popular European payments API (iDEAL, cards, Bancontact...).
Docs: https://docs.mollie.com/reference/create-payment
"""
from __future__ import annotations

import json

import aiohttp

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus

API_BASE = "https://api.mollie.com/v2"
SUPPORTED_CURRENCIES = ("EUR", "USD", "GBP", "CHF", "DKK", "NOK", "SEK", "PLN")


class MollieGateway(BaseGateway):
    key = "mollie"
    display_name = "Mollie (Europe)"
    supported_currencies = SUPPORTED_CURRENCIES
    homepage = "https://www.mollie.com/dashboard/developers/api-keys"
    credential_fields = (
        GatewayField("api_key", "API Key (live_... / test_...)", secret=True),
        GatewayField("redirect_url", "Redirect URL after payment", secret=False, required=False,
                     placeholder="https://discord.com"),
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
            "amount": {"currency": currency.upper(), "value": f"{amount:.2f}"},
            "description": description[:255],
            "redirectUrl": self.credentials.get("redirect_url") or "https://discord.com",
            "metadata": {"order_id": str(order_id)},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/payments", headers=self._headers(),
                data=json.dumps(payload), timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"Mollie error: {data}")

        checkout_url = data.get("_links", {}).get("checkout", {}).get("href")
        return ChargeResult(
            external_id=data["id"],
            checkout_url=checkout_url,
            instructions="Click the link to complete your payment.",
            raw=data,
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/payments/{external_id}", headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return PaymentStatus.ERROR
        return {
            "paid": PaymentStatus.PAID,
            "open": PaymentStatus.PENDING,
            "pending": PaymentStatus.PENDING,
            "authorized": PaymentStatus.PENDING,
            "expired": PaymentStatus.EXPIRED,
            "canceled": PaymentStatus.CANCELLED,
            "failed": PaymentStatus.ERROR,
        }.get(data.get("status", ""), PaymentStatus.PENDING)
