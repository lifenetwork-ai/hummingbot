# Local PostgreSQL spot feasibility spike

This disposable experiment tests transactional current financial state and indexed identity/active queries against real PostgreSQL. It is imported only by its explicit test runner. The LIFE controller does not install it.

The experiment uses the actual `StrategyV2Base._filter_authorized_actions`, `OrderExecutor.place_open_order` and `RESTConnection.call`. A synthetic adapter schedules one async send task per adapter and awaits fresh PostgreSQL authorization after an injected throttle wait. `FakeSession` performs no HTTP: it captures the exact post-only LIFE-USDT wire and produces synthetic ACK/cancel responses. The measured watchdog belongs to this spike, not the production LIFE safety manager. Tick/queue listening, real OKX post-throttler integration, recorder provenance and full financial reconciliation remain subsequent work.

## Run

Requires Docker, the project `hummingbot` conda runtime, and the pinned PostgreSQL image available locally or through Docker. Run from the repository root:

```sh
conda run -n hummingbot python -m pip install --target /tmp/life-pg-spike-deps -r test/hummingbot/strategy_v2/life_liquidity/postgres_spike/requirements.txt
PYTHONPATH=/tmp/life-pg-spike-deps:. conda run -n hummingbot python -m test.hummingbot.strategy_v2.life_liquidity.postgres_spike.run --output /tmp/life-pg-spike-results
```

Use a new output directory for each run. The runner refuses to overwrite evidence. It creates its own labeled container, exposes a random loopback port, creates a dedicated synthetic database/runtime role, runs functional and scale suites sequentially, saves XML/logs/source hashes/plans/results, then removes its container. Test data uses temporary schemas removed after each case. Trust authentication is confined to this disposable, synthetic fixture; no production DSN or exchange credentials are accepted by the runner.

## Extended runner/OKX/recorder assembly

```sh
PYTHONPATH=/tmp/life-pg-spike-deps:. conda run -n hummingbot python -m test.hummingbot.strategy_v2.life_liquidity.postgres_spike.run --assembly --output /tmp/life-pg-assembly-results
```

This mode uses actual V2 tick/action queue/listener, orchestrator and executor control loops; actual OKX protected scheduling/create/quantization/tracking/authentication/throttler/request/cancel code; and a file-backed SQLite `MarketsRecorder` projection. Its in-memory HTTP session accepts only the expected OKX routes and validates synthetic authentication headers; it performs no network I/O. Synthetic provider/price/balances, the PG spot authority and an experimental coordinator/watchdog remain the experiment boundary.

Reservation and a detached `READY` projection operation commit together. Worker-thread recording and fresh exact metadata reads precede `APPLIED` publication and arm. An awaited callback now runs inside actual RESTConnection after throttling/authentication, followed by the existing synchronous local stop/expiry/wire fence. Missing/wrong/delayed recorder evidence denies send; cancellation/unknown ACK retains holds. Cold objects start unarmed. Full repair dispatcher, real LIFE controller financial migration, external ownership fencing and venue proof remain open.

The assembly admits two inflight sends, uses a two-batch action queue and retains at most 32 local session intent entries. Terminal identities remain in PostgreSQL. Final scale seeds terminal histories in both PostgreSQL and SQLite; full recorder startup hydration/seed are outside hot-path timing. See the [assembly evidence](../../../../../docs/plans/LIFE_POSTGRES_ASSEMBLY_EVIDENCE_2026-10-12.md) for results and exact limitations. Earlier spike artifacts remain historical evidence for their original sources.

`pg_spike_cases.py` is intentionally outside default `test_*.py` discovery so ordinary file-backend regression runs require neither Docker nor asyncpg. The pinned driver is installed in a temporary directory; this experiment does not alter the application dependency set.

## Financial and integrity scope

- An account row serializes allocation with `FOR UPDATE`. Current balances, reserved cash/base, service budget and active count update in the same transaction as the attempt and append-only event.
- Exact command identity makes a duplicate reservation a readback, never resend permission. Conflicting command/wire identities fail. The synthetic fee ceiling is 0.1%, service hold is 0.01 USDT, and quantities use eight decimal places.
- `SEND_UNKNOWN` is committed before fake transport. Stop, timeout, DB failure and lost ACK retain the hold. A fresh object cannot arm that identity again. Zero-fill release requires the explicitly injected fake terminal proof; the method is not a general exchange reconciliation API.
- Runtime cannot update/delete historical events or modify account activation fields. Application commands are trusted to maintain counters; the schema/role is a prototype, not a complete privilege or transition enforcement design. DBA tampering, arbitrary disk corruption and independent audit anchoring are not qualified here.
- Final PostgreSQL reads use the primary. A local stop/expiry fence runs synchronously inside actual `RESTConnection`. There is still a DB-check/external-send race; this spike establishes neither enforceable ownership handoff nor atomic DB/OKX delivery.

## Synthetic capacities and measurement

PostgreSQL 16 image digest and asyncpg 0.31.0 are pinned. The container has two CPUs and 1 GiB RAM. Pool size is 1–2, acquire timeout 300 ms, command/statement timeout 1,000 ms, lock timeout 300 ms, observation age 30 seconds, throttle wait 1,000 ms, proposal lifetime 30 seconds, and active capacity 32. These values exercise bounded refusal and responsiveness; none is a deployment default or live calibration.

History sizes 0/1k/10k contain corresponding terminal identities and synthetic fill/fee events, with precomputed current balances/budget spending independently checked during fixture setup. Each variant runs five successful sends, zero-fill settlements between samples, and a watchdog cancel of the final owned order. Seed, audit and `EXPLAIN ANALYZE` are outside measured admission. SQL query/returned-row counters cover authority methods; pool setup/reset, protocol bytes and DB internal buffer work are not included in those counters. Raw query plans retain actual buffers and row counts for the sampled identity/active queries.

The spike uses the frozen O.8 limits without changing their file: poll 2,000 ms, send 1,000 ms, lag 500 ms, cancel start 250 ms. Passing these thresholds in this smaller workload does not close the full O.8 assembly. Five samples are diagnostic evidence, not a stable production percentile or a causal speedup comparison with the file backend.

## Migration configuration and startup

Application schema scripts and `.env` settings live in the
[central LIFE migration guide](../../../../../migrations/life_liquidity/README.md).
They include separate versioned SQL files, manual Make targets and default-off
`LIFE_AUTO_MIGRATE` at application startup. SQL files in this spike directory are
synthetic test fixtures, not application migrations.
