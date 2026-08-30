from __future__ import annotations

import time

import discord
from discord import app_commands
from discord.ext import commands

from config import BOT_NAME, BOT_VERSION
from gateways.registry import all_gateways
from utils.embeds import base_embed
from utils.emoji_manager import emoji_manager as E


class MiscCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="ping", description="Check the bot's latency")
    async def ping(self, interaction: discord.Interaction):
        start = time.perf_counter()
        await interaction.response.send_message(embed=base_embed("Pinging..."), ephemeral=True)
        elapsed_ms = (time.perf_counter() - start) * 1000
        await interaction.edit_original_response(
            embed=base_embed(f"{E.get('dvcheck', '✅')} Pong!", f"Gateway latency: {self.bot.latency * 1000:.0f}ms\nRound-trip: {elapsed_ms:.0f}ms")
        )

    @app_commands.command(name="about", description="About Davix Sales Bot")
    async def about(self, interaction: discord.Interaction):
        gateway_names = ", ".join(cls.display_name for cls in all_gateways())
        embed = base_embed(
            f"{E.get('dvshield', '🛡️')} {BOT_NAME} v{BOT_VERSION}",
            "A secure, extensible Discord sales bot with automatic multi-gateway checkout, "
            "private cart channels, encrypted stock delivery, coupons, and staff tooling.",
        )
        embed.add_field(name="Supported payment gateways", value=gateway_names, inline=False)
        embed.add_field(name="Documentation", value="See README.md and SECURITY.md in the project files.", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="help", description="How to buy something in this server")
    async def help_cmd(self, interaction: discord.Interaction):
        embed = base_embed(
            f"{E.get('dvcart', '🛒')} How to buy",
            "1. Click the **Buy** button on the store panel.\n"
            "2. Pick a product, then optionally apply a coupon code.\n"
            "3. Choose a payment method and follow the instructions "
            "(Pix QR code, checkout link, etc.).\n"
            f"4. {E.get('dvwarn', '⚠️')} Make sure your DMs are open - that's where your item is delivered.\n"
            "5. Once you've paid, click **I've paid - check now**, or just wait - "
            "payments are checked automatically.\n"
            "6. Didn't get your item, or lost it? Run `/resend` any time to get the "
            "exact same content sent again.",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(MiscCog(bot))
