# Security practices in Davix Sales Bot

No software can honestly promise to be "bug-free forever" or have "the best
security in the world" - what this project does instead is follow concrete,
verifiable practices:

1. **Credentials AND stock encrypted at rest.** Every gateway API key/secret,
   plus every stock line (keys, accounts, codes), is encrypted with Fernet
   (AES-128-CBC + HMAC) using `MASTER_ENCRYPTION_KEY` before it is written
   to SQLite. The key itself must live only in `.env`, which is git-ignored
   by default.
2. **Webhook signature verification.** Mercado Pago (`x-signature` HMAC-SHA256),
   Stripe (`Stripe-Signature` HMAC-SHA256 with a 5 minute replay window) and
   NOWPayments (`x-nowpayments-sig` HMAC-SHA512) requests are verified against
   the secret configured for that specific guild before anything is trusted.
   A webhook is never treated as proof of payment by itself - the bot always
   re-confirms the real status via a direct API call before delivering
   anything.
3. **Idempotent orders.** Every order gets a unique idempotency key, and
   delivery is guarded by a per-order `asyncio.Lock` plus a `delivered_at`
   check, so a webhook firing at the same time as the polling loop can never
   deliver a product twice.
4. **Parameterized SQL everywhere.** No query in `database/db.py` builds SQL
   with string formatting; every value goes through placeholders, which
   eliminates SQL injection as an attack surface.
5. **Least-privilege command checks.** Every admin/staff command requires the
   `Manage Server` Discord permission; buyer-facing components never expose
   staff actions.
6. **Rate limiting.** Purchases and status checks are rate-limited per user
   to blunt spam/abuse against payment gateways.
7. **Small, capped webhook surface.** The optional webhook server caps
   request bodies at 1 MB and only exposes two routes (`/webhook/...` and
   `/health`).
8. **No secrets in logs.** `utils/logger.py` never receives credential
   dictionaries; only high-level events are logged.
9. **Full audit trail.** Every staff action (product changes, gateway
   configuration, blacklist changes, coupon changes, refunds) is recorded
   in `audit_log` and viewable with `/auditlog`.
10. **Delivery never duplicates.** The exact content handed to a buyer is
    persisted per-order (encrypted). Resend paths always replay that saved
    content instead of re-claiming stock or re-generating anything, which
    closes off an entire class of "buyer gets two keys for one payment"
    bugs common in simpler sales bots.
11. **Staff permissions are configurable, not just implicit.** Beyond the
    Discord `Manage Server` permission, `/staff add` lets you grant scoped
    access to specific roles - useful for giving a support team store
    access without full server admin rights.

## Responsible use

- Never commit your `.env` file or share your `MASTER_ENCRYPTION_KEY`.
- Rotate gateway API keys periodically and immediately if you suspect a leak.
- Run the bot on infrastructure you trust; the webhook server should sit
  behind HTTPS (e.g. a reverse proxy or tunnel) since Discord and gateway
  dashboards typically require HTTPS callback URLs.
- Review each gateway's own dashboard for suspicious activity regularly -
  this bot cannot see or prevent fraud that happens outside of it.
