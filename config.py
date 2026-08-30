"""
Davix Sales Bot - central configuration.

Loads and validates environment variables once, at import time, so every
other module can simply `from config import settings`.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _get_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except ValueError:
        return default


def _get_id_list(name: str) -> list[int]:
    raw = os.getenv(name, "")
    out: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            out.append(int(chunk))
    return out


@dataclass
class Settings:
    discord_token: str
    application_id: int | None
    owner_ids: list[int] = field(default_factory=list)
    master_key: str = ""
    webhook_enabled: bool = False
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8787
    webhook_public_url: str = ""
    poll_interval: int = 15
    database_path: str = "data/davix_sales.db"
    timezone: str = "UTC"

    def validate(self) -> list[str]:
        """Return a list of human readable problems (empty = all good)."""
        problems = []
        if not self.discord_token:
            problems.append("DISCORD_TOKEN is missing from your .env file.")
        if not self.master_key:
            problems.append(
                "MASTER_ENCRYPTION_KEY is missing. Generate one with:\n"
                "  python3 -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\""
            )
        else:
            try:
                from cryptography.fernet import Fernet

                Fernet(self.master_key.encode())
            except Exception:
                problems.append(
                    "MASTER_ENCRYPTION_KEY is not a valid Fernet key. "
                    "Regenerate it with the command shown in .env.example."
                )
        return problems


def load_settings() -> Settings:
    app_id_raw = os.getenv("DISCORD_APPLICATION_ID", "").strip()
    settings = Settings(
        discord_token=os.getenv("DISCORD_TOKEN", "").strip(),
        application_id=int(app_id_raw) if app_id_raw.isdigit() else None,
        owner_ids=_get_id_list("BOT_OWNER_IDS"),
        master_key=os.getenv("MASTER_ENCRYPTION_KEY", "").strip(),
        webhook_enabled=_get_bool("WEBHOOK_ENABLED", False),
        webhook_host=os.getenv("WEBHOOK_HOST", "0.0.0.0"),
        webhook_port=_get_int("WEBHOOK_PORT", 8787),
        webhook_public_url=os.getenv("WEBHOOK_PUBLIC_URL", "").strip(),
        poll_interval=max(5, _get_int("PAYMENT_POLL_INTERVAL", 15)),
        database_path=os.getenv("DATABASE_PATH", "data/davix_sales.db"),
        timezone=os.getenv("TIMEZONE", "UTC"),
    )
    return settings


settings = load_settings()

# Brand constants
BOT_NAME = "Davix Sales Bot"
BOT_VERSION = "1.0.0"
EMBED_COLOR = 0x5865F2
EMBED_COLOR_SUCCESS = 0x57F287
EMBED_COLOR_ERROR = 0xED4245
EMBED_COLOR_WARNING = 0xFEE75C


def ensure_ready_or_exit() -> None:
    problems = settings.validate()
    if problems:
        print("=" * 60)
        print(f"{BOT_NAME} cannot start - configuration problems found:")
        for p in problems:
            print(f" - {p}")
        print("=" * 60)
        sys.exit(1)
