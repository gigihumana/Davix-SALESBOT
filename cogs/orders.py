from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from database.db import db
from services.payment_service import confirm_and_deliver, resend_delivery
from utils.embeds import base_embed, error_embed, format_money, success_embed


def is_staff():
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.guild_permissions.manage_guild:
            return True
        staff_role_ids = await db.list_staff_roles(interaction.guild_id)
        member_role_ids = {r.id for r in getattr(interaction.user, "roles", [])}
        if member_role_ids & set(staff_role_ids):
            return True
        raise app_commands.CheckFailure("You need the **Manage Server** permission or a staff role for this.")
    return app_commands.check(predicate)


class OrdersCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    order_group = app_commands.Group(name="order", description="Manage individual orders")

    @order_group.command(name="status", description="Check the status of an order")
    @is_staff()
    async def order_status(self, interaction: discord.Interaction, order_id: int):
        order = await db.get_order(order_id)
        if not order or order["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found"), ephemeral=True)
            return
        embed = base_embed(f"Order #{order_id}")
        embed.add_field(name="Buyer", value=f"<@{order['user_id']}>", inline=True)
        embed.add_field(name="Status", value=order["status"], inline=True)
        embed.add_field(name="Amount", value=format_money(order["amount"], order["currency"]), inline=True)
        embed.add_field(name="Gateway", value=order["gateway_key"], inline=True)
        embed.add_field(name="External ID", value=order["external_id"] or "-", inline=True)
        embed.add_field(name="Delivered", value="yes" if order["delivered_at"] else "no", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @order_group.command(name="confirm", description="Manually mark an order as paid and deliver it (e.g. manual gateway)")
    @is_staff()
    async def order_confirm(self, interaction: discord.Interaction, order_id: int):
        order = await db.get_order(order_id)
        if not order or order["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok = await confirm_and_deliver(self.bot, order_id, force=True)
        if ok:
            await interaction.followup.send(embed=success_embed("Order confirmed and delivered"), ephemeral=True)
        else:
            await interaction.followup.send(
                embed=error_embed("Could not deliver", "Check stock levels or product configuration."), ephemeral=True
            )

    @order_group.command(name="cancel", description="Cancel a pending order")
    @is_staff()
    async def order_cancel(self, interaction: discord.Interaction, order_id: int):
        order = await db.get_order(order_id)
        if not order or order["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found"), ephemeral=True)
            return
        await db.set_order_status(order_id, "cancelled")
        await db.log_action(interaction.guild_id, interaction.user.id, "order_cancel", f"#{order_id}")
        await interaction.response.send_message(embed=success_embed("Order cancelled"), ephemeral=True)

    @order_group.command(name="refund", description="Mark an order as refunded (does not call the gateway's refund API)")
    @is_staff()
    async def order_refund(self, interaction: discord.Interaction, order_id: int, reason: str = ""):
        order = await db.get_order(order_id)
        if not order or order["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found"), ephemeral=True)
            return
        await db.set_order_status(order_id, "refunded")
        await db.log_action(interaction.guild_id, interaction.user.id, "order_refund", f"#{order_id} {reason}")
        await interaction.response.send_message(
            embed=success_embed(
                "Order marked as refunded",
                "Remember to also issue the actual refund from the gateway's own dashboard.",
            ),
            ephemeral=True,
        )

    @order_group.command(name="resend", description="Resend the exact item already delivered for this order (staff)")
    @is_staff()
    async def order_resend(self, interaction: discord.Interaction, order_id: int):
        order = await db.get_order(order_id)
        if not order or order["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found"), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        ok, message = await resend_delivery(self.bot, order_id)
        if ok:
            await db.log_action(interaction.guild_id, interaction.user.id, "order_resend", f"#{order_id}")
            await interaction.followup.send(embed=success_embed("Resent", message), ephemeral=True)
        else:
            await interaction.followup.send(embed=error_embed("Could not resend", message), ephemeral=True)

    @order_group.command(name="history", description="Show a user's recent delivered orders")
    @is_staff()
    async def order_history(self, interaction: discord.Interaction, user: discord.User):
        orders = await db.list_user_orders(user.id, limit=10)
        orders = [o for o in orders if o["guild_id"] == interaction.guild_id]
        if not orders:
            await interaction.response.send_message(embed=error_embed("No orders found for this user"), ephemeral=True)
            return
        embed = base_embed(f"Order history - {user}")
        for o in orders:
            embed.add_field(name=f"#{o['id']}", value=f"{format_money(o['amount'], o['currency'])} via {o['gateway_key']}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(OrdersCog(bot))
