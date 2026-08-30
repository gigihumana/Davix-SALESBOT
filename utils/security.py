"""
Security helpers used across the bot.

- Fernet symmetric encryption for gateway credentials at rest.
- A simple in-memory sliding-window rate limiter for purchase spam.
- Constant-time helpers and webhook signature verification utilities.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict, deque

from cryptography.fernet import Fernet, InvalidToken

from config import settings


class CredentialVault:
    """Encrypts/decrypts gateway API keys before they touch the database."""

    def __init__(self, key: str):
        self._fernet = Fernet(key.encode())

    def encrypt(self, plaintext: str) -> str:
        if plaintext is None:
            plaintext = ""
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")

    def decrypt(self, token: str) -> str:
        if not token:
            return ""
        try:
            return self._fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            # Never crash the bot because of a corrupted/rotated key -
            # surface it as an empty credential so callers fail safely.
            return ""


vault = CredentialVault(settings.master_key) if settings.master_key else None


def encrypt_credentials(plain: dict) -> dict:
    """Encrypt every value in a flat credentials dict before it hits the DB."""
    if vault is None:
        raise RuntimeError("MASTER_ENCRYPTION_KEY is not configured.")
    return {k: vault.encrypt(str(v)) for k, v in plain.items()}


def decrypt_credentials(encrypted: dict) -> dict:
    if vault is None:
        raise RuntimeError("MASTER_ENCRYPTION_KEY is not configured.")
    return {k: vault.decrypt(v) for k, v in encrypted.items()}


class SlidingWindowRateLimiter:
    """Per-key rate limiter, e.g. `user:<id>:purchase` -> N actions / window."""

    def __init__(self):
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, max_hits: int, window_seconds: float) -> bool:
        now = time.monotonic()
        bucket = self._hits[key]
        while bucket and now - bucket[0] > window_seconds:
            bucket.popleft()
        if len(bucket) >= max_hits:
            return False
        bucket.append(now)
        return True

    def retry_after(self, key: str, window_seconds: float) -> float:
        bucket = self._hits.get(key)
        if not bucket:
            return 0.0
        return max(0.0, window_seconds - (time.monotonic() - bucket[0]))


rate_limiter = SlidingWindowRateLimiter()


def constant_time_equals(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def hmac_sha256_hex(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_hmac_sha256(secret: str, message: str, signature_hex: str) -> bool:
    expected = hmac_sha256_hex(secret, message)
    return constant_time_equals(expected, signature_hex.lower())


def new_idempotency_key(prefix: str = "davix") -> str:
    import secrets as _secrets

    return f"{prefix}_{int(time.time())}_{_secrets.token_hex(8)}"
