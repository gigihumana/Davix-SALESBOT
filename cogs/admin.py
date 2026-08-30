from __future__ import annotations

import os

import discord
from discord import app_commands
from discord.ext import commands

from database.db import db
from gateways.registry import get_gateway_class, all_gateways
from utils.embeds import base_embed, error_embed, format_money, success_embed
from utils.emoji_manager import emoji_manager as E
from utils.security import encrypt_credentials


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


class GatewayCredentialsModal(discord.ui.Modal):
    def __init__(self, gateway_key: str):
        gw_cls = get_gateway_class(gateway_key)
        super().__init__(title=f"Configure {gw_cls.display_name}"[:45])
        self.gateway_key = gateway_key
        self.fields_map: dict[str, discord.ui.TextInput] = {}
        for field in gw_cls.credential_fields[:5]:  # Discord modals cap at 5 inputs
            ti = discord.ui.TextInput(
                label=field.label[:45],
                placeholder=field.placeholder[:100] if field.placeholder else None,
                required=field.required,
                style=discord.TextStyle.paragraph if field.key == "instructions" else discord.TextStyle.short,
                max_length=500,
            )
            self.fields_map[field.key] = ti
            self.add_item(ti)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        credentials_plain = {k: ti.value for k, ti in self.fields_map.items() if ti.value}
        encrypted = encrypt_credentials(credentials_plain)
        await db.set_gateway_config(
            interaction.guild_id, self.gateway_key, enabled=True, credentials=encrypted
        )
        await db.log_action(interaction.guild_id, interaction.user.id, "gateway_configure", self.gateway_key)
        gw_cls = get_gateway_class(self.gateway_key)
        await interaction.response.send_message(
            embed=success_embed(
                f"{gw_cls.display_name} configured",
                f"Accepted currencies: {', '.join(gw_cls.supported_currencies) or 'any'}",
            ),
            ephemeral=True,
        )


class GatewaySelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(label=cls.display_name, value=cls.key, description=cls.homepage[:100] or None)
            for cls in all_gateways()
        ]
        super().__init__(placeholder="Choose a payment gateway to configure...", options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(GatewayCredentialsModal(self.values[0]))


class GatewaySelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(GatewaySelect())


class AdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    product_group = app_commands.Group(name="product", description="Manage products for sale")
    gateway_group = app_commands.Group(name="gateway", description="Configure payment gateways")

    # ---------------- setup ----------------
    @app_commands.command(name="setup", description="Configure the store's panel and log channels")
    @is_staff()
    @app_commands.describe(panel_channel="Where the buy panel will be posted", log_channel="Where sales are logged")
    async def setup(
        self, interaction: discord.Interaction,
        panel_channel: discord.TextChannel, log_channel: discord.TextChannel | None = None,
    ):
        await db.set_panel_channel(interaction.guild_id, panel_channel.id)
        if log_channel:
            await db.set_log_channel(interaction.guild_id, log_channel.id)
        await db.log_action(interaction.guild_id, interaction.user.id, "setup", panel_channel.name)
        await interaction.response.send_message(
            embed=success_embed(
                "Store configured",
                f"Panel channel: {panel_channel.mention}\n"
                f"Log channel: {log_channel.mention if log_channel else 'not set'}",
            ),
            ephemeral=True,
        )

    # ---------------- products ----------------
    @product_group.command(name="add", description="Add a new product")
    @is_staff()
    @app_commands.describe(
        name="Product name", price="Price (e.g. 19.90)", currency="Currency code, e.g. USD or BRL",
        description="Short description shown to buyers", delivery="How the product is delivered",
        role="Role to grant (only if delivery = role)",
        cart_category="Category where a private cart channel is created for each purchase (optional)",
    )
    @app_commands.choices(delivery=[
        app_commands.Choice(name="Stock (text/keys/files pasted by you)", value="stock"),
        app_commands.Choice(name="Discord role", value="role"),
        app_commands.Choice(name="Manual (staff delivers by hand)", value="manual"),
    ])
    async def product_add(
        self, interaction: discord.Interaction, name: str, price: float, currency: str,
        description: str = "", delivery: app_commands.Choice[str] = None, role: discord.Role = None,
        cart_category: discord.CategoryChannel = None,
    ):
        delivery_type = delivery.value if delivery else "stock"
        if delivery_type == "role" and role is None:
            await interaction.response.send_message(
                embed=error_embed("Missing role", "Pick a role when delivery type is 'role'."),
                ephemeral=True,
            )
            return
        product_id = await db.add_product(
            interaction.guild_id, name, description, price, currency.upper(),
            delivery_type, role.id if role else None, cart_category.id if cart_category else None,
        )
        await db.log_action(interaction.guild_id, interaction.user.id, "product_add", f"#{product_id} {name}")
        await interaction.response.send_message(
            embed=success_embed("Product added", f"**{name}** (#{product_id}) - {format_money(price, currency)}"),
            ephemeral=True,
        )

    @product_group.command(name="set-cart-category", description="Set/clear the category where this product's private cart channel is created")
    @is_staff()
    async def product_set_cart_category(self, interaction: discord.Interaction, product_id: int, category: discord.CategoryChannel = None):
        product = await db.get_product(product_id)
        if not product or product["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found"), ephemeral=True)
            return
        await db.set_product_cart_category(product_id, category.id if category else None)
        await interaction.response.send_message(
            embed=success_embed("Updated", f"Cart category set to {category.mention if category else 'none (ephemeral checkout)'}"),
            ephemeral=True,
        )

    @product_group.command(name="set-file", description="Attach a file that is sent in the buyer's DM after payment")
    @is_staff()
    @app_commands.describe(file="Upload the file to deliver, or leave empty to clear it")
    async def product_set_file(self, interaction: discord.Interaction, product_id: int, file: discord.Attachment = None):
        product = await db.get_product(product_id)
        if not product or product["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found"), ephemeral=True)
            return
        if file is None:
            await db.set_product_file(product_id, None, None)
            await interaction.response.send_message(embed=success_embed("File cleared"), ephemeral=True)
            return
        if file.size > 8 * 1024 * 1024:
            await interaction.response.send_message(embed=error_embed("Too large", "Please keep delivery files under 8 MB."), ephemeral=True)
            return
        import os
        directory = os.path.join("data", "product_files", str(product_id))
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, file.filename)
        await file.save(path)
        await db.set_product_file(product_id, path, file.filename)
        await db.log_action(interaction.guild_id, interaction.user.id, "product_set_file", f"#{product_id} {file.filename}")
        await interaction.response.send_message(embed=success_embed("File attached", file.filename), ephemeral=True)

    @product_group.command(name="set-lowstock", description="Set the stock threshold that triggers a low-stock alert")
    @is_staff()
    async def product_set_lowstock(self, interaction: discord.Interaction, product_id: int, threshold: int):
        product = await db.get_product(product_id)
        if not product or product["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found"), ephemeral=True)
            return
        await db.set_product_low_stock_alert(product_id, max(0, threshold))
        await interaction.response.send_message(embed=success_embed("Updated", f"Low stock alert set to {threshold}"), ephemeral=True)

    @product_group.command(name="stock-add", description="Add stock lines to a product (one item per line)")
    @is_staff()
    @app_commands.describe(product_id="Product ID", items="One item per line (keys, accounts, links...)")
    async def product_stock_add(self, interaction: discord.Interaction, product_id: int, items: str):
        product = await db.get_product(product_id)
        if not product or product["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found", "No such product."), ephemeral=True)
            return
        lines = [l for l in items.splitlines() if l.strip()]
        count = await db.add_stock_bulk(product_id, lines)
        await interaction.response.send_message(
            embed=success_embed("Stock added", f"Added {count} item(s) to **{product['name']}**."),
            ephemeral=True,
        )

    @product_group.command(name="list", description="List all products")
    @is_staff()
    async def product_list(self, interaction: discord.Interaction):
        products = await db.list_products(interaction.guild_id, active_only=False)
        if not products:
            await interaction.response.send_message(embed=error_embed("No products", "Add one with /product add."), ephemeral=True)
            return
        embed = base_embed(f"{E.get('dvcart', '🛒')} Products")
        for p in products:
            stock = await db.stock_count(p["id"]) if p["delivery_type"] == "stock" else "-"
            status = "Active" if p["active"] else "Disabled"
            embed.add_field(
                name=f"#{p['id']} - {p['name']} ({status})",
                value=f"{format_money(p['price'], p['currency'])} | stock: {stock} | delivery: {p['delivery_type']}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @product_group.command(name="info", description="Show full details for a product")
    @is_staff()
    async def product_info(self, interaction: discord.Interaction, product_id: int):
        product = await db.get_product(product_id)
        if not product or product["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found"), ephemeral=True)
            return
        embed = base_embed(f"{E.get('dvcart', '🛒')} {product['name']} (#{product['id']})", product["description"] or "-")
        embed.add_field(name="Price", value=format_money(product["price"], product["currency"]), inline=True)
        embed.add_field(name="Status", value="Active" if product["active"] else "Disabled", inline=True)
        embed.add_field(name="Delivery type", value=product["delivery_type"], inline=True)
        if product["delivery_type"] == "stock":
            embed.add_field(name="Stock left", value=str(await db.stock_count(product_id)), inline=True)
            embed.add_field(name="Low stock alert", value=str(product["low_stock_alert"]), inline=True)
        if product["role_id"]:
            embed.add_field(name="Role granted", value=f"<@&{product['role_id']}>", inline=True)
        if product["cart_category_id"]:
            embed.add_field(name="Cart category", value=f"<#{product['cart_category_id']}>", inline=True)
        if product["file_name"]:
            embed.add_field(name="Attached file", value=product["file_name"], inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @product_group.command(name="edit", description="Edit a product's name, description or price")
    @is_staff()
    async def product_edit(
        self, interaction: discord.Interaction, product_id: int,
        name: str = None, description: str = None, price: float = None,
    ):
        product = await db.get_product(product_id)
        if not product or product["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found"), ephemeral=True)
            return
        if name is None and description is None and price is None:
            await interaction.response.send_message(embed=error_embed("Nothing to change", "Provide at least one field."), ephemeral=True)
            return
        await db.edit_product(product_id, name=name, description=description, price=price)
        await db.log_action(interaction.guild_id, interaction.user.id, "product_edit", f"#{product_id}")
        await interaction.response.send_message(embed=success_embed("Product updated", f"#{product_id}"), ephemeral=True)

    @product_group.command(name="remove", description="Delete a product permanently")
    @is_staff()
    async def product_remove(self, interaction: discord.Interaction, product_id: int):
        product = await db.get_product(product_id)
        if not product or product["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found", "No such product."), ephemeral=True)
            return
        await db.delete_product(product_id)
        await interaction.response.send_message(embed=success_embed("Product removed", product["name"]), ephemeral=True)

    @product_group.command(name="toggle", description="Enable or disable a product")
    @is_staff()
    async def product_toggle(self, interaction: discord.Interaction, product_id: int, active: bool):
        product = await db.get_product(product_id)
        if not product or product["guild_id"] != interaction.guild_id:
            await interaction.response.send_message(embed=error_embed("Not found", "No such product."), ephemeral=True)
            return
        await db.set_product_active(product_id, active)
        await interaction.response.send_message(embed=success_embed("Updated", f"{product['name']} is now {'active' if active else 'disabled'}."), ephemeral=True)

    # ---------------- gateways ----------------
    @gateway_group.command(name="setup", description="Pick a gateway and enter its credentials")
    @is_staff()
    async def gateway_setup(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=base_embed("Select a payment gateway", "Your credentials are encrypted before being stored."),
            view=GatewaySelectView(),
            ephemeral=True,
        )

    @gateway_group.command(name="disable", description="Disable a configured gateway")
    @is_staff()
    async def gateway_disable(self, interaction: discord.Interaction, gateway: str):
        cfg = await db.get_gateway_config(interaction.guild_id, gateway)
        if not cfg:
            await interaction.response.send_message(embed=error_embed("Not configured", "That gateway was never set up."), ephemeral=True)
            return
        await db.set_gateway_config(interaction.guild_id, gateway, enabled=False, credentials=cfg["credentials"], extra=cfg["extra"])
        await interaction.response.send_message(embed=success_embed("Gateway disabled", gateway), ephemeral=True)

    @gateway_group.command(name="list", description="List gateways enabled in this server")
    @is_staff()
    async def gateway_list(self, interaction: discord.Interaction):
        enabled = await db.list_enabled_gateways(interaction.guild_id)
        if not enabled:
            await interaction.response.send_message(embed=error_embed("None enabled", "Use /gateway setup first."), ephemeral=True)
            return
        embed = base_embed(f"{E.get('dvshield', '🛡️')} Enabled gateways")
        for g in enabled:
            cls = get_gateway_class(g["gateway_key"])
            embed.add_field(name=cls.display_name if cls else g["gateway_key"], value=", ".join(cls.supported_currencies) if cls else "-", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @gateway_group.command(name="test", description="Verify a configured gateway's credentials actually work")
    @is_staff()
    async def gateway_test(self, interaction: discord.Interaction, gateway: str):
        from services.payment_service import get_gateway_instance
        gw = await get_gateway_instance(interaction.guild_id, gateway)
        if gw is None:
            await interaction.response.send_message(embed=error_embed("Not configured", "Set it up with /gateway setup first."), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        currency = gw.supported_currencies[0] if gw.supported_currencies else "USD"
        try:
            charge = await gw.create_charge(
                amount=1.00, currency=currency, description="Davix Sales Bot credential test",
                order_id=0, idempotency_key="test-" + os.urandom(4).hex(), buyer_id=interaction.user.id,
            )
            await interaction.followup.send(
                embed=success_embed(
                    "Credentials look valid",
                    f"Created a test R$/US$1.00 charge successfully (id: `{charge.external_id}`). "
                    f"This charge was never sent to a buyer and can be safely ignored/left unpaid.",
                ),
                ephemeral=True,
            )
        except Exception as exc:
            await interaction.followup.send(
                embed=error_embed("Credentials rejected", f"The gateway returned an error: {exc}"), ephemeral=True
            )

    # ---------------- coupons ----------------
    coupon_group = app_commands.Group(name="coupon", description="Manage discount coupons")

    @coupon_group.command(name="create", description="Create a discount coupon")
    @is_staff()
    @app_commands.describe(
        code="Coupon code buyers will type", percent_off="Percent discount, e.g. 10 for 10%",
        amount_off="Fixed amount discount instead of percent", product="Limit to one product (optional)",
        max_uses="Max number of uses, 0 = unlimited", expires_in_days="Expires after N days (optional)",
    )
    async def coupon_create(
        self, interaction: discord.Interaction, code: str, percent_off: float = None,
        amount_off: float = None, product: int = None, max_uses: int = 0, expires_in_days: int = None,
    ):
        if not percent_off and not amount_off:
            await interaction.response.send_message(embed=error_embed("Missing discount", "Set percent_off or amount_off."), ephemeral=True)
            return
        import time
        expires_at = int(time.time()) + expires_in_days * 86400 if expires_in_days else None
        await db.create_coupon(interaction.guild_id, code, percent_off, amount_off, product, max_uses, expires_at)
        await db.log_action(interaction.guild_id, interaction.user.id, "coupon_create", code.upper())
        await interaction.response.send_message(embed=success_embed("Coupon created", code.upper()), ephemeral=True)

    @coupon_group.command(name="delete", description="Delete a coupon")
    @is_staff()
    async def coupon_delete(self, interaction: discord.Interaction, code: str):
        await db.delete_coupon(interaction.guild_id, code)
        await db.log_action(interaction.guild_id, interaction.user.id, "coupon_delete", code.upper())
        await interaction.response.send_message(embed=success_embed("Coupon deleted", code.upper()), ephemeral=True)

    @coupon_group.command(name="list", description="List all coupons")
    @is_staff()
    async def coupon_list(self, interaction: discord.Interaction):
        coupons = await db.list_coupons(interaction.guild_id)
        if not coupons:
            await interaction.response.send_message(embed=error_embed("No coupons"), ephemeral=True)
            return
        embed = base_embed(f"{E.get('dvstar', '⭐')} Coupons")
        for c in coupons:
            discount = f"{c['percent_off']}%" if c["percent_off"] else format_money(c["amount_off"], "")
            uses = f"{c['used_count']}/{c['max_uses'] or '∞'}"
            embed.add_field(name=c["code"], value=f"{discount} off | used {uses} | {'active' if c['active'] else 'inactive'}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------------- staff roles ----------------
    staff_group = app_commands.Group(name="staff", description="Manage which roles count as staff")

    @staff_group.command(name="add", description="Grant a role staff access (sees cart channels, gets pinged, etc.)")
    @is_staff()
    async def staff_add(self, interaction: discord.Interaction, role: discord.Role):
        await db.add_staff_role(interaction.guild_id, role.id)
        await db.log_action(interaction.guild_id, interaction.user.id, "staff_add", role.name)
        await interaction.response.send_message(embed=success_embed("Staff role added", role.mention), ephemeral=True)

    @staff_group.command(name="remove", description="Remove a role's staff access")
    @is_staff()
    async def staff_remove(self, interaction: discord.Interaction, role: discord.Role):
        await db.remove_staff_role(interaction.guild_id, role.id)
        await db.log_action(interaction.guild_id, interaction.user.id, "staff_remove", role.name)
        await interaction.response.send_message(embed=success_embed("Staff role removed", role.mention), ephemeral=True)

    @staff_group.command(name="list", description="List staff roles")
    @is_staff()
    async def staff_list(self, interaction: discord.Interaction):
        role_ids = await db.list_staff_roles(interaction.guild_id)
        if not role_ids:
            await interaction.response.send_message(embed=error_embed("No staff roles configured"), ephemeral=True)
            return
        mentions = ", ".join(f"<@&{r}>" for r in role_ids)
        await interaction.response.send_message(embed=base_embed("Staff roles", mentions), ephemeral=True)

    # ---------------- audit log ----------------
    @app_commands.command(name="auditlog", description="Show recent staff actions")
    @is_staff()
    async def auditlog(self, interaction: discord.Interaction):
        entries = await db.recent_audit(interaction.guild_id, limit=15)
        if not entries:
            await interaction.response.send_message(embed=error_embed("No audit entries yet"), ephemeral=True)
            return
        lines = [f"<t:{e['created_at']}:R> <@{e['actor_id']}> **{e['action']}** {e['details']}" for e in entries]
        embed = base_embed(f"{E.get('dvshield', '🛡️')} Recent staff actions", "\n".join(lines))
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------------- blacklist ----------------
    @app_commands.command(name="blacklist", description="Ban a user from purchasing")
    @is_staff()
    async def blacklist(self, interaction: discord.Interaction, user: discord.User, reason: str = ""):
        await db.blacklist_add(interaction.guild_id, user.id, reason)
        await db.log_action(interaction.guild_id, interaction.user.id, "blacklist_add", f"{user} - {reason}")
        await interaction.response.send_message(embed=success_embed("User blacklisted", f"{user.mention} can no longer purchase."), ephemeral=True)

    @app_commands.command(name="unblacklist", description="Remove a purchasing ban")
    @is_staff()
    async def unblacklist(self, interaction: discord.Interaction, user: discord.User):
        await db.blacklist_remove(interaction.guild_id, user.id)
        await interaction.response.send_message(embed=success_embed("User removed from blacklist", user.mention), ephemeral=True)

    # ---------------- backup ----------------
    @app_commands.command(name="backup", description="Export all orders in this server to a CSV file")
    @is_staff()
    async def backup(self, interaction: discord.Interaction):
        import csv
        import io as _io
        rows = await db.export_orders_csv_rows(interaction.guild_id)
        buf = _io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["order_id", "user_id", "product_id", "gateway", "amount", "currency", "status", "created_at", "delivered_at"])
        for r in rows:
            writer.writerow([r["id"], r["user_id"], r["product_id"], r["gateway_key"], r["amount"], r["currency"], r["status"], r["created_at"], r["delivered_at"]])
        buf.seek(0)
        file = discord.File(_io.BytesIO(buf.getvalue().encode("utf-8")), filename="davix_orders_backup.csv")
        await db.log_action(interaction.guild_id, interaction.user.id, "backup_export", f"{len(rows)} orders")
        await interaction.response.send_message(embed=success_embed("Backup ready", f"{len(rows)} order(s) exported."), file=file, ephemeral=True)

    # ---------------- stats ----------------
    @app_commands.command(name="stats", description="Show sales statistics")
    @is_staff()
    async def stats(self, interaction: discord.Interaction):
        stats = await db.sales_stats(interaction.guild_id)
        if not stats:
            await interaction.response.send_message(embed=base_embed("No sales yet"), ephemeral=True)
            return
        embed = base_embed(f"{E.get('dvmoney', '💰')} Sales statistics")
        for currency, s in stats.items():
            embed.add_field(name=currency, value=f"{s['count']} sale(s) - {format_money(s['total'], currency)}", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminCog(bot))
