"""
Stripe gateway using Stripe Checkout Sessions via raw REST calls
(no stripe-python dependency needed).

Docs: https://docs.stripe.com/api/checkout/sessions
Webhook signing: https://docs.stripe.com/webhooks#verify-manually
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import aiohttp

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus

API_BASE = "https://api.stripe.com/v1"

# Stripe accepts most ISO currencies; this is a broad, realistic subset.
SUPPORTED_CURRENCIES = (
    "USD", "EUR", "GBP", "CAD", "AUD", "JPY", "MXN", "CHF", "NZD", "SGD",
    "HKD", "SEK", "NOK", "DKK", "PLN", "ZAR", "AED", "INR",
)


class StripeGateway(BaseGateway):
    key = "stripe"
    display_name = "Stripe"
    supported_currencies = SUPPORTED_CURRENCIES
    homepage = "https://dashboard.stripe.com/apikeys"
    credential_fields = (
        GatewayField("secret_key", "Secret Key (sk_live_... / sk_test_...)", secret=True),
        GatewayField("webhook_secret", "Webhook Signing Secret (whsec_...)", secret=True, required=False),
        GatewayField("success_url", "Success redirect URL", secret=False, required=False,
                     placeholder="https://discord.com/channels/@me"),
    )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.credentials.get('secret_key', '')}",
            "Content-Type": "application/x-www-form-urlencoded",
        }

    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        currency_lc = currency.lower()
        # Stripe wants the smallest currency unit (cents), zero-decimal
        # currencies (like JPY) are the well-known exception.
        zero_decimal = currency.upper() in ("JPY", "KRW", "VND")
        unit_amount = int(round(amount)) if zero_decimal else int(round(amount * 100))

        success_url = self.credentials.get("success_url") or "https://discord.com"
        form = {
            "mode": "payment",
            "success_url": f"{success_url}?paid=1",
            "cancel_url": f"{success_url}?cancelled=1",
            "line_items[0][price_data][currency]": currency_lc,
            "line_items[0][price_data][product_data][name]": description[:250],
            "line_items[0][price_data][unit_amount]": str(unit_amount),
            "line_items[0][quantity]": "1",
            "client_reference_id": str(order_id),
            "metadata[order_id]": str(order_id),
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/checkout/sessions",
                headers={**self._headers(), "Idempotency-Key": idempotency_key},
                data=form,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"Stripe error: {data}")

        return ChargeResult(
            external_id=data["id"],
            checkout_url=data.get("url"),
            instructions="Click the link and complete checkout with your card.",
            raw=data,
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/checkout/sessions/{external_id}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return PaymentStatus.ERROR
        payment_status = data.get("payment_status")
        status = data.get("status")
        if payment_status == "paid":
            return PaymentStatus.PAID
        if status == "expired":
            return PaymentStatus.EXPIRED
        return PaymentStatus.PENDING

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        secret = self.credentials.get("webhook_secret")
        if not secret:
            return True
        sig_header = headers.get("stripe-signature") or headers.get("Stripe-Signature", "")
        parts = dict(p.split("=", 1) for p in sig_header.split(",") if "=" in p)
        ts = parts.get("t", "")
        v1 = parts.get("v1", "")
        if not ts or not v1:
            return False
        # Reject stale signatures (replay protection), 5 minute tolerance.
        try:
            if abs(time.time() - int(ts)) > 300:
                return False
        except ValueError:
            return False
        signed_payload = f"{ts}.{body.decode('utf-8')}"
        expected = hmac.new(secret.encode(), signed_payload.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, v1)

    def parse_webhook(self, headers: dict, body: bytes) -> tuple[str, PaymentStatus] | None:
        try:
            event = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        if event.get("type") != "checkout.session.completed":
            return None
        session_obj = event.get("data", {}).get("object", {})
        session_id = session_obj.get("id")
        if not session_id:
            return None
        status = PaymentStatus.PAID if session_obj.get("payment_status") == "paid" else PaymentStatus.PENDING
        return session_id, status
