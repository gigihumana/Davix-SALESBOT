"""
Coinbase Commerce gateway - accepts BTC, ETH, USDC, LTC, and more via a
hosted crypto checkout. Docs: https://docs.cdp.coinbase.com/commerce-onchain/docs/api-overview
"""
from __future__ import annotations

import hashlib
import hmac
import json

import aiohttp

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus

API_BASE = "https://api.commerce.coinbase.com"
SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP")


class CoinbaseCommerceGateway(BaseGateway):
    key = "coinbase_commerce"
    display_name = "Coinbase Commerce (Crypto)"
    supported_currencies = SUPPORTED_CURRENCIES
    homepage = "https://beta.commerce.coinbase.com/settings/security"
    credential_fields = (
        GatewayField("api_key", "API Key", secret=True),
        GatewayField("webhook_secret", "Webhook Shared Secret (optional)", secret=True, required=False),
    )

    def _headers(self) -> dict:
        return {
            "X-CC-Api-Key": self.credentials.get("api_key", ""),
            "X-CC-Version": "2018-03-22",
            "Content-Type": "application/json",
        }

    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        payload = {
            "name": description[:100],
            "description": description[:250],
            "pricing_type": "fixed_price",
            "local_price": {"amount": f"{amount:.2f}", "currency": currency.upper()},
            "metadata": {"order_id": str(order_id), "buyer_id": str(buyer_id)},
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/charges", headers=self._headers(),
                data=json.dumps(payload), timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"Coinbase Commerce error: {data}")

        item = data["data"]
        return ChargeResult(
            external_id=item["code"],
            checkout_url=item.get("hosted_url"),
            instructions="Open the link and pay with any supported cryptocurrency.",
            raw=item,
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/charges/{external_id}", headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return PaymentStatus.ERROR
        timeline = data.get("data", {}).get("timeline", [])
        statuses = {t.get("status") for t in timeline}
        if "COMPLETED" in statuses or "RESOLVED" in statuses:
            return PaymentStatus.PAID
        if "EXPIRED" in statuses:
            return PaymentStatus.EXPIRED
        if "CANCELED" in statuses:
            return PaymentStatus.CANCELLED
        return PaymentStatus.PENDING

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        secret = self.credentials.get("webhook_secret")
        if not secret:
            return True
        received = headers.get("x-cc-webhook-signature") or headers.get("X-CC-Webhook-Signature", "")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, received)

    def parse_webhook(self, headers: dict, body: bytes) -> tuple[str, PaymentStatus] | None:
        try:
            event = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        charge = event.get("event", {}).get("data", {})
        code = charge.get("code")
        if not code:
            return None
        event_type = event.get("event", {}).get("type", "")
        status = PaymentStatus.PAID if "confirmed" in event_type or "resolved" in event_type else PaymentStatus.PENDING
        return code, status
