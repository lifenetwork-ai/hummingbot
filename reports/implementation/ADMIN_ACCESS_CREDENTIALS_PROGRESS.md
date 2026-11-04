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

- **1,110 CLI/config tests** passed, including route-by-route read permissions, malformed/
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

## A2 — Whitelist, Google onboarding and audit API

Added `/access/login`, `/access/me`, `/access/bootstrap`, account list/create/change
and `/audit`. Account administration serializes last-admin changes, checks current
permissions in the transaction and preserves subject identity. Google-only mode
requires a bound working administrator; bootstrap is never silently re-enabled.

Audit queries have bounded cursor pagination with microsecond/ID ordering, filter
scope and UTC times. Security changes and their success audit are atomic. Sign-in
and mutation denials retain safe verified actors or an unknown actor; invalid tokens
are not parsed into claimed identities for audit. Ordinary polling/anonymous failures
stay in structured platform logs; failed DB audit writes are explicitly logged with
request IDs, never presented as guaranteed persisted history.

Red evidence: missing account management methods and inaccessible bootstrap route
failed first. A new binding-collision test reproduced a raw unique-constraint error;
Green now rejects a second email for an existing subject with a clear binding code.
The schema tests were updated for the new alternative Google/Bearer security contract.

Verified: **1,087 CLI tests**, **17 focused PostgreSQL checks**, and Swagger generation/
drift checks. Targeted SQL checks cover concurrent self-demotion, rollback on audit
failure, duplicate binding, actor-preserving denials and API restart with disabled
Basic and a still-valid Google token. A separate **real supervisor/API/engine process
restart test passed**: bootstrap first, then a new Google-only revision rejects the
still-populated old password and accepts the existing Google token/account. Offline
tests use signed RSA fixtures through the official verifier, not real Google OAuth
acceptance. Actual external Google sign-in remains an A6 check.

Remaining: A3 encrypted providers; A4 credential lifecycle/automatic cancel-close/
recovery; A5 final FE contract; A6 final-image release/load and external Google/GCP/
Demo acceptance. No managed credential or Disconnect endpoint is enabled yet.
