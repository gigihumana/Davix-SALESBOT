"""
Central registry of every payment gateway plugin the bot ships with.

To add support for a brand new gateway later:
  1. Create gateways/your_gateway.py implementing BaseGateway.
  2. Import it below and add it to GATEWAY_CLASSES.
That's the entire integration surface - no other file needs to change.
"""
from __future__ import annotations

from gateways.base import BaseGateway
from gateways.abacatepay import AbacatePayGateway
from gateways.asaas import AsaasGateway
from gateways.coinbase_commerce import CoinbaseCommerceGateway
from gateways.manual import ManualGateway
from gateways.mercadopago import MercadoPagoGateway
from gateways.mollie import MollieGateway
from gateways.nowpayments import NowPaymentsGateway
from gateways.paypal import PayPalGateway
from gateways.paystack import PaystackGateway
from gateways.picpay import PicPayGateway
from gateways.pushinpay import PushinPayGateway
from gateways.razorpay_gateway import RazorpayGateway
from gateways.stripe_gateway import StripeGateway

GATEWAY_CLASSES: dict[str, type[BaseGateway]] = {
    cls.key: cls
    for cls in (
        MercadoPagoGateway,
        PushinPayGateway,
        AsaasGateway,
        AbacatePayGateway,
        PicPayGateway,
        StripeGateway,
        PayPalGateway,
        MollieGateway,
        RazorpayGateway,
        PaystackGateway,
        CoinbaseCommerceGateway,
        NowPaymentsGateway,
        ManualGateway,
    )
}


def get_gateway_class(key: str) -> type[BaseGateway] | None:
    return GATEWAY_CLASSES.get(key)


def build_gateway(key: str, credentials: dict, extra: dict | None = None) -> BaseGateway | None:
    cls = get_gateway_class(key)
    if cls is None:
        return None
    return cls(credentials, extra)


def all_gateways() -> list[type[BaseGateway]]:
    return list(GATEWAY_CLASSES.values())


def gateways_for_currency(currency: str) -> list[type[BaseGateway]]:
    return [c for c in GATEWAY_CLASSES.values() if currency.upper() in c.supported_currencies]
