"""
Auto-creates Davix Sales Bot's own custom emojis directly on the bot's
Discord Application (not on any one server) the first time it starts, so
they work everywhere the bot is installed without eating a server's emoji
slots. Uses Discord's Application Emoji endpoints directly over HTTP.

The emoji GIFs and the bot icon PNG ship pre-generated inside assets/ -
nothing needs to be run or configured to produce them.

Docs: https://discord.com/developers/docs/resources/emoji#application-emoji
"""
from __future__ import annotations

import base64
import glob
import os

import aiohttp
import discord

from utils.logger import log

EMOJI_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "emojis")
API = "https://discord.com/api/v10"
MAX_EMOJI_BYTES = 256 * 1024  # Discord's application emoji upload cap


class EmojiManager:
    """Holds name -> `<:name:id>` mappings once emojis are ensured to exist."""

    def __init__(self):
        self.emojis: dict[str, str] = {}

    def get(self, name: str, fallback: str = "\u2022") -> str:
        return self.emojis.get(name, fallback)

    async def ensure_all(self, bot: discord.Client, application_id: int) -> None:
        headers = {"Authorization": f"Bot {bot.http.token}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            existing = await self._fetch_existing(session, application_id)
            files = sorted(glob.glob(os.path.join(EMOJI_DIR, "*.gif")))
            files += sorted(glob.glob(os.path.join(EMOJI_DIR, "*.png")))
            for path in files:
                name = os.path.splitext(os.path.basename(path))[0]
                if name in existing:
                    self.emojis[name] = f"<:{name}:{existing[name]}>"
                    continue
                try:
                    new_id = await self._upload(session, application_id, name, path)
                    self.emojis[name] = f"<:{name}:{new_id}>"
                    log.info(f"Created application emoji :{name}:")
                except Exception as exc:
                    log.warning(f"Could not create emoji '{name}': {exc}")

    async def _fetch_existing(self, session: aiohttp.ClientSession, app_id: int) -> dict[str, int]:
        async with session.get(f"{API}/applications/{app_id}/emojis") as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()
        return {e["name"]: int(e["id"]) for e in data.get("items", [])}

    async def _upload(
        self, session: aiohttp.ClientSession, app_id: int, name: str, path: str
    ) -> int:
        with open(path, "rb") as f:
            raw = f.read()
        if len(raw) > MAX_EMOJI_BYTES:
            raise RuntimeError(f"{os.path.basename(path)} is {len(raw)} bytes, over Discord's 256KB emoji limit")
        mime = "image/gif" if path.lower().endswith(".gif") else "image/png"
        b64 = base64.b64encode(raw).decode("ascii")
        payload = {"name": name, "image": f"data:{mime};base64,{b64}"}
        async with session.post(f"{API}/applications/{app_id}/emojis", json=payload) as resp:
            data = await resp.json()
            if resp.status not in (200, 201):
                raise RuntimeError(f"{resp.status}: {data}")
            return int(data["id"])


async def ensure_bot_icon(bot: discord.Client) -> None:
    """Uploads Davix Sales Bot's own pre-generated icon as the bot's avatar
    on first run, so the bot looks fully branded with zero manual setup.
    Safe to call every startup - skipped once the avatar is already set."""
    if bot.user and bot.user.avatar:
        return  # already has an avatar, don't overwrite something custom
    icon_path = os.path.join(os.path.dirname(__file__), "..", "assets", "icon", "davix_icon_512.png")
    if not os.path.exists(icon_path):
        return
    try:
        with open(icon_path, "rb") as f:
            await bot.user.edit(avatar=f.read())
        log.info("Uploaded Davix Sales Bot's default icon as the bot avatar.")
    except discord.HTTPException as exc:
        log.warning(f"Could not set bot avatar automatically: {exc}")


emoji_manager = EmojiManager()
