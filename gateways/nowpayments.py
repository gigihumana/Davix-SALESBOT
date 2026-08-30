"""
NOWPayments gateway - accepts 100+ cryptocurrencies (BTC, ETH, USDT, LTC,
SOL, TRX, etc.) while the shop's own product prices stay in fiat (USD/BRL);
NOWPayments handles the fiat<->crypto conversion on their side.

Docs: https://documenter.getpostman.com/view/7907941/S1a32n38
"""
from __future__ import annotations

import hashlib
import hmac
import json

import aiohttp

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus

API_BASE = "https://api.nowpayments.io/v1"

# Fiat currencies NOWPayments can price an invoice in - the buyer then pays
# with whichever crypto coin they choose on the hosted checkout page.
SUPPORTED_CURRENCIES = ("USD", "EUR", "BRL", "GBP")


class NowPaymentsGateway(BaseGateway):
    key = "nowpayments"
    display_name = "NOWPayments (Crypto: BTC, ETH, USDT, SOL, LTC & more)"
    supported_currencies = SUPPORTED_CURRENCIES
    homepage = "https://nowpayments.io"
    credential_fields = (
        GatewayField("api_key", "API Key", secret=True),
        GatewayField("ipn_secret", "IPN Secret Key (optional)", secret=True, required=False),
    )

    def _headers(self) -> dict:
        return {
            "x-api-key": self.credentials.get("api_key", ""),
            "Content-Type": "application/json",
        }

    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        payload = {
            "price_amount": round(float(amount), 2),
            "price_currency": currency.lower(),
            "order_id": str(order_id),
            "order_description": description[:250],
            "is_fixed_rate": True,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/invoice",
                headers=self._headers(),
                data=json.dumps(payload),
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"NOWPayments error: {data}")

        return ChargeResult(
            external_id=str(data.get("id")),
            checkout_url=data.get("invoice_url"),
            instructions="Open the link, pick your coin (BTC, ETH, USDT, SOL, ...) and pay.",
            raw=data,
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        # NOWPayments invoices resolve into a "payment" once the buyer picks
        # a coin. We look the invoice's linked payment(s) up by order search.
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/payment/?invoiceId={external_id}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return PaymentStatus.ERROR
                data = await resp.json()

        payments = data.get("data") or data.get("payments") or []
        if not payments:
            return PaymentStatus.PENDING
        latest = payments[-1]
        return _map_status(str(latest.get("payment_status", "")))

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        secret = self.credentials.get("ipn_secret")
        if not secret:
            return True
        received_sig = headers.get("x-nowpayments-sig") or headers.get("X-Nowpayments-Sig", "")
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return False
        sorted_payload = json.dumps(data, sort_keys=True, separators=(",", ":"))
        expected = hmac.new(secret.encode(), sorted_payload.encode(), hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, received_sig)

    def parse_webhook(self, headers: dict, body: bytes) -> tuple[str, PaymentStatus] | None:
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        invoice_id = str(data.get("invoice_id") or data.get("order_id") or "")
        if not invoice_id:
            return None
        return invoice_id, _map_status(str(data.get("payment_status", "")))


def _map_status(status: str) -> PaymentStatus:
    return {
        "finished": PaymentStatus.PAID,
        "confirmed": PaymentStatus.PAID,
        "partially_paid": PaymentStatus.PENDING,
        "waiting": PaymentStatus.PENDING,
        "confirming": PaymentStatus.PENDING,
        "sending": PaymentStatus.PENDING,
        "expired": PaymentStatus.EXPIRED,
        "failed": PaymentStatus.ERROR,
        "refunded": PaymentStatus.REFUNDED,
    }.get(status, PaymentStatus.PENDING)
