# Admin access and exchange credentials implementation

Scope: [approved TDD plan](../../docs/plans/ADMIN_ACCESS_OKX_CREDENTIALS_TDD_PLAN.md).
Implementation started 2026-11-05 (Asia/Saigon). This evidence does not approve
production deployment or credential cutover on the running Demo account.

## A1 — Authentication and storage foundation

Implemented optional Basic Auth with an explicit default-off flag, Google ID-token
verification using the official library, fresh PostgreSQL role lookup and explicit
permissions for every existing admin handler. Mutations recheck current role in
their commit transaction. Trading events and atomic security audit use the verified
actor. Added replayable accounts/audit SQL, with UTC timestamps and schema validation.

Verification runs use locally generated RSA signatures and certificates with the
real Google verifier. No fake verification flag is shipped. The bounded verifier
reuses the existing worker scheduler with a reserved control lane; only public
signing certificates are cached, never account permissions or tokens.

Red evidence: configuration tests initially had **4 failures / 4 passes**, proving
implicit Basic activation and the Google-only password requirement. Identity tests
initially failed collection because their implementation did not exist.

Green evidence:

- **1,110 CLI tests** passed, including route-by-route read permissions, malformed/
  expired/wrong-audience tokens, disabled Basic and auth DB failure redaction.
- **219 configuration/model/account-observation tests** passed.
- **7 targeted PostgreSQL tests** passed: repeated migrations, immediate role/active
  changes, subject binding, binding/audit atomic rollback, mutation-time role check,
  verified trading actor/idempotent audit and real service startup/read workflow.
- `python -m hummingbot.cli.swagger --check` passed.

The first broad PostgreSQL attempt was interrupted after source changes. It retained
two fixture check-constraint failures (an unbound invitation incorrectly contained
an issuer) and one legacy service-test setup missing explicit Basic opt-in. Fixtures
were corrected; the targeted rerun above passes. No full PostgreSQL/release acceptance
is claimed for A1. Existing Basic test/load harnesses now opt in explicitly, and
deployment examples explain that the bootstrap method must later be disabled.

Remaining: A2 whitelist/login/audit endpoints and last-admin protection; A3 encrypted
providers; A4 credential lifecycle/automatic cancel-close/recovery; A5 FE contract;
A6 final-image release/load and external Google/GCP/Demo acceptance.
