# Wingmarz — independent Fastly digital-inventory storefront

This is a new, separate stock-and-order subsystem. It does **not** mutate
Rebecca panels, fixed-plan orders, legacy renewals, PAYG accounts, wallet
balances or their schemas. The existing public/reseller home buttons retain
their text, order and callbacks; one new **خرید اکانت Fastly** row is appended
to each home keyboard. The sudo homepage is unchanged. Store management is
under **فروش و سفارش‌ها → مدیریت فروشگاه Fastly**, or via `/fastly_admin`.

## Deployment requirements

1. Back up the current SQLite database and your existing service unit/env.
2. Install the updated `requirements.txt` (including `cryptography`).
3. Generate a fresh key **on the server**, never in Git:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
4. Configure these variables in the service's secret environment:

   ```dotenv
   FASTLY_STORE_ENABLED=1
   FASTLY_STORE_FERNET_KEY=<paste-a-new-Fernet-key-from-the-command-above>
   ```

5. Restart in a maintenance window. The Fastly schema is added automatically
   on first use with `CREATE TABLE IF NOT EXISTS`. No existing tables are
   dropped or renamed. If you need to keep the storefront hidden, set
   `FASTLY_STORE_ENABLED=0` (already-purchased account retrieval remains
   available when the key remains configured).
6. On the staff account open `/fastly_admin`, create one or more products,
   set price and guarantee separately, import stock and perform end-to-end
   test purchases before opening it broadly.
7. Preserve **both the encrypted database AND the Fernet key** in secure
   backups. If the key is lost, stored credentials cannot be decrypted.
   Do not rotate this key without a planned database re-encryption migration.

## Product setup

Create separate products for ordinary and guaranteed accounts. For example:
- فستلی تازه‌ساخت — XHTTP باز: warranty days 0, replacements 0
- فستلی تازه‌ساخت — با ضمانت تعویض: choose higher price and positive
  warranty days plus maximum replacements

The account type/title, price, usage rules, guarantee and maximum quantity
are managed inside the bot by staff. Rules, title, unit price, warranty period
and replacement limit are copied into each order when created. Existing
purchases are not retroactively modified when a product changes.

### Uploading inventory safely

Staff-only, Telegram private chat:
```text
email1@example.test|first-password
email2@example.test|second-password
```
You may also send a UTF-8 `.txt` document. At most 200 rows per batch.
Email/usernames are deduplicated across all Fastly SKUs by a keyed HMAC;
secrets are Fernet-encrypted before storage. The bot attempts to delete the
source Telegram message, but **Telegram bot chats are not end-to-end
encrypted**, and neither deletion nor Telegram-side retention can be
guaranteed. Never include secrets in group chats.

## Customer flow

- `/start` → one extra Fastly button; all preexisting actions untouched.
- Select a product → select quantity (custom quantity available) → if a
  repeat buyer, select why a new account is needed.
- Create an independent Fastly order with snapshot price and warranty.
- Check the existing one-time identity approval via `trust_service` and
  `kyc_service`. New users follow the existing `kyc:start` review flow;
  approved users skip repeating documents. Pending Fastly orders do not
  overwrite fixed/PAYG checkout contexts.
- Show active bank cards only **after KYC**. Submit a payment photo; it remains
  `submitted` pending staff verification. A photo by itself never approves
  payment or releases a credential.
- Staff reviews the real payment and clicks approve; a SQLite
  `BEGIN IMMEDIATE` transaction assigns exactly N distinct units to the
  order. Repeated approval is idempotent. If stock is insufficient, mark
  `out_of_stock` after payment confirmation; the staff console can retry
  allocation after restock or handle a refund manually. Do **not** tell the
  buyer that a missing allocation was delivered.
- Buyer reads the order's snapshotted rules, accepts them and **only then**
  decrypts credentials in a private DM. The allocated units never return to
  available stock, even if Telegram delivery fails.
- A user may recover their previously allocated credentials from their own
  **سفارش‌های من**, after a fresh ownership check. Such private bot messages
  are not E2EE. Buyer should store them securely and avoid forwarding.
- Warranty claims track a specific sold unit, reason, purchaser, expiration,
  max replacement count and ancestry. Staff explicitly decides claims;
  an approved replacement atomically consumes one fresh unit and retains
  historical linkage and purchase statistics.

## Boundaries and important controls

- Staff permission: existing numeric `config.SUDO_ADMINS` / `is_staff`.
- Buyer permission: numeric Telegram ID, plus private-chat check before any
  payment details or credentials. No secrets in callback data or admin reports.
- SQLite: `fastly_products`, `fastly_inventory`, `fastly_orders`,
  `fastly_order_items`, `fastly_claims`, `fastly_audit` only.
- Payments: manual bank reconciliation; no auto-confirm from receipt image.
- Text templates: `fastly_*` entries in the existing Premium UI text editor.
  Product rules are edited per SKU and snapshotted in each purchase.
- There is no automated Fastly account creation, ban circumvention, or remote
  Fastly API integration. Accounts must be supplied and authorized by staff.

## QA / rollback

Run `python -m compileall -q fastly_store_service.py handlers/fastly_store.py`
and `python -m pytest -q tests/test_fastly_store.py` plus the existing test
suite, on Python 3.10 and 3.11. Test two simultaneous buyers and duplicate
staff approvals, old KYC, paid-but-out-of-stock recovery, order retrieval
after restart, warranty chains, role enforcement, all old navigation buttons,
and buyer blocks of bot messages.

Rollback code by restoring the previous branch/commit; all new tables are
isolated and may remain unused. For financial audit, **never** delete Fastly
tables or already-sold inventory as part of rollback. Keep the encryption key.
A code rollback hides Fastly UI but does not automatically refund or undo paid
orders; handle in-progress Fastly orders from the admin console before rollback.
