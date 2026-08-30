from __future__ import annotations

import base64
import io

import discord
from discord import app_commands
from discord.ext import commands

from config import BOT_NAME
from database.db import db
from gateways.base import PaymentStatus
from gateways.registry import get_gateway_class
from services.payment_service import (
    confirm_and_deliver,
    create_cart_channel,
    get_gateway_instance,
    resend_delivery,
    start_purchase,
)
from utils.embeds import base_embed, error_embed, format_money, success_embed, warning_embed
from utils.emoji_manager import emoji_manager as E
from utils.security import rate_limiter

DM_WARNING = (
    "Make sure **Allow direct messages from server members** is enabled in your "
    "Discord Privacy Settings for this server - that's how your item gets delivered. "
    "If it's off and you already paid, just run `/resend` after enabling it."
)


class BuyPanelView(discord.ui.View):
    """Persistent view posted in the store channel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Buy", style=discord.ButtonStyle.success, custom_id="davix:open_store")
    async def buy_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if await db.is_blacklisted(interaction.guild_id, interaction.user.id):
            await interaction.response.send_message(
                embed=error_embed("You cannot purchase here", "Contact staff if you think this is a mistake."),
                ephemeral=True,
            )
            return
        products = await db.list_products(interaction.guild_id, active_only=True)
        if not products:
            await interaction.response.send_message(
                embed=error_embed("No products available", "Please check back later."), ephemeral=True
            )
            return
        embed = base_embed(f"{E.get('dvcart', '🛒')} Choose a product")
        embed.set_footer(text=f"{DM_WARNING[:150]}... | {BOT_NAME}")
        await interaction.response.send_message(embed=embed, view=ProductSelectView(products), ephemeral=True)


class ProductSelectView(discord.ui.View):
    def __init__(self, products):
        super().__init__(timeout=180)
        self.add_item(ProductSelect(products))


class ProductSelect(discord.ui.Select):
    def __init__(self, products):
        options = [
            discord.SelectOption(
                label=f"{p['name']} - {format_money(p['price'], p['currency'])}"[:100],
                value=str(p["id"]),
                description=(p["description"] or "")[:100] or None,
            )
            for p in products[:25]
        ]
        super().__init__(placeholder="Select a product...", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        product_id = int(self.values[0])
        product = await db.get_product(product_id)
        if not product or not product["active"]:
            await interaction.response.send_message(embed=error_embed("Unavailable", "This product is no longer for sale."), ephemeral=True)
            return

        enabled = await db.list_enabled_gateways(interaction.guild_id)
        usable = [g for g in enabled if get_gateway_class(g["gateway_key"]) and product["currency"].upper() in get_gateway_class(g["gateway_key"]).supported_currencies]
        if not usable:
            await interaction.response.send_message(
                embed=error_embed("No payment method available", f"No gateway here accepts {product['currency']}."),
                ephemeral=True,
            )
            return

        embed = base_embed(
            f"{E.get('dvcard', '💳')} Choose a payment method",
            f"**{product['name']}** - {format_money(product['price'], product['currency'])}\n\n"
            f"Have a coupon? Click **Apply coupon** below before picking a payment method.",
        )
        await interaction.response.edit_message(embed=embed, view=GatewayPickView(product_id, usable, product["price"]))


class ApplyCouponModal(discord.ui.Modal, title="Apply coupon"):
    code = discord.ui.TextInput(label="Coupon code", max_length=40, required=True)

    def __init__(self, product_id: int, usable: list[dict]):
        super().__init__()
        self.product_id = product_id
        self.usable = usable

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from services.payment_service import validate_coupon, apply_coupon
        product = await db.get_product(self.product_id)
        try:
            coupon = await validate_coupon(interaction.guild_id, self.code.value, self.product_id)
        except ValueError as exc:
            await interaction.response.send_message(embed=error_embed("Coupon error", str(exc)), ephemeral=True)
            return
        new_price = apply_coupon(product["price"], coupon)
        embed = base_embed(
            f"{E.get('dvstar', '⭐')} Coupon applied!",
            f"**{product['name']}**\n~~{format_money(product['price'], product['currency'])}~~ "
            f"→ **{format_money(new_price, product['currency'])}**\n\nNow choose a payment method.",
        )
        await interaction.response.edit_message(
            embed=embed, view=GatewayPickView(self.product_id, self.usable, new_price, coupon_code=self.code.value.upper())
        )


class GatewayPickView(discord.ui.View):
    def __init__(self, product_id: int, gateways: list[dict], price: float, coupon_code: str | None = None):
        super().__init__(timeout=180)
        self.add_item(GatewayPickSelect(product_id, gateways, coupon_code))
        if not coupon_code:
            self.add_item(ApplyCouponButton(product_id, gateways))


class ApplyCouponButton(discord.ui.Button):
    def __init__(self, product_id: int, usable: list[dict]):
        super().__init__(label="Apply coupon", style=discord.ButtonStyle.secondary, emoji="🏷️")
        self.product_id = product_id
        self.usable = usable

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(ApplyCouponModal(self.product_id, self.usable))


class GatewayPickSelect(discord.ui.Select):
    def __init__(self, product_id: int, gateways: list[dict], coupon_code: str | None):
        self.product_id = product_id
        self.coupon_code = coupon_code
        options = [
            discord.SelectOption(label=get_gateway_class(g["gateway_key"]).display_name[:100], value=g["gateway_key"])
            for g in gateways
        ]
        super().__init__(placeholder="Select a payment method...", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        gateway_key = self.values[0]
        rl_key = f"user:{interaction.user.id}:purchase"
        if not rate_limiter.allow(rl_key, max_hits=5, window_seconds=60):
            await interaction.response.send_message(
                embed=warning_embed("Slow down", "Too many purchase attempts, please wait a bit."), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            order_id, charge = await start_purchase(
                guild_id=interaction.guild_id, user_id=interaction.user.id,
                product_id=self.product_id, gateway_key=gateway_key, coupon_code=self.coupon_code,
            )
        except ValueError as exc:
            await interaction.followup.send(embed=error_embed("Could not start payment", str(exc)), ephemeral=True)
            return

        product = await db.get_product(self.product_id)
        cls = get_gateway_class(gateway_key)
        order_row = await db.get_order(order_id)

        # If the product wants a private cart channel, create it and post
        # the checkout details there instead of only an ephemeral message.
        cart_channel = None
        if product["cart_category_id"] and interaction.guild:
            cart_channel = await create_cart_channel(
                interaction.guild, product["cart_category_id"], interaction.user, order_id
            )
            if cart_channel:
                await db.set_order_cart_channel(order_id, cart_channel.id)

        embed = base_embed(
            f"{E.get('dvpix', '💠')} Complete your payment",
            f"**{product['name']}** - {format_money(order_row['amount'], product['currency'])}\n"
            f"Method: {cls.display_name}\nOrder: #{order_id}",
        )
        embed.add_field(name=f"{E.get('dvwarn', '⚠️')} Before you pay", value=DM_WARNING, inline=False)
        if charge.instructions:
            embed.add_field(name="Instructions", value=charge.instructions, inline=False)
        if charge.checkout_url:
            embed.add_field(name="Checkout link", value=charge.checkout_url, inline=False)
        if charge.qr_code_text:
            embed.add_field(name="Pix copy-and-paste code", value=f"```{charge.qr_code_text}```", inline=False)

        file = None
        if charge.qr_code_base64:
            try:
                raw = base64.b64decode(charge.qr_code_base64)
                file = discord.File(io.BytesIO(raw), filename="qrcode.png")
                embed.set_image(url="attachment://qrcode.png")
            except Exception:
                file = None

        view = OrderStatusView(order_id)
        kwargs = {"embed": embed, "view": view}
        if file:
            kwargs["file"] = file

        if cart_channel:
            await cart_channel.send(content=interaction.user.mention, **kwargs)
            await interaction.followup.send(
                embed=success_embed("Cart channel created", f"Continue here: {cart_channel.mention}"), ephemeral=True
            )
        else:
            await interaction.followup.send(ephemeral=True, **kwargs)


class OrderStatusView(discord.ui.View):
    def __init__(self, order_id: int):
        super().__init__(timeout=None)
        self.order_id = order_id

    @discord.ui.button(label="I've paid - check now", style=discord.ButtonStyle.primary)
    async def check_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        rl_key = f"user:{interaction.user.id}:checkstatus"
        if not rate_limiter.allow(rl_key, max_hits=6, window_seconds=60):
            await interaction.response.send_message(
                embed=warning_embed("Slow down", "Please wait a few seconds before checking again."), ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        order = await db.get_order(self.order_id)
        if order is None:
            await interaction.followup.send(embed=error_embed("Order not found"), ephemeral=True)
            return
        if order["user_id"] != interaction.user.id:
            await interaction.followup.send(embed=error_embed("This is not your order"), ephemeral=True)
            return
        if order["status"] == "paid" and order["delivered_at"]:
            await interaction.followup.send(embed=success_embed("Already confirmed", "Check your DMs, or use /resend if you missed it."), ephemeral=True)
            return

        gateway = await get_gateway_instance(order["guild_id"], order["gateway_key"])
        if gateway is None or not order["external_id"]:
            await interaction.followup.send(embed=warning_embed("Still pending", "Payment not confirmed yet."), ephemeral=True)
            return

        status = await gateway.check_status(order["external_id"])
        if status == PaymentStatus.PAID:
            ok = await confirm_and_deliver(interaction.client, self.order_id)
            if ok:
                await interaction.followup.send(embed=success_embed("Payment confirmed!", "Check your DMs (use /resend if you don't see it)."), ephemeral=True)
            else:
                await interaction.followup.send(embed=warning_embed("Payment confirmed", "Delivery is being processed, staff has been notified."), ephemeral=True)
        else:
            await interaction.followup.send(embed=warning_embed("Still pending", f"Current status: {status.value}"), ephemeral=True)

    @discord.ui.button(label="Resend my item", style=discord.ButtonStyle.secondary)
    async def resend(self, interaction: discord.Interaction, button: discord.ui.Button):
        order = await db.get_order(self.order_id)
        if order is None or order["user_id"] != interaction.user.id:
            await interaction.response.send_message(embed=error_embed("This is not your order"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await resend_delivery(interaction.client, self.order_id)
        if ok:
            await interaction.followup.send(embed=success_embed("Resent", message), ephemeral=True)
        else:
            await interaction.followup.send(embed=error_embed("Could not resend", message), ephemeral=True)


class StoreCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="panel", description="Post (or refresh) the buy panel in this server's configured channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction):
        settings_row = await db.get_guild_settings(interaction.guild_id)
        if not settings_row or not settings_row["panel_channel_id"]:
            await interaction.response.send_message(
                embed=error_embed("Not configured", "Run /setup first to pick a panel channel."), ephemeral=True
            )
            return
        channel = interaction.guild.get_channel(settings_row["panel_channel_id"])
        if channel is None:
            await interaction.response.send_message(embed=error_embed("Channel not found"), ephemeral=True)
            return
        embed = base_embed(
            f"{E.get('dvstar', '⭐')} {BOT_NAME}",
            f"Click **Buy** below to see our products and pay with your preferred method.\n"
            f"{E.get('dvshield', '🛡️')} Secure checkout, instant automatic delivery.\n\n"
            f"{E.get('dvwarn', '⚠️')} {DM_WARNING}",
        )
        await channel.send(embed=embed, view=BuyPanelView())
        await interaction.response.send_message(embed=success_embed("Panel posted", channel.mention), ephemeral=True)

    @app_commands.command(name="resend", description="Get the exact item(s) you already paid for resent to your DMs")
    async def resend_cmd(self, interaction: discord.Interaction):
        orders = await db.list_user_orders(interaction.user.id, limit=20)
        if not orders:
            await interaction.response.send_message(
                embed=error_embed("No past purchases found", "You don't have any delivered orders yet."), ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=base_embed(f"{E.get('dvgift', '🎁')} Your purchases", "Select an order to resend its content to your DMs."),
            view=ResendOrderView(orders),
            ephemeral=True,
        )


class ResendOrderView(discord.ui.View):
    def __init__(self, orders):
        super().__init__(timeout=120)
        self.add_item(ResendOrderSelect(orders))


class ResendOrderSelect(discord.ui.Select):
    def __init__(self, orders):
        options = []
        for o in orders[:25]:
            options.append(discord.SelectOption(
                label=f"Order #{o['id']} - {format_money(o['amount'], o['currency'])}"[:100],
                value=str(o["id"]),
            ))
        super().__init__(placeholder="Select a past order...", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        order_id = int(self.values[0])
        order = await db.get_order(order_id)
        if not order or order["user_id"] != interaction.user.id:
            await interaction.response.send_message(embed=error_embed("Not your order"), ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=base_embed(f"Order #{order_id}", "Click below to resend the exact content you were delivered."),
            view=ResendConfirmView(order_id),
        )


class ResendConfirmView(discord.ui.View):
    def __init__(self, order_id: int):
        super().__init__(timeout=120)
        self.order_id = order_id

    @discord.ui.button(label="Send content back to my DMs", style=discord.ButtonStyle.success, emoji="🎁")
    async def send_back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await resend_delivery(interaction.client, self.order_id)
        if ok:
            await interaction.followup.send(embed=success_embed("Resent", message), ephemeral=True)
        else:
            await interaction.followup.send(embed=error_embed("Could not resend", message), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(StoreCog(bot))
    bot.add_view(BuyPanelView())
