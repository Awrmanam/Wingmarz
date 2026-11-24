# Wingmarz PAYG final-release runbook (2026-11-15)

**Release target:** PR #83 (`audit/payg-release-hardening-20261114`). It contains the ancestor changes from PR #82. Do not merge both independently.

**Status as written:** NOT APPROVED FOR PUBLIC DEPLOYMENT. Automated Python/SQLite/fake-Rebecca tests are not real Telegram–Rebecca staging acceptance. The following checklist requires a dedicated test Telegram account, Rebecca test service, disposable local database or restored backup, and a non-production receipt/payment method.

## Evidence and acceptance record

For each case record: date, tester, commit SHA, Telegram test user ID, local account ID, Rebecca admin username, ledger row IDs, before/after balances, before/after usage, screenshots or redacted logs, **PASS / FAIL / BLOCKED**, and level **local mock / isolated integration / Rebecca staging**. Never put credentials, full receipts, private keys, or live customer data in GitHub.

| Gate | Actions on isolated staging | Pass condition |
| --- | --- | --- |
| W1 Wallet | Open customer /start → کیف پول من → history and filters; refresh repeatedly | Expected balance, credits and debits agree with the same wallet_ledger; read-only navigation does not change wallet or ledger row count; no other customer's data |
| W2 Existing PAYG | Open سرویس‌های من → one existing PAYG → گزارش مصرف and increase | Customer sees real account, accrued historical expense, paid amount, debt, rate, last captured counter; no FIXED-renewal buttons; existing account/admin ID unchanged |
| W3 Recharge | Add one test top-up to this PAYG account; approve once; repeat callback | Exactly one credited ledger entry, same account ID and Rebecca username, no new admin CREATE, no double credit, clear KYC and Financial Hold behavior |
| W4 FIXED renewal | Open a distinct genuine FIXED account and select allowed policy modes; inspect quote and submit using test data | FIXED-only choices and correct new order; no PAYG top-up order; correct planned traffic/time semantics; no duplicate approval |
| W5 Navigation | Walk every back button through wallet, services, report, users, recharge, finance and owner discounts, including old Telegram buttons | Each back button reaches correct parent; no legacy mixed menus; no stale callback can create a charge or order |

### Additional blocking tests

1. Rebecca real API contract: dedicated test service / test account only. Create one PAYG panel, capture remote username and permissions, inject real small traffic, verify billed bytes × effective rate (GiB), suspend after wallet exhausted, recharge and explicit authorized hold release, verify remote enable and no additional admin. Never intentionally exhaust a real customer's wallet.
2. Migration: take a consistent snapshot of the actual database, **restore it to isolated staging**, run schema initialization twice, compare wallet balances, ledger row counts and historic debt against baseline; verify that migrated PAYG admins do not reappear as FIXED accounts. A representative fixture is not equivalent to production backup/restore.
3. Security: owner-only finance actions; customer A cannot see customer B wallet/service or recharge B; reseller isolation, KYC, generic receipt handlers, legacy FIXED renewal, and notification callbacks require targeted checks. Tests passing in CI are not proof of comprehensive coverage.
4. Pending/ambiguous Rebecca create: safe manual review; no blind retry, remote delete or duplicate wallet debit.
5. Final commit must have all required CI checks green on Python 3.10 and 3.11. Record exact final SHA.

## Production backup and installation (ONLY after gates PASS and explicit approval)

The currently reported installation uses `/opt/wingmarz` and `wingmarz.service` (systemd). Inspect the actual unit (`systemctl cat wingmarz`) and database path before use. This is a **runbook, not an instruction to run immediately**.

```bash
cd /opt/wingmarz || exit 1
systemctl cat wingmarz
git status --short
git rev-parse HEAD
.venv/bin/python -c 'import config; print(config.DATABASE_PATH)'
```

**Precondition:** working tree clean, complete consistent backup, backup restore rehearsed on isolated staging, configured `PAYG_OWNER_ID=120674213` in systemd-loaded `.env`, verified Rebecca service/test credentials, no pending ambiguous financial/provision operations.

With the service stopped, preserve the exact previous SHA and the entire data directory, and take a SQLite online-backup or consistent stopped copy; also preserve `.env` outside source control. Verify backup accessibility and `PRAGMA integrity_check` on the copy. Do not `git reset --hard`, `rm -rf data`, or run code against real users during rehearsals.

After an approved merge of PR #83 (PR #82 remains unmerged or closed as superseded), update production with `git fetch origin main && git switch main && git pull --ff-only origin main`, install pinned dependencies, start the service and verify its commit, health and logs. Use a small test account before opening PAYG sales to everyone.

### Rollback

If the new bot fails after upgrade, stop `wingmarz.service`. Restore **both** the pre-upgrade code SHA and the matching backed-up database/data and `.env` together; then start. Rolling back **code only** can be unsafe after schema/ledger migrations. Do not replace a production database with an earlier snapshot if genuine post-upgrade payments have occurred: reconcile those financial events first.

## Sign-off

- [ ] W1 wallet: staging PASS
- [ ] W2 existing PAYG: staging PASS
- [ ] W3 recharge: staging PASS
- [ ] W4 FIXED renewal: staging PASS
- [ ] W5 navigation: staging PASS
- [ ] Rebecca create/usage/hold/recovery: staging PASS
- [ ] Real backup restore and additive migration twice: staging PASS
- [ ] Security and reseller isolation: PASS, with evidence
- [ ] Last PR head: Python 3.10 and 3.11 full suite and all required checks: PASS
- [ ] Owner explicitly approves merge and production deployment

**No sign-off based solely on simulated tests.**
