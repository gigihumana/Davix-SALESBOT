"""
Optional local HTTP server that receives instant webhook notifications
from Mercado Pago, Stripe, PayPal and NOWPayments, instead of waiting for
the polling loop. Every request is signature-verified using that guild's
own stored secret before anything is trusted.

Route shape:  POST /webhook/<gateway_key>/<guild_id>
Configure this URL (WEBHOOK_PUBLIC_URL + the route) in each gateway's
dashboard if you enable WEBHOOK_ENABLED=true.
"""
from __future__ import annotations

from aiohttp import web

from config import settings
from database.db import db
from gateways.base import PaymentStatus
from services.payment_service import confirm_and_deliver, get_gateway_instance
from utils.logger import log


def build_app(bot) -> web.Application:
    app = web.Application(client_max_size=1 * 1024 * 1024)  # 1 MB cap, avoid oversized payload abuse

    async def handle_webhook(request: web.Request) -> web.Response:
        gateway_key = request.match_info["gateway_key"]
        try:
            guild_id = int(request.match_info["guild_id"])
        except ValueError:
            return web.Response(status=400, text="bad guild id")

        body = await request.read()
        gateway = await get_gateway_instance(guild_id, gateway_key)
        if gateway is None:
            return web.Response(status=404, text="gateway not configured")

        headers = {k.lower(): v for k, v in request.headers.items()}
        if not gateway.verify_webhook(headers, body):
            log.warning(f"Rejected webhook with bad signature for guild {guild_id}/{gateway_key}")
            return web.Response(status=401, text="invalid signature")

        parsed = gateway.parse_webhook(headers, body)
        if parsed is None:
            return web.Response(status=200, text="ignored")

        external_id, status = parsed
        order = await db.get_order_by_external(gateway_key, external_id)
        if order is None:
            return web.Response(status=200, text="unknown order")

        # Always re-confirm with a live status call - webhooks are a signal
        # to check sooner, never a substitute for verifying the real status.
        real_status = await gateway.check_status(external_id)
        if real_status == PaymentStatus.PAID:
            await confirm_and_deliver(bot, order["id"])
        elif real_status in (PaymentStatus.EXPIRED, PaymentStatus.CANCELLED, PaymentStatus.ERROR):
            await db.set_order_status(order["id"], real_status.value)

        return web.Response(status=200, text="ok")

    app.router.add_post("/webhook/{gateway_key}/{guild_id}", handle_webhook)

    async def healthcheck(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "bot": "davix-sales-bot"})

    app.router.add_get("/health", healthcheck)
    return app


async def start_webhook_server(bot) -> web.AppRunner:
    app = build_app(bot)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, settings.webhook_host, settings.webhook_port)
    await site.start()
    log.info(f"Webhook server listening on {settings.webhook_host}:{settings.webhook_port}")
    return runner
