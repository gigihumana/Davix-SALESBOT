"""
Asaas gateway - Brazilian payments API (Pix, boleto, card).
Docs: https://docs.asaas.com/reference/pix-1
"""
from __future__ import annotations

import json

import aiohttp

from gateways.base import BaseGateway, ChargeResult, GatewayField, PaymentStatus


class AsaasGateway(BaseGateway):
    key = "asaas"
    display_name = "Asaas (Pix/Boleto)"
    supported_currencies = ("BRL",)
    homepage = "https://www.asaas.com"
    credential_fields = (
        GatewayField("api_key", "API Key ($aact_...)", secret=True),
        GatewayField("environment", "Environment (production/sandbox)", secret=False, required=False,
                     placeholder="production"),
    )

    def _api_base(self) -> str:
        env = (self.credentials.get("environment") or "production").strip().lower()
        return "https://sandbox.asaas.com/api/v3" if env == "sandbox" else "https://api.asaas.com/v3"

    def _headers(self) -> dict:
        return {"access_token": self.credentials.get("api_key", ""), "Content-Type": "application/json"}

    async def create_charge(
        self, *, amount: float, currency: str, description: str,
        order_id: int, idempotency_key: str, buyer_id: int,
    ) -> ChargeResult:
        async with aiohttp.ClientSession() as session:
            # Asaas payments require a customer id - create a lightweight
            # anonymous customer per buyer so we don't need real PII.
            cust_payload = {"name": f"Discord Buyer {buyer_id}", "externalReference": str(buyer_id)}
            async with session.post(
                f"{self._api_base()}/customers", headers=self._headers(),
                data=json.dumps(cust_payload), timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                cust_data = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"Asaas customer error: {cust_data}")

            payload = {
                "customer": cust_data["id"],
                "billingType": "PIX",
                "value": round(float(amount), 2),
                "dueDate": _due_date(),
                "description": description[:500],
                "externalReference": str(order_id),
            }
            async with session.post(
                f"{self._api_base()}/payments", headers=self._headers(),
                data=json.dumps(payload), timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                data = await resp.json()
                if resp.status not in (200, 201):
                    raise RuntimeError(f"Asaas error: {data}")

            pix_qr = {}
            async with session.get(
                f"{self._api_base()}/payments/{data['id']}/pixQrCode", headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as qr_resp:
                if qr_resp.status == 200:
                    pix_qr = await qr_resp.json()

        return ChargeResult(
            external_id=str(data["id"]),
            checkout_url=data.get("invoiceUrl"),
            qr_code_text=pix_qr.get("payload"),
            qr_code_base64=pix_qr.get("encodedImage"),
            instructions="Scan the Pix QR code or copy the Pix code to pay.",
            raw=data,
        )

    async def check_status(self, external_id: str) -> PaymentStatus:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self._api_base()}/payments/{external_id}", headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                data = await resp.json()
                if resp.status != 200:
                    return PaymentStatus.ERROR
        return {
            "RECEIVED": PaymentStatus.PAID,
            "CONFIRMED": PaymentStatus.PAID,
            "RECEIVED_IN_CASH": PaymentStatus.PAID,
            "PENDING": PaymentStatus.PENDING,
            "OVERDUE": PaymentStatus.EXPIRED,
            "REFUNDED": PaymentStatus.REFUNDED,
        }.get(data.get("status", ""), PaymentStatus.PENDING)


def _due_date() -> str:
    import datetime
    return (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
