"""
Mercado Pago gateway (Pix + Checkout Pro), using the public REST API directly
so the bot has zero hard dependency on Mercado Pago's own SDK.

Docs: https://www.mercadopago.com.br/developers/en/reference
Webhook signature: https://www.mercadopago.com.br/developers/en/docs/checkout-api/webhooks
"""
from __future__ import annotations

import hashlib
import hmac
import json

import aiohttp

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus

API_BASE = "https://api.mercadopago.com"


class MercadoPagoGateway(BaseGateway):
    key = "mercadopago"
    display_name = "Mercado Pago"
    supported_currencies = ("BRL",)
    homepage = "https://www.mercadopago.com.br/developers"
    credential_fields = (
        GatewayField("access_token", "Access Token", secret=True),
        GatewayField("webhook_secret", "Webhook Secret Key (optional)", secret=True, required=False),
    )

    def _headers(self, idempotency_key: str | None = None) -> dict:
        h = {
            "Authorization": f"Bearer {self.credentials.get('access_token', '')}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            h["X-Idempotency-Key"] = idempotency_key
        return h

    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        payload = {
            "transaction_amount": round(float(amount), 2),
            "description": description[:250],
            "payment_method_id": "pix",
            "payer": {"email": f"buyer{buyer_id}@davixsales.bot"},
            "external_reference": str(order_id),
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{API_BASE}/v1/payments",
                headers=self._headers(idempotency_key),
                data=json.dumps(payload),
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"Mercado Pago error: {data}")

        poi = data.get("point_of_interaction", {}) or {}
        tx_data = poi.get("transaction_data", {}) or {}
        return ChargeResult(
            external_id=str(data["id"]),
            checkout_url=tx_data.get("ticket_url"),
            qr_code_text=tx_data.get("qr_code"),
            qr_code_base64=tx_data.get("qr_code_base64"),
            instructions="Scan the Pix QR code or copy the Pix code to pay.",
            raw=data,
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{API_BASE}/v1/payments/{external_id}",
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return PaymentStatus.ERROR
        return _map_status(data.get("status", ""))

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        secret = self.credentials.get("webhook_secret")
        if not secret:
            # No secret configured -> the admin opted out of verification;
            # we still accept the notification but callers should re-check
            # the payment status via the API before delivering anything.
            return True
        signature_header = headers.get("x-signature") or headers.get("X-Signature", "")
        request_id = headers.get("x-request-id") or headers.get("X-Request-Id", "")
        parts = dict(
            p.split("=", 1) for p in signature_header.split(",") if "=" in p
        )
        ts = parts.get("ts", "")
        v1 = parts.get("v1", "")
        try:
            data = json.loads(body.decode("utf-8"))
            data_id = str(data.get("data", {}).get("id", ""))
        except Exception:
            data_id = ""
        manifest = f"id:{data_id};request-id:{request_id};ts:{ts};"
        expected = hmac.new(secret.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, v1)

    def parse_webhook(self, headers: dict, body: bytes) -> tuple[str, PaymentStatus] | None:
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        payment_id = data.get("data", {}).get("id")
        if not payment_id:
            return None
        # Status is not in the webhook payload itself - caller must confirm
        # via check_status(). We return PENDING here as a signal to re-check.
        return str(payment_id), PaymentStatus.PENDING


def _map_status(mp_status: str) -> PaymentStatus:
    return {
        "approved": PaymentStatus.PAID,
        "pending": PaymentStatus.PENDING,
        "in_process": PaymentStatus.PENDING,
        "rejected": PaymentStatus.ERROR,
        "cancelled": PaymentStatus.CANCELLED,
        "refunded": PaymentStatus.REFUNDED,
        "charged_back": PaymentStatus.REFUNDED,
    }.get(mp_status, PaymentStatus.PENDING)
