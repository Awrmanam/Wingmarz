# PAYG release acceptance — evidence and blockers

Date: 2026-11-14. Branch: `audit/payg-release-hardening-20261114`.

**Interpretation:** PASS below means the cited deterministic local SQLite/pytest test
passed in CI with mocked Rebecca; it is **not** a staging acceptance sign-off.
BLOCKED means no sufficiently complete end-to-end acceptance evidence exists.
Production deployment and merge are prohibited until all critical blockers clear.

| # | Scenario | Result | Evidence and test level |
|---|---|---|---|
| 1 | New customer PAYG purchase | BLOCKED | No full purchase+remote creation+wallet+delivery staging trace. Unit tests cover provisioning reservation only |
| 2 | Concurrent duplicate purchase | PASS (mock) | `tests/test_payg_provision_reservation.py::test_concurrent_provision_only_one_remote_create`; prevents duplicate remote CREATE, but checkout/receipt race not fully exercised |
| 3 | Rebecca creation timeout | PASS (mock) | `test_remote_timeout_never_deletes_or_recreates` |
| 4 | Restart during creation | PASS (mock, safe hold) | `test_restart_inspects_ambiguous_operation_without_creating`; owner adoption requires verifiable remote identity |
| 5 | Normal usage and wallet debit | BLOCKED | Unit settlement exists, but no full staging end-to-end billing proof |
| 6 | Wallet exhaustion and financial hold | BLOCKED | Billing/suspension code exists; remote-disable + DB reconciliation needs staging proof |
| 7 | Recharge and service recovery | BLOCKED | Explicit financial-hold release and remote-enable staging trace required; wallet credit alone must not override manual disable |
| 8 | Percentage discount | PASS (mock) | `tests/test_payg_customer_discounts.py::test_discount_persistence_audit_and_owner` |
| 9 | Fixed private rate | PASS (mock) | `test_discount_precedence_and_validation` |
| 10 | Scheduled activation | PASS (mock) | `tests/test_payg_discount_scheduler.py::test_scheduled_activation_expiry_and_retry` |
| 11 | Automatic expiry | PASS (mock) | Same scheduler test; boundary failures are quarantined |
| 12 | Immediate cancellation | PASS (mock) | `test_cancel_queues_public_rate_transition` and rate switch fence tests; live Telegram/Rebecca not exercised |
| 13 | Rebecca outage during rate change | PASS (mock) | `test_switch_rebecca_failure_keeps_old_rate`, `test_failed_transition_stays_pending_and_blocks_billing` |
| 14 | Recovery after prolonged outage | PASS (mock, manual write-off) | `tests/test_payg_discount_disputed_boundary.py`; ambiguous bytes are not automatically priced. Automated reconstruction is impossible without historical provider counters |
| 15 | Old SQLite additive migration | PASS (local integration) | `tests/test_payg_historical_accrual.py::test_old_database_migration_is_additive`; representative fixture, not production database backup |

## Rebecca reconciliation policy

- `payg_provision_operations.id` is a durable local operation ID; `(user_id,provider,service_id)` and remote username are unique.
- A timeout or process crash after sending CREATE is ambiguous. A later `find_admin(username) == None` does **not** prove the request was never applied. Never retry CREATE based only on that negative lookup.
- `inspect_incomplete_provisions` records remote observations without changing remote state. The owner can request `adopt_verified_provision` only when Rebecca returns a matching exact username, Telegram user ID, standard role, requested service and user-only permissions. Missing fields fail closed.
- Adoption creates/links a local account and records the initial usage counter as a baseline, with an audit entry. Pre-adoption consumption is not retroactively charged. This write-off is a financial consequence of an ambiguous create.
- If Rebecca cannot prove remote identity, **BLOCKED: manual provider-side investigation**. Never delete a healthy remote panel, re-create it blindly, or debit the wallet twice.
- A production-ready automated negative-proof retry requires an upstream idempotency key or authoritative create-operation lookup, neither verified in the current Rebecca API.
- Remote admin credentials are stored in existing local persistence; database file permissions and encryption-at-rest require a separate production review.

## Security and finance audit scope

**Reviewed and modified this branch:** PAYG reservation and concurrency, remote creation timeout, ownership verification on adoption, owner-only Telegram callbacks and backend actions, discount billing boundaries, financial hold for disputed usage, rate history, wallet credit replay protection, additive PAYG migrations. This pass also found and fixed missing backend operator authorization in PAYG receipt approval/rejection, and incorrect historical-debt calculation during financial-hold release. The latter now uses the persisted billing accrual and refuses release while a discount boundary or rate switch is disputed. Regression tests: `tests/test_payg_release_security_audit.py`.

**Not signed off as a comprehensive audit:** all KYC flows, all legacy purchase/renewal routes, generic receipt approvals, reseller data isolation, all notification callbacks, production database migration and backup restore, Rebecca staging contract, full end-to-end payment reconciliation.

**Release gate:** do not merge PR #83 or deploy until staging acceptance, full security review, and Python 3.10/3.11 CI are green on the final commit. PR #83 includes PR #82's ancestry; avoid merging both independently without checking duplicate changes.
