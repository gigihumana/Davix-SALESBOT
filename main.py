"""
Davix Sales Bot - entrypoint.

Run with:  python3 main.py
"""
from __future__ import annotations

import asyncio

import discord
from discord.ext import commands

from config import BOT_NAME, BOT_VERSION, ensure_ready_or_exit, settings
from database.db import db
from services.payment_service import poll_pending_orders
from services.webhook_server import start_webhook_server
from utils.emoji_manager import emoji_manager, ensure_bot_icon
from utils.logger import log

INTENTS = discord.Intents.default()
INTENTS.message_content = False  # not needed, everything is slash commands / components

EXTENSIONS = ("cogs.admin", "cogs.store", "cogs.orders", "cogs.misc")


class DavixSalesBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=commands.when_mentioned, intents=INTENTS)
        self._webhook_runner = None

    async def setup_hook(self) -> None:
        await db.connect()
        for ext in EXTENSIONS:
            await self.load_extension(ext)
            log.info(f"Loaded extension {ext}")

        app_id = settings.application_id or (self.application_id if self.application else None)
        if app_id:
            try:
                await emoji_manager.ensure_all(self, app_id)
            except Exception as exc:
                log.warning(f"Emoji setup skipped: {exc}")
        else:
            log.warning("DISCORD_APPLICATION_ID not set - skipping custom emoji creation.")

        await self.tree.sync()
        log.info("Slash commands synced.")

        self.loop.create_task(poll_pending_orders(self))

        if settings.webhook_enabled:
            self._webhook_runner = await start_webhook_server(self)

    async def on_ready(self):
        log.info(f"{BOT_NAME} v{BOT_VERSION} is online as {self.user} (id: {self.user.id})")
        await ensure_bot_icon(self)
        await self.change_presence(activity=discord.Game(name="Managing sales | /panel"))

    async def close(self):
        if self._webhook_runner:
            await self._webhook_runner.cleanup()
        await db.close()
        await super().close()


async def main():
    ensure_ready_or_exit()
    bot = DavixSalesBot()
    async with bot:
        await bot.start(settings.discord_token)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down.")
