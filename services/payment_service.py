"""
Ties together the database, a guild's configured gateway credentials, and
product delivery. This is the one place that decides "an order got paid,
now give the buyer their product" - keeping that logic in a single spot
(instead of duplicated per gateway) is what prevents double-delivery bugs.

Key guarantee: whatever is handed to the buyer at delivery time (a stock
line and/or a file) is persisted on the order row. Every later "resend" -
whether the buyer clicks a button, runs /resend in DM, or staff runs
/order resend - replays that exact saved content. Nothing is ever
re-claimed from stock or regenerated on resend.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import time

import discord

from config import settings
from database.db import db
from gateways.base import BaseGateway, PaymentStatus
from gateways.registry import build_gateway
from utils.embeds import error_embed, format_money, success_embed, warning_embed
from utils.emoji_manager import emoji_manager as E
from utils.logger import log
from utils.security import decrypt_credentials, new_idempotency_key

# Guards against two coroutines delivering the same order at once
# (e.g. a webhook firing at the same moment the poller wakes up).
_delivery_locks: dict[int, asyncio.Lock] = {}

DELIVERIES_DIR = "data/deliveries"
PRODUCT_FILES_DIR = "data/product_files"


def _lock_for(order_id: int) -> asyncio.Lock:
    lock = _delivery_locks.get(order_id)
    if lock is None:
        lock = asyncio.Lock()
        _delivery_locks[order_id] = lock
    return lock


async def get_gateway_instance(guild_id: int, gateway_key: str) -> BaseGateway | None:
    cfg = await db.get_gateway_config(guild_id, gateway_key)
    if not cfg or not cfg["enabled"]:
        return None
    creds = decrypt_credentials(cfg["credentials"])
    return build_gateway(gateway_key, creds, cfg["extra"])


def apply_coupon(price: float, coupon) -> float:
    if coupon is None:
        return price
    if coupon["percent_off"]:
        price = price * (1 - float(coupon["percent_off"]) / 100)
    if coupon["amount_off"]:
        price = price - float(coupon["amount_off"])
    return max(0.01, round(price, 2))


async def validate_coupon(guild_id: int, code: str, product_id: int):
    if not code:
        return None
    coupon = await db.get_coupon(guild_id, code)
    if coupon is None:
        raise ValueError("Invalid or expired coupon code.")
    if coupon["expires_at"] and coupon["expires_at"] < int(time.time()):
        raise ValueError("This coupon has expired.")
    if coupon["max_uses"] and coupon["used_count"] >= coupon["max_uses"]:
        raise ValueError("This coupon has reached its usage limit.")
    if coupon["product_id"] and coupon["product_id"] != product_id:
        raise ValueError("This coupon does not apply to this product.")
    return coupon


async def start_purchase(
    *, guild_id: int, user_id: int, product_id: int, gateway_key: str, coupon_code: str | None = None,
):
    """Creates the DB order row + the charge on the gateway's side."""
    product = await db.get_product(product_id)
    if not product or not product["active"]:
        raise ValueError("This product is not available anymore.")

    if product["delivery_type"] == "stock":
        stock_left = await db.stock_count(product_id)
        if stock_left <= 0:
            raise ValueError("This product is out of stock.")

    gateway = await get_gateway_instance(guild_id, gateway_key)
    if gateway is None:
        raise ValueError("This payment method is not enabled in this server.")
    if not gateway.supports_currency(product["currency"]):
        raise ValueError(
            f"{gateway.display_name} does not support {product['currency']} for this product."
        )

    coupon = await validate_coupon(guild_id, coupon_code, product_id) if coupon_code else None
    final_price = apply_coupon(product["price"], coupon)

    idem_key = new_idempotency_key("order")
    order_id = await db.create_order(
        guild_id=guild_id, user_id=user_id, product_id=product_id,
        gateway_key=gateway_key, amount=final_price, currency=product["currency"],
        idempotency_key=idem_key,
    )
    if coupon:
        await db.set_order_coupon(order_id, coupon["code"])
        await db.use_coupon(coupon["id"])

    try:
        charge = await gateway.create_charge(
            amount=final_price, currency=product["currency"],
            description=f"{product['name']} - Order #{order_id}",
            order_id=order_id, idempotency_key=idem_key, buyer_id=user_id,
        )
    except Exception as exc:
        await db.set_order_status(order_id, "error")
        log.error(f"Gateway '{gateway_key}' failed to create charge for order {order_id}: {exc}")
        raise ValueError(
            "The payment provider rejected this request. Please contact staff."
        ) from exc

    await db.set_order_external(order_id, charge.external_id, charge.raw)
    return order_id, charge


async def create_cart_channel(
    guild: discord.Guild, category_id: int, buyer: discord.abc.User, order_id: int,
) -> discord.TextChannel | None:
    """Creates a private ticket-style channel for this order, visible only
    to the buyer and configured staff roles."""
    category = guild.get_channel(category_id)
    if not isinstance(category, discord.CategoryChannel):
        return None

    staff_role_ids = await db.list_staff_roles(guild.id)
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True),
    }
    member = guild.get_member(buyer.id)
    if member:
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )
    for role_id in staff_role_ids:
        role = guild.get_role(role_id)
        if role:
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

    safe_name = f"cart-{order_id}-{getattr(buyer, 'name', 'buyer')}"[:90]
    try:
        channel = await category.create_text_channel(
            name=safe_name, overwrites=overwrites, category=category,
            reason=f"Davix Sales Bot cart for order #{order_id}",
        )
        return channel
    except discord.Forbidden:
        log.warning(f"Missing permission to create cart channel for order {order_id}")
        return None


async def confirm_and_deliver(bot: discord.Client, order_id: int, *, force: bool = False) -> bool:
    """Marks an order paid and delivers the product exactly once."""
    async with _lock_for(order_id):
        order = await db.get_order(order_id)
        if order is None:
            return False
        if order["status"] == "paid" and order["delivered_at"]:
            return True  # already handled - idempotent no-op
        if order["status"] not in ("pending", "paid") and not force:
            return False

        if order["status"] != "paid":
            await db.set_order_status(order_id, "paid")

        product = await db.get_product(order["product_id"])
        if product is None:
            log.error(f"Order {order_id} points to a deleted product.")
            return False

        delivered_content: str | None = None
        if product["delivery_type"] == "stock":
            delivered_content = await db.claim_stock_item(product["id"], order_id)
            if delivered_content is None:
                await _notify_staff_out_of_stock(bot, order, product)
                return False
            remaining = await db.stock_count(product["id"])
            if remaining <= product["low_stock_alert"]:
                await _notify_low_stock(bot, product, remaining)

        delivered_file_path, delivered_file_name = None, None
        if product["file_path"] and os.path.exists(product["file_path"]):
            delivered_file_path, delivered_file_name = _snapshot_file(order_id, product)

        # Persist EXACTLY what was handed out, encrypted - resend replays this.
        await db.save_delivered_content(order_id, delivered_content, delivered_file_path, delivered_file_name)
        await db.mark_delivered(order_id)

        await _grant_role_if_applicable(bot, order, product)
        dm_ok = await _deliver_to_buyer(bot, order, product, delivered_content, delivered_file_path, delivered_file_name)
        await db.set_dm_failed(order_id, not dm_ok)
        if not dm_ok:
            await _notify_dm_failed(bot, order, product)
        await _log_sale(bot, order, product)
        return True


def _snapshot_file(order_id: int, product) -> tuple[str, str]:
    """Copies the product's template file into a per-order folder so a
    later resend always sends this exact file, even if the admin swaps
    the product's file afterwards."""
    order_dir = os.path.join(DELIVERIES_DIR, str(order_id))
    os.makedirs(order_dir, exist_ok=True)
    file_name = product["file_name"] or os.path.basename(product["file_path"])
    dest = os.path.join(order_dir, file_name)
    shutil.copyfile(product["file_path"], dest)
    return dest, file_name


async def _grant_role_if_applicable(bot, order, product) -> None:
    if product["delivery_type"] != "role" or not product["role_id"]:
        return
    guild = bot.get_guild(order["guild_id"])
    if not guild:
        return
    member = guild.get_member(order["user_id"])
    role = guild.get_role(product["role_id"])
    if member and role:
        try:
            await member.add_roles(role, reason=f"Davix Sales Bot order #{order['id']}")
        except discord.Forbidden:
            log.warning(f"Missing permission to grant role for order {order['id']}")


def build_delivery_embed(order, product, content: str | None, file_name: str | None):
    embed = success_embed(
        "Payment confirmed!",
        f"Thank you for your purchase, here is your **{product['name']}**.",
    )
    embed.add_field(name="Order", value=f"#{order['id']}", inline=True)
    embed.add_field(name="Amount", value=format_money(order["amount"], order["currency"]), inline=True)
    if content:
        embed.add_field(name=f"{E.get('dvgift', '🎁')} Delivery", value=f"```{content}```", inline=False)
    if file_name:
        embed.add_field(name=f"{E.get('dvgift', '🎁')} Attached file", value=file_name, inline=False)
    if not content and not file_name and product["delivery_type"] == "role":
        embed.add_field(name=f"{E.get('dvgift', '🎁')} Delivery", value="Your role has been granted.", inline=False)
    return embed


async def _deliver_to_buyer(bot, order, product, content, file_path, file_name) -> bool:
    """Returns True if the DM succeeded."""
    try:
        user = await bot.fetch_user(order["user_id"])
    except discord.NotFound:
        return False

    embed = build_delivery_embed(order, product, content, file_name)
    file = discord.File(file_path, filename=file_name) if file_path and os.path.exists(file_path) else None

    try:
        if file:
            await user.send(embed=embed, file=file)
        else:
            await user.send(embed=embed)
        return True
    except discord.Forbidden:
        return False


async def resend_delivery(bot: discord.Client, order_id: int) -> tuple[bool, str]:
    """Re-sends the exact content already recorded for this order. Never
    re-claims stock or regenerates anything."""
    order = await db.get_order(order_id)
    if order is None:
        return False, "Order not found."
    if order["status"] != "paid" or not order["delivered_at"]:
        return False, "This order was never confirmed/delivered."

    product = await db.get_product(order["product_id"])
    content, file_path, file_name = await db.get_delivered_content(order_id)
    if not content and not file_path and (not product or product["delivery_type"] != "role"):
        return False, "Nothing was recorded for this order."

    try:
        user = await bot.fetch_user(order["user_id"])
    except discord.NotFound:
        return False, "Could not find your Discord user."

    embed = build_delivery_embed(order, product or {"name": "your item", "delivery_type": "manual"}, content, file_name)
    file = discord.File(file_path, filename=file_name) if file_path and os.path.exists(file_path) else None
    try:
        if file:
            await user.send(embed=embed, file=file)
        else:
            await user.send(embed=embed)
    except discord.Forbidden:
        return False, (
            "I still can't DM you. Please enable **Allow direct messages from server members** "
            "in your Privacy Settings for a server we share, then try /resend again."
        )

    await db.set_dm_failed(order_id, False)
    return True, "Delivered to your DMs."


async def _notify_dm_failed(bot, order, product) -> None:
    embed = warning_embed(
        "Could not DM the buyer",
        f"<@{order['user_id']}> paid for **{product['name']}** (order #{order['id']}) but has "
        f"DMs disabled. They can get their item with `/resend` once DMs are enabled, or staff "
        f"can run `/order resend order_id:{order['id']}`.",
    )
    if order["cart_channel_id"]:
        channel = bot.get_channel(order["cart_channel_id"])
        if channel:
            try:
                await channel.send(f"<@{order['user_id']}>", embed=embed)
                return
            except discord.Forbidden:
                pass
    settings_row = await db.get_guild_settings(order["guild_id"])
    if settings_row and settings_row["log_channel_id"]:
        channel = bot.get_channel(settings_row["log_channel_id"])
        if channel:
            try:
                await channel.send(embed=embed)
            except discord.Forbidden:
                pass


async def _log_sale(bot, order, product) -> None:
    settings_row = await db.get_guild_settings(order["guild_id"])
    if not settings_row or not settings_row["log_channel_id"]:
        return
    channel = bot.get_channel(settings_row["log_channel_id"])
    if not channel:
        return
    embed = success_embed(
        "New sale",
        f"<@{order['user_id']}> bought **{product['name']}** via {order['gateway_key']}.",
    )
    embed.add_field(name="Order", value=f"#{order['id']}")
    embed.add_field(name="Amount", value=format_money(order["amount"], order["currency"]))
    if order["coupon_code"]:
        embed.add_field(name="Coupon used", value=order["coupon_code"])
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass


async def _notify_staff_out_of_stock(bot, order, product) -> None:
    settings_row = await db.get_guild_settings(order["guild_id"])
    log.warning(f"Order {order['id']} paid but '{product['name']}' has no stock left.")
    if not settings_row or not settings_row["log_channel_id"]:
        return
    channel = bot.get_channel(settings_row["log_channel_id"])
    if not channel:
        return
    embed = error_embed(
        "Out of stock after payment",
        f"<@{order['user_id']}> paid for **{product['name']}** (order #{order['id']}) "
        f"but there is no stock left. Please add stock and deliver manually, "
        f"then run `/order confirm order_id:{order['id']}`.",
    )
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass


async def _notify_low_stock(bot, product, remaining: int) -> None:
    settings_row = await db.get_guild_settings(product["guild_id"])
    if not settings_row or not settings_row["log_channel_id"]:
        return
    channel = bot.get_channel(settings_row["log_channel_id"])
    if not channel:
        return
    embed = warning_embed(
        "Low stock warning",
        f"**{product['name']}** has only **{remaining}** item(s) left. "
        f"Add more with `/product stock-add`.",
    )
    try:
        await channel.send(embed=embed)
    except discord.Forbidden:
        pass


async def poll_pending_orders(bot: discord.Client) -> None:
    """Background loop: for gateways without (or in addition to) webhooks."""
    while True:
        try:
            pending = await db.pending_orders()
            for order in pending:
                gateway = await get_gateway_instance(order["guild_id"], order["gateway_key"])
                if gateway is None or not order["external_id"]:
                    continue
                try:
                    status = await gateway.check_status(order["external_id"])
                except Exception as exc:
                    log.warning(f"Status check failed for order {order['id']}: {exc}")
                    continue

                if status == PaymentStatus.PAID:
                    await confirm_and_deliver(bot, order["id"])
                elif status in (PaymentStatus.EXPIRED, PaymentStatus.CANCELLED):
                    await db.set_order_status(order["id"], status.value)
                elif status == PaymentStatus.ERROR:
                    await db.set_order_status(order["id"], "error")
        except Exception as exc:  # never let the loop die
            log.error(f"poll_pending_orders crashed: {exc}")
        await asyncio.sleep(settings.poll_interval)
