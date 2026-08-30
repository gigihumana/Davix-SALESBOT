# Davix Sales Bot

A complete, production-grade Discord sales bot in Python (`discord.py`), built
around a plugin-style payment gateway architecture, encrypted data at rest,
private per-order "cart" channels, coupons, staff tooling, and automatic
branding (its own icon + its own animated emojis, uploaded on first boot -
nothing to generate or configure by hand).

**~4,400 lines of Python** across a clean, modular layout - not a single
giant file - so it's actually maintainable and easy to extend.

## Highlights

- **13 real payment gateways**, one file each, all implementing the same
  small interface (`gateways/base.py`). Adding a 14th is a single new file.
- **Private cart channels.** Set a category on any product and every
  purchase opens a ticket-style private channel there instead of just an
  ephemeral message - buyers and staff can talk, staff can see the
  checkout, and it doubles as a receipt.
- **Delivery that never duplicates.** Whatever was handed to a buyer (a
  stock line, a file, or both) is saved - encrypted - on the order itself.
  The "Resend" button, `/resend` (works in the bot's DMs too), and staff's
  `/order resend` all replay that exact saved content. Nothing is ever
  re-claimed from stock or regenerated.
- **DM-down handling done right.** Buyers are warned up front to keep DMs
  open. If delivery fails because DMs are closed, staff get pinged in the
  cart channel (or log channel), and the buyer can self-serve with
  `/resend` the moment they fix their settings - no lost sales, no
  duplicate keys.
- **Coupons**, **low-stock alerts**, **staff roles** (beyond `Manage
  Server`), a full **audit log**, CSV **backups**, and a **/gateway test**
  command that verifies real credentials before you go live.
- **Its own branding, fully automatic.** A minimalist icon (a "D" merged
  into a shopping cart) and 13 animated emoji GIFs ship pre-generated in
  `assets/` - the bot uploads them as its own Application avatar/emojis on
  first run. Nothing to draw, export, or configure.
- **Security by default**: Fernet encryption for both gateway credentials
  and stock content, HMAC webhook signature verification, idempotent
  orders with per-order locking, parameterized SQL everywhere, and
  per-user rate limiting. Full write-up in `SECURITY.md`.

## Requirements

- Python 3.11+
- A Discord bot application (bot token + Application ID, from the
  Discord Developer Portal)
- API credentials for whichever gateway(s) you want to enable

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env
```

Generate your encryption key and paste it into `.env`:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Fill in `.env`:

```
DISCORD_TOKEN=your-bot-token
DISCORD_APPLICATION_ID=your-application-id
MASTER_ENCRYPTION_KEY=the-key-you-just-generated
```

Run it:

```bash
python3 main.py
```

That's it - on first connect the bot uploads its icon and 13 animated
emojis automatically. No extra step, no script to run.

### In Discord

1. `/setup` - pick the channel where the buy panel is posted, and (recommended)
   a log channel for sales/alerts.
2. `/staff add role:@Support` - optional, lets a role manage the store
   without full `Manage Server` permission.
3. `/gateway setup` - pick a gateway from the dropdown, fill in its
   credentials in the modal. Repeat for every gateway you want to accept.
4. `/gateway test gateway:stripe` - confirms the credentials actually work
   before a real buyer hits them.
5. `/product add name:"Premium Role" price:19.90 currency:BRL delivery:stock`
   - creates a product. Add `cart_category:#tickets` if you want a private
   channel per purchase.
6. `/product stock-add product_id:1 items:"KEY-AAA\nKEY-BBB\nKEY-CCC"` - one
   item per line; each buyer gets exactly one line, and it's encrypted at
   rest.
7. (Optional) `/product set-file product_id:1 file:<upload>` - attaches a
   file sent alongside/instead of a stock line.
8. (Optional) `/coupon create code:LAUNCH10 percent_off:10` - buyers apply
   it at checkout before picking a payment method.
9. `/panel` - posts the Buy button. Done.

## How a purchase flows

1. Buyer clicks **Buy** -> picks a product -> optionally applies a coupon ->
   picks a payment method.
2. If the product has a cart category set, a private channel is created
   for just that buyer + staff; otherwise everything happens in an
   ephemeral message.
3. The bot creates the charge on the gateway (Pix QR code, checkout link,
   whatever fits that gateway) and shows it, plus a reminder to keep DMs
   open.
4. The bot confirms payment either instantly (webhook, if you've enabled
   one) or within `PAYMENT_POLL_INTERVAL` seconds (polling, on by default,
   zero setup required).
5. On confirmation: stock is atomically claimed (never double-claimed),
   any attached file is snapshotted for this specific order, a role is
   granted if applicable, and everything is DMed to the buyer. The exact
   content is saved to the order so it can be resent identically later.
6. If the DM fails, staff are notified where to help, and the buyer can
   run `/resend` themselves once DMs are open again.

## Payment gateways included

| Gateway | Region / currencies | Method |
|---|---|---|
| Mercado Pago | BRL | Pix (QR + copy-paste code) |
| PushinPay | BRL | Pix, lightweight |
| Asaas | BRL | Pix (auto-creates a lightweight customer) |
| AbacatePay | BRL | Pix |
| PicPay | BRL | Wallet checkout |
| Stripe | USD, EUR, GBP + 15 more | Hosted Checkout |
| PayPal | USD, EUR, GBP + 12 more | Orders v2 |
| Mollie | EUR + 7 more (Europe) | Hosted Checkout |
| Razorpay | INR (India) | Payment Links |
| Paystack | NGN, GHS, ZAR, USD, KES (Africa) | Hosted Checkout |
| Coinbase Commerce | Priced in USD/EUR/GBP, paid in crypto | Hosted Checkout |
| NOWPayments | Priced in USD/EUR/BRL/GBP, paid in 100+ coins (BTC, ETH, USDT, SOL, LTC...) | Hosted Checkout |
| Manual | Any currency | Staff confirms with `/order confirm` |

Honest scope note: no codebase can truthfully claim bug-free support for
"every gateway in the world." This covers the rails that the overwhelming
majority of real Discord shops need - Brazilian Pix, global cards, Europe,
India, Africa, and crypto - plus a Manual fallback for anything else and a
one-file extension point for developers who want to add more.

## Command reference (37 commands)

**Setup & config**
`/setup`, `/staff add|remove|list`, `/gateway setup|disable|list|test`

**Products**
`/product add|edit|info|list|remove|toggle|stock-add|set-file|
set-cart-category|set-lowstock`

**Coupons**
`/coupon create|delete|list`

**Orders**
`/order status|confirm|cancel|refund|resend|history`

**Buyers**
`/panel`, `/resend`, `/help`

**Moderation & ops**
`/blacklist`, `/unblacklist`, `/stats`, `/auditlog`, `/backup`

**Utility**
`/ping`, `/about`

Discord caps applications at 100 global commands - this set covers the
entire feature list above while staying well under that limit and easy to
actually navigate, which matters more than raw count.

## Webhooks vs. polling

By default the bot polls each pending order every `PAYMENT_POLL_INTERVAL`
seconds (15s) - reliable with zero extra setup. For instant confirmations,
set `WEBHOOK_ENABLED=true`, put the bot behind a public HTTPS URL (reverse
proxy or tunnel), and register:

```
<WEBHOOK_PUBLIC_URL>/webhook/<gateway_key>/<guild_id>
```

in each gateway's dashboard, e.g. `/webhook/stripe/123456789012345678`.
Webhooks are always re-verified against a live status check before
anything is delivered - a webhook alone is never treated as proof of
payment.

## Branding assets

`assets/icon/davix_icon*.png` and `assets/emojis/*.gif` ship pre-generated
and ready to use - the bot uploads them automatically on first run. There
is no generator script to run and nothing to configure; if you ever want
to redesign the artwork, just replace the files in `assets/` with your own
same-named PNG/GIF files before starting the bot.

## Security

See `SECURITY.md` for the full list: encryption at rest (credentials *and*
stock), webhook signature verification, idempotency + per-order locking,
parameterized SQL, least-privilege command checks, rate limiting, and more.

## Project layout

```
main.py                       entrypoint
config.py                     environment/config loader
database/db.py                SQLite data layer (aiosqlite), one method per query
gateways/                     one file per payment gateway + the shared interface
services/payment_service.py   order creation, cart channels, confirmation, delivery, resend
services/webhook_server.py    optional aiohttp webhook receiver
cogs/admin.py                 /setup, /product, /gateway, /coupon, /staff, /auditlog, /backup, /blacklist, /stats
cogs/store.py                 the buy panel, checkout flow, /resend, /panel
cogs/orders.py                /order status|confirm|cancel|refund|resend|history
cogs/misc.py                  /ping, /about, /help
utils/                        security, embeds, logging, emoji + icon manager
assets/icon/, assets/emojis/  pre-generated branding (icon + animated emojis) - ready to use
```
