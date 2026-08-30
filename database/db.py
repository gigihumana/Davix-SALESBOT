"""
Async SQLite data layer for Davix Sales Bot.

Every query uses parameter placeholders (never string formatting) to
eliminate SQL injection risk, and the connection runs with
`PRAGMA foreign_keys = ON` plus WAL mode for safe concurrent access.
"""
from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import aiosqlite

from config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id        INTEGER PRIMARY KEY,
    panel_channel_id INTEGER,
    log_channel_id  INTEGER,
    audit_channel_id INTEGER,
    staff_role_id   INTEGER,
    currency_default TEXT DEFAULT 'BRL',
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    price           REAL NOT NULL,
    currency        TEXT NOT NULL DEFAULT 'BRL',
    active          INTEGER NOT NULL DEFAULT 1,
    delivery_type   TEXT NOT NULL DEFAULT 'stock',  -- 'stock' or 'role' or 'manual'
    role_id         INTEGER,
    cart_category_id INTEGER,     -- Discord category where a private cart channel is created
    file_path       TEXT,          -- template file attached to every delivery of this product
    file_name       TEXT,
    low_stock_alert INTEGER NOT NULL DEFAULT 3,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS stock_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id      INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    content         TEXT NOT NULL,      -- encrypted with the master key
    delivered       INTEGER NOT NULL DEFAULT 0,
    order_id        INTEGER,
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS gateway_configs (
    guild_id        INTEGER NOT NULL,
    gateway_key     TEXT NOT NULL,
    enabled         INTEGER NOT NULL DEFAULT 0,
    credentials     TEXT NOT NULL DEFAULT '{}',   -- JSON, values individually encrypted
    extra           TEXT NOT NULL DEFAULT '{}',
    updated_at      INTEGER NOT NULL,
    PRIMARY KEY (guild_id, gateway_key)
);

CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    product_id      INTEGER NOT NULL,
    gateway_key     TEXT NOT NULL,
    external_id     TEXT,
    idempotency_key TEXT UNIQUE,
    amount          REAL NOT NULL,
    currency        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending, paid, expired, cancelled, refunded, error
    payment_payload TEXT NOT NULL DEFAULT '{}',
    cart_channel_id INTEGER,
    delivered_content TEXT,       -- the EXACT stock line delivered (encrypted). Resend reuses this, never re-claims stock.
    delivered_file_path TEXT,     -- the EXACT copy of the file delivered for this order
    delivered_file_name TEXT,
    dm_failed       INTEGER NOT NULL DEFAULT 0,
    coupon_code     TEXT,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    delivered_at    INTEGER
);

CREATE TABLE IF NOT EXISTS blacklist (
    guild_id        INTEGER NOT NULL,
    user_id         INTEGER NOT NULL,
    reason          TEXT DEFAULT '',
    created_at      INTEGER NOT NULL,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS coupons (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    code            TEXT NOT NULL,
    percent_off     REAL,
    amount_off      REAL,
    product_id      INTEGER,          -- NULL = applies to any product
    max_uses        INTEGER NOT NULL DEFAULT 0,  -- 0 = unlimited
    used_count      INTEGER NOT NULL DEFAULT 0,
    expires_at      INTEGER,
    active          INTEGER NOT NULL DEFAULT 1,
    created_at      INTEGER NOT NULL,
    UNIQUE(guild_id, code)
);

CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id        INTEGER NOT NULL,
    actor_id        INTEGER NOT NULL,
    action          TEXT NOT NULL,
    details         TEXT NOT NULL DEFAULT '',
    created_at      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS staff_roles (
    guild_id        INTEGER NOT NULL,
    role_id         INTEGER NOT NULL,
    created_at      INTEGER NOT NULL,
    PRIMARY KEY (guild_id, role_id)
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_guild ON orders(guild_id);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, delivered_at);
CREATE INDEX IF NOT EXISTS idx_stock_product ON stock_items(product_id, delivered);
CREATE INDEX IF NOT EXISTS idx_audit_guild ON audit_log(guild_id, created_at);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        self._conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode = WAL;")
        await self._conn.execute("PRAGMA foreign_keys = ON;")
        await self._conn.executescript(SCHEMA)
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected yet.")
        return self._conn

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[aiosqlite.Connection]:
        """A tiny transaction helper - commits on success, rolls back on error."""
        try:
            yield self.conn
            await self.conn.commit()
        except Exception:
            await self.conn.rollback()
            raise

    # ---------- guild settings ----------
    async def ensure_guild(self, guild_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO guild_settings (guild_id, created_at) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO NOTHING",
            (guild_id, int(time.time())),
        )
        await self.conn.commit()

    async def set_panel_channel(self, guild_id: int, channel_id: int) -> None:
        await self.ensure_guild(guild_id)
        await self.conn.execute(
            "UPDATE guild_settings SET panel_channel_id = ? WHERE guild_id = ?",
            (channel_id, guild_id),
        )
        await self.conn.commit()

    async def set_log_channel(self, guild_id: int, channel_id: int) -> None:
        await self.ensure_guild(guild_id)
        await self.conn.execute(
            "UPDATE guild_settings SET log_channel_id = ? WHERE guild_id = ?",
            (channel_id, guild_id),
        )
        await self.conn.commit()

    async def set_audit_channel(self, guild_id: int, channel_id: int) -> None:
        await self.ensure_guild(guild_id)
        await self.conn.execute(
            "UPDATE guild_settings SET audit_channel_id = ? WHERE guild_id = ?",
            (channel_id, guild_id),
        )
        await self.conn.commit()

    async def get_guild_settings(self, guild_id: int) -> Optional[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,)
        )
        return await cur.fetchone()

    # ---------- staff roles ----------
    async def add_staff_role(self, guild_id: int, role_id: int) -> None:
        await self.conn.execute(
            "INSERT INTO staff_roles (guild_id, role_id, created_at) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, role_id) DO NOTHING",
            (guild_id, role_id, int(time.time())),
        )
        await self.conn.commit()

    async def remove_staff_role(self, guild_id: int, role_id: int) -> None:
        await self.conn.execute(
            "DELETE FROM staff_roles WHERE guild_id = ? AND role_id = ?", (guild_id, role_id)
        )
        await self.conn.commit()

    async def list_staff_roles(self, guild_id: int) -> list[int]:
        cur = await self.conn.execute(
            "SELECT role_id FROM staff_roles WHERE guild_id = ?", (guild_id,)
        )
        rows = await cur.fetchall()
        return [r["role_id"] for r in rows]

    # ---------- audit log ----------
    async def log_action(self, guild_id: int, actor_id: int, action: str, details: str = "") -> None:
        await self.conn.execute(
            "INSERT INTO audit_log (guild_id, actor_id, action, details, created_at) VALUES (?, ?, ?, ?, ?)",
            (guild_id, actor_id, action, details, int(time.time())),
        )
        await self.conn.commit()

    async def recent_audit(self, guild_id: int, limit: int = 15):
        cur = await self.conn.execute(
            "SELECT * FROM audit_log WHERE guild_id = ? ORDER BY id DESC LIMIT ?",
            (guild_id, limit),
        )
        return await cur.fetchall()

    # ---------- products ----------
    async def add_product(
        self, guild_id: int, name: str, description: str, price: float,
        currency: str, delivery_type: str = "stock", role_id: int | None = None,
        cart_category_id: int | None = None, low_stock_alert: int = 3,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO products (guild_id, name, description, price, currency, "
            "delivery_type, role_id, cart_category_id, low_stock_alert, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, name.strip(), description.strip(), price, currency.upper(),
             delivery_type, role_id, cart_category_id, low_stock_alert, int(time.time())),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def set_product_cart_category(self, product_id: int, category_id: int | None) -> None:
        await self.conn.execute(
            "UPDATE products SET cart_category_id = ? WHERE id = ?", (category_id, product_id)
        )
        await self.conn.commit()

    async def set_product_file(self, product_id: int, file_path: str | None, file_name: str | None) -> None:
        await self.conn.execute(
            "UPDATE products SET file_path = ?, file_name = ? WHERE id = ?",
            (file_path, file_name, product_id),
        )
        await self.conn.commit()

    async def set_product_low_stock_alert(self, product_id: int, threshold: int) -> None:
        await self.conn.execute(
            "UPDATE products SET low_stock_alert = ? WHERE id = ?", (threshold, product_id)
        )
        await self.conn.commit()

    async def edit_product(
        self, product_id: int, name: str | None = None, description: str | None = None,
        price: float | None = None,
    ) -> None:
        fields, params = [], []
        if name is not None:
            fields.append("name = ?")
            params.append(name.strip())
        if description is not None:
            fields.append("description = ?")
            params.append(description.strip())
        if price is not None:
            fields.append("price = ?")
            params.append(price)
        if not fields:
            return
        params.append(product_id)
        await self.conn.execute(f"UPDATE products SET {', '.join(fields)} WHERE id = ?", params)
        await self.conn.commit()

    async def list_products(self, guild_id: int, active_only: bool = True):
        q = "SELECT * FROM products WHERE guild_id = ?"
        params: list[Any] = [guild_id]
        if active_only:
            q += " AND active = 1"
        q += " ORDER BY id ASC"
        cur = await self.conn.execute(q, params)
        return await cur.fetchall()

    async def get_product(self, product_id: int) -> Optional[aiosqlite.Row]:
        cur = await self.conn.execute("SELECT * FROM products WHERE id = ?", (product_id,))
        return await cur.fetchone()

    async def set_product_active(self, product_id: int, active: bool) -> None:
        await self.conn.execute(
            "UPDATE products SET active = ? WHERE id = ?", (1 if active else 0, product_id)
        )
        await self.conn.commit()

    async def delete_product(self, product_id: int) -> None:
        await self.conn.execute("DELETE FROM products WHERE id = ?", (product_id,))
        await self.conn.commit()

    async def stock_count(self, product_id: int) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) FROM stock_items WHERE product_id = ? AND delivered = 0",
            (product_id,),
        )
        row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def add_stock_bulk(self, product_id: int, lines: list[str]) -> int:
        from utils.security import vault
        now = int(time.time())
        rows = []
        for line in lines:
            if not line.strip():
                continue
            content = vault.encrypt(line.strip()) if vault else line.strip()
            rows.append((product_id, content, now))
        await self.conn.executemany(
            "INSERT INTO stock_items (product_id, content, created_at) VALUES (?, ?, ?)",
            rows,
        )
        await self.conn.commit()
        return len(rows)

    async def claim_stock_item(self, product_id: int, order_id: int) -> Optional[str]:
        """Atomically reserve exactly one stock item for this order, decrypted."""
        from utils.security import vault
        async with self.tx() as conn:
            cur = await conn.execute(
                "SELECT id, content FROM stock_items WHERE product_id = ? AND delivered = 0 "
                "ORDER BY id ASC LIMIT 1",
                (product_id,),
            )
            row = await cur.fetchone()
            if row is None:
                return None
            await conn.execute(
                "UPDATE stock_items SET delivered = 1, order_id = ? WHERE id = ?",
                (order_id, row["id"]),
            )
            raw = row["content"]
            return vault.decrypt(raw) if vault else raw

    # ---------- gateway configs ----------
    async def set_gateway_config(
        self, guild_id: int, gateway_key: str, enabled: bool,
        credentials: dict, extra: dict | None = None,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO gateway_configs (guild_id, gateway_key, enabled, credentials, extra, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, gateway_key) DO UPDATE SET "
            "enabled = excluded.enabled, credentials = excluded.credentials, "
            "extra = excluded.extra, updated_at = excluded.updated_at",
            (guild_id, gateway_key, 1 if enabled else 0, json.dumps(credentials),
             json.dumps(extra or {}), int(time.time())),
        )
        await self.conn.commit()

    async def get_gateway_config(self, guild_id: int, gateway_key: str) -> Optional[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM gateway_configs WHERE guild_id = ? AND gateway_key = ?",
            (guild_id, gateway_key),
        )
        row = await cur.fetchone()
        if not row:
            return None
        return {
            "guild_id": row["guild_id"],
            "gateway_key": row["gateway_key"],
            "enabled": bool(row["enabled"]),
            "credentials": json.loads(row["credentials"]),
            "extra": json.loads(row["extra"]),
        }

    async def list_enabled_gateways(self, guild_id: int) -> list[dict]:
        cur = await self.conn.execute(
            "SELECT * FROM gateway_configs WHERE guild_id = ? AND enabled = 1", (guild_id,)
        )
        rows = await cur.fetchall()
        return [
            {
                "gateway_key": r["gateway_key"],
                "credentials": json.loads(r["credentials"]),
                "extra": json.loads(r["extra"]),
            }
            for r in rows
        ]

    # ---------- orders ----------
    async def create_order(
        self, guild_id: int, user_id: int, product_id: int, gateway_key: str,
        amount: float, currency: str, idempotency_key: str,
    ) -> int:
        now = int(time.time())
        cur = await self.conn.execute(
            "INSERT INTO orders (guild_id, user_id, product_id, gateway_key, amount, "
            "currency, idempotency_key, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, user_id, product_id, gateway_key, amount, currency,
             idempotency_key, now, now),
        )
        await self.conn.commit()
        return cur.lastrowid

    async def set_order_external(self, order_id: int, external_id: str, payload: dict) -> None:
        await self.conn.execute(
            "UPDATE orders SET external_id = ?, payment_payload = ?, updated_at = ? WHERE id = ?",
            (external_id, json.dumps(payload), int(time.time()), order_id),
        )
        await self.conn.commit()

    async def set_order_status(self, order_id: int, status: str) -> None:
        await self.conn.execute(
            "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
            (status, int(time.time()), order_id),
        )
        await self.conn.commit()

    async def set_order_cart_channel(self, order_id: int, channel_id: int) -> None:
        await self.conn.execute(
            "UPDATE orders SET cart_channel_id = ? WHERE id = ?", (channel_id, order_id)
        )
        await self.conn.commit()

    async def set_order_coupon(self, order_id: int, code: str) -> None:
        await self.conn.execute(
            "UPDATE orders SET coupon_code = ? WHERE id = ?", (code, order_id)
        )
        await self.conn.commit()

    async def save_delivered_content(
        self, order_id: int, content: str | None, file_path: str | None, file_name: str | None,
    ) -> None:
        """Persists the EXACT thing handed to the buyer, encrypted, so /resend
        (and staff redeliveries) always replay this same content - never a
        freshly claimed stock line."""
        from utils.security import vault
        enc_content = vault.encrypt(content) if (content and vault) else content
        await self.conn.execute(
            "UPDATE orders SET delivered_content = ?, delivered_file_path = ?, "
            "delivered_file_name = ? WHERE id = ?",
            (enc_content, file_path, file_name, order_id),
        )
        await self.conn.commit()

    async def get_delivered_content(self, order_id: int) -> tuple[str | None, str | None, str | None]:
        from utils.security import vault
        cur = await self.conn.execute(
            "SELECT delivered_content, delivered_file_path, delivered_file_name FROM orders WHERE id = ?",
            (order_id,),
        )
        row = await cur.fetchone()
        if not row:
            return None, None, None
        content = row["delivered_content"]
        if content and vault:
            content = vault.decrypt(content)
        return content, row["delivered_file_path"], row["delivered_file_name"]

    async def set_dm_failed(self, order_id: int, failed: bool) -> None:
        await self.conn.execute(
            "UPDATE orders SET dm_failed = ? WHERE id = ?", (1 if failed else 0, order_id)
        )
        await self.conn.commit()

    async def list_user_orders(self, user_id: int, limit: int = 20):
        cur = await self.conn.execute(
            "SELECT * FROM orders WHERE user_id = ? AND status = 'paid' AND delivered_at IS NOT NULL "
            "ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        return await cur.fetchall()

    async def mark_delivered(self, order_id: int) -> None:
        await self.conn.execute(
            "UPDATE orders SET delivered_at = ? WHERE id = ?", (int(time.time()), order_id)
        )
        await self.conn.commit()

    async def get_order(self, order_id: int) -> Optional[aiosqlite.Row]:
        cur = await self.conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        return await cur.fetchone()

    async def get_order_by_external(self, gateway_key: str, external_id: str) -> Optional[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM orders WHERE gateway_key = ? AND external_id = ?",
            (gateway_key, external_id),
        )
        return await cur.fetchone()

    async def pending_orders(self, older_than_seconds: int = 0) -> list[aiosqlite.Row]:
        cutoff = int(time.time()) - older_than_seconds
        cur = await self.conn.execute(
            "SELECT * FROM orders WHERE status = 'pending' AND created_at <= ?",
            (cutoff,),
        )
        return await cur.fetchall()

    async def sales_stats(self, guild_id: int) -> dict:
        cur = await self.conn.execute(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(amount), 0) as total, currency "
            "FROM orders WHERE guild_id = ? AND status = 'paid' GROUP BY currency",
            (guild_id,),
        )
        rows = await cur.fetchall()
        return {r["currency"]: {"count": r["cnt"], "total": r["total"]} for r in rows}

    async def export_orders_csv_rows(self, guild_id: int):
        cur = await self.conn.execute(
            "SELECT id, user_id, product_id, gateway_key, amount, currency, status, created_at, delivered_at "
            "FROM orders WHERE guild_id = ? ORDER BY id ASC",
            (guild_id,),
        )
        return await cur.fetchall()

    # ---------- coupons ----------
    async def create_coupon(
        self, guild_id: int, code: str, percent_off: float | None, amount_off: float | None,
        product_id: int | None, max_uses: int, expires_at: int | None,
    ) -> None:
        await self.conn.execute(
            "INSERT INTO coupons (guild_id, code, percent_off, amount_off, product_id, "
            "max_uses, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (guild_id, code.upper().strip(), percent_off, amount_off, product_id,
             max_uses, expires_at, int(time.time())),
        )
        await self.conn.commit()

    async def get_coupon(self, guild_id: int, code: str) -> Optional[aiosqlite.Row]:
        cur = await self.conn.execute(
            "SELECT * FROM coupons WHERE guild_id = ? AND code = ? AND active = 1",
            (guild_id, code.upper().strip()),
        )
        return await cur.fetchone()

    async def use_coupon(self, coupon_id: int) -> None:
        await self.conn.execute(
            "UPDATE coupons SET used_count = used_count + 1 WHERE id = ?", (coupon_id,)
        )
        await self.conn.commit()

    async def list_coupons(self, guild_id: int):
        cur = await self.conn.execute(
            "SELECT * FROM coupons WHERE guild_id = ? ORDER BY id DESC", (guild_id,)
        )
        return await cur.fetchall()

    async def delete_coupon(self, guild_id: int, code: str) -> None:
        await self.conn.execute(
            "DELETE FROM coupons WHERE guild_id = ? AND code = ?", (guild_id, code.upper().strip())
        )
        await self.conn.commit()

    # ---------- blacklist ----------
    async def is_blacklisted(self, guild_id: int, user_id: int) -> bool:
        cur = await self.conn.execute(
            "SELECT 1 FROM blacklist WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        return await cur.fetchone() is not None

    async def blacklist_add(self, guild_id: int, user_id: int, reason: str = "") -> None:
        await self.conn.execute(
            "INSERT INTO blacklist (guild_id, user_id, reason, created_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET reason = excluded.reason",
            (guild_id, user_id, reason, int(time.time())),
        )
        await self.conn.commit()

    async def blacklist_remove(self, guild_id: int, user_id: int) -> None:
        await self.conn.execute(
            "DELETE FROM blacklist WHERE guild_id = ? AND user_id = ?", (guild_id, user_id)
        )
        await self.conn.commit()


db = Database(settings.database_path)
