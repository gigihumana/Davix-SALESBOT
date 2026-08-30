from __future__ import annotations

import discord

from config import BOT_NAME, EMBED_COLOR, EMBED_COLOR_ERROR, EMBED_COLOR_SUCCESS, EMBED_COLOR_WARNING
from utils.emoji_manager import emoji_manager as E


def base_embed(title: str, description: str = "", color: int = EMBED_COLOR) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=BOT_NAME)
    return embed


def success_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(f"{E.get('dvcheck', '✅')} {title}", description, EMBED_COLOR_SUCCESS)


def error_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(f"{E.get('dvcross', '❌')} {title}", description, EMBED_COLOR_ERROR)


def warning_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(f"{E.get('dvwarn', '⚠️')} {title}", description, EMBED_COLOR_WARNING)


def format_money(amount: float, currency: str) -> str:
    symbols = {"USD": "$", "BRL": "R$", "EUR": "€", "GBP": "£"}
    symbol = symbols.get(currency.upper())
    if symbol:
        return f"{symbol}{amount:,.2f}"
    return f"{amount:,.2f} {currency.upper()}"
