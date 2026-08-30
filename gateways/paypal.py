"""
PayPal gateway using the Orders v2 REST API.

Docs: https://developer.paypal.com/docs/api/orders/v2/
Webhook verification: https://developer.paypal.com/api/rest/webhooks/rest/#link-verifysignature
"""
from __future__ import annotations

import json
import time

import aiohttp

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus

SUPPORTED_CURRENCIES = (
    "USD", "EUR", "GBP", "CAD", "AUD", "JPY", "MXN", "CHF", "NZD", "SGD",
    "HKD", "SEK", "NOK", "DKK", "PLN", "TWD", "THB", "ILS",
)


class PayPalGateway(BaseGateway):
    key = "paypal"
    display_name = "PayPal"
    supported_currencies = SUPPORTED_CURRENCIES
    homepage = "https://developer.paypal.com/dashboard/applications"
    credential_fields = (
        GatewayField("client_id", "Client ID", secret=True),
        GatewayField("client_secret", "Client Secret", secret=True),
        GatewayField("environment", "Environment (live/sandbox)", secret=False, required=False,
                     placeholder="live"),
    )

    def _api_base(self) -> str:
        env = (self.credentials.get("environment") or "live").strip().lower()
        return "https://api-m.sandbox.paypal.com" if env == "sandbox" else "https://api-m.paypal.com"

    def _token_cache_key(self) -> str:
        return f"paypal_token::{self.credentials.get('client_id', '')}"

    _tokens: dict[str, tuple[str, float]] = {}

    async def _get_access_token(self, session: aiohttp.ClientSession) -> str:
        cache_key = self._token_cache_key()
        cached = self._tokens.get(cache_key)
        if cached and cached[1] > time.time() + 30:
            return cached[0]

        auth = aiohttp.BasicAuth(
            self.credentials.get("client_id", ""), self.credentials.get("client_secret", "")
        )
        async with session.post(
            f"{self._api_base()}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=auth,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f"PayPal auth error: {data}")
        token = data["access_token"]
        expires_at = time.time() + int(data.get("expires_in", 3600))
        self._tokens[cache_key] = (token, expires_at)
        return token

    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        async with aiohttp.ClientSession() as session:
            token = await self._get_access_token(session)
            payload = {
                "intent": "CAPTURE",
                "purchase_units": [
                    {
                        "reference_id": str(order_id),
                        "description": description[:127],
                        "amount": {
                            "currency_code": currency.upper(),
                            "value": f"{amount:.2f}",
                        },
                    }
                ],
                "application_context": {
                    "brand_name": "Davix Sales Bot",
                    "user_action": "PAY_NOW",
                    "shipping_preference": "NO_SHIPPING",
                },
            }
            async with session.post(
                f"{self._api_base()}/v2/checkout/orders",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                    "PayPal-Request-Id": idempotency_key,
                },
                data=json.dumps(payload),
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"PayPal error: {data}")

        approve_url = next(
            (link["href"] for link in data.get("links", []) if link.get("rel") == "approve"),
            None,
        )
        return ChargeResult(
            external_id=data["id"],
            checkout_url=approve_url,
            instructions="Click the link to approve the payment with your PayPal account.",
            raw=data,
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        async with aiohttp.ClientSession() as session:
            token = await self._get_access_token(session)
            async with session.get(
                f"{self._api_base()}/v2/checkout/orders/{external_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return PaymentStatus.ERROR

            status = data.get("status")
            if status == "COMPLETED":
                return PaymentStatus.PAID
            if status == "APPROVED":
                # Funds authorized but not captured yet - capture now.
                async with session.post(
                    f"{self._api_base()}/v2/checkout/orders/{external_id}/capture",
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as cap_resp:
                    cap_data = await cap_resp.json()
                    if cap_resp.status in (200, 201) and cap_data.get("status") == "COMPLETED":
                        return PaymentStatus.PAID
            if status == "VOIDED":
                return PaymentStatus.CANCELLED
        return PaymentStatus.PENDING

    def verify_webhook(self, headers: dict, body: bytes) -> bool:
        # Full verification requires an extra call to PayPal's
        # verify-webhook-signature endpoint (needs the webhook ID + certs).
        # We accept the notification here and always re-confirm the real
        # status via check_status() before ever delivering a product.
        return True

    def parse_webhook(self, headers: dict, body: bytes) -> tuple[str, PaymentStatus] | None:
        try:
            event = json.loads(body.decode("utf-8"))
        except Exception:
            return None
        resource = event.get("resource", {})
        order_id = resource.get("supplementary_data", {}).get("related_ids", {}).get("order_id")
        order_id = order_id or resource.get("id")
        if not order_id:
            return None
        return str(order_id), PaymentStatus.PENDING
