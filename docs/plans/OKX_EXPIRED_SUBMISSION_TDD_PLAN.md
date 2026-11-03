# OKX expired submission and recovery: TDD follow-up

Demo image `e9845e3f` stopped during Random rollover on 2026-11-03. OKX
returned an expiry rejection message while a request reached the step deadline;
shutdown then received order-not-found, and the unresolved intent blocked startup.
The service is stopped with HALT and zero open Demo orders. The original response
payload was not retained, so the exact returned identity fields are unconfirmed.
An independent read found one pending local intent, OKX 51603 for that client ID,
and no match in the recent fill page; the latter is not exhaustive history proof.

- [x] Capture the failing run and preserve a private post-test database backup.
- [x] Red/Green: only an explicit OKX 50036 response correlated to the exact
  client ID, with no exchange order ID, may prove this request was rejected.
  Both connectors propagate the typed outcome; ambiguous responses remain blocked.
- [x] On fenced, durable rejection remove the pending intent and retain bounded,
  object-specific proof for shutdown; keep HALT on failed persistence or any
  uncertain outcome. Test mismatches and object replacement with real PostgreSQL.
- [x] Run native, PostgreSQL and Swagger regressions; record source test scope.
- [ ] Capture/reproduce the actual OKX response shape in an isolated read-safe or
  controlled Demo scenario. Do not infer that the real response contained an ID.
- [x] Fully reconcile the Demo intent and account against the venue, prepare and
  obtain explicit operator review of any specific maintenance/HALT recovery.
  Never use an old database-reconstruction approval for this separate change.
- [x] Requalify a new image offline and run full R1, an isolated repeat of the
  affected warmup/mixed and a clean independent R2; document overlapping dry runs.
- [x] Complete the nine-case Demo matrix, Random, Stop and restart after reviewed
  recovery on `7dca3ce9`; preserve image-specific load and failed evidence.
- [x] Complete per-trade REST proof for the seven new natural fills. A later
  read returns all seven maker fills; IDs, quantities, prices, sides and original
  fees match. Preserve the earlier failed query/observation separately.

Final checkpoint-fix image `51d4d879` also passes its own full R1/R2:
[release report](../../reports/release/mm-checkpoint-final-release.md).
