"""Explicit opt-in cases; run with run.py against its disposable real PostgreSQL."""
import asyncio
import json
import os
import time
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.postgres_spike.spike import Command, PgAuthority, SpikeDispatcher
from test.hummingbot.strategy_v2.life_liquidity.test_o8_performance import limit
from types import SimpleNamespace
from unittest.mock import MagicMock

import asyncpg
import pytest
import pytest_asyncio

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction

HERE = Path(__file__).parent


def command(intent="order", **changes):
    return replace(Command(intent, "wire-" + intent, datetime.now(timezone.utc) + timedelta(seconds=30)), **changes)


@pytest_asyncio.fixture
async def db():
    # Never accept an arbitrary user's DB. The runner creates this synthetic DB.
    dsn = os.environ["LIFE_PG_SPIKE_DSN"]
    assert dsn.startswith("postgresql://postgres@127.0.0.1:") and dsn.endswith("/life_spike")
    admin = await asyncpg.connect(dsn, command_timeout=2)
    assert await admin.fetchval("SELECT current_database()") == "life_spike"
    schema = "life_spike_" + uuid.uuid4().hex
    await admin.execute(f'CREATE SCHEMA "{schema}"')
    await admin.execute(f'SET search_path TO "{schema}"')
    await admin.execute(HERE.joinpath("schema.sql").read_text())
    await admin.execute("""INSERT INTO account_state
        (account,environment,owner,lineage,epoch,config_version,risk_epoch,session,armed,expires_at,observed_at,cash,base)
        VALUES ('synthetic-life','OFFLINE_SYNTHETIC_ONLY','process-a','synthetic-lineage',1,1,1,
                'synthetic-session',true,clock_timestamp()+interval '1 hour',clock_timestamp(),1000,1000)""")
    await admin.execute(f'GRANT USAGE ON SCHEMA "{schema}" TO life_spike_runtime')
    await admin.execute("GRANT SELECT ON account_state,attempts,events TO life_spike_runtime")
    await admin.execute("""GRANT UPDATE (cash,base,reserved_cash,reserved_base,budget_spent,budget_reserved,version)
                           ON account_state TO life_spike_runtime""")
    await admin.execute("GRANT UPDATE (active_count) ON account_state TO life_spike_runtime")
    await admin.execute("GRANT INSERT, UPDATE ON attempts TO life_spike_runtime")
    await admin.execute("GRANT INSERT ON events TO life_spike_runtime")

    async def setup(connection):
        await connection.execute(f'SET search_path TO "{schema}"')
        await connection.execute("SET statement_timeout = '1000ms'")
        await connection.execute("SET lock_timeout = '300ms'")

    pool = await asyncpg.create_pool(dsn.replace("postgres@", "life_spike_runtime@"),
                                     min_size=1, max_size=2, command_timeout=1, setup=setup)
    try:
        yield SimpleNamespace(store=PgAuthority(pool), admin=admin, pool=pool, schema=schema, dsn=dsn)
    finally:
        await asyncio.wait_for(pool.close(), 5)
        await admin.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await admin.close()


@pytest.mark.asyncio
async def test_reserve_is_atomic_and_retry_is_idempotent(db):
    c = command()
    assert await db.store.reserve(c) is True
    assert await db.store.reserve(c) is False
    row = await db.admin.fetchrow("SELECT * FROM account_state")
    assert row["reserved_cash"] == D("1.001") and row["budget_reserved"] == D("0.01")
    assert await db.admin.fetchval("SELECT count(*) FROM events") == 1
    with pytest.raises((ValueError, asyncpg.UniqueViolationError)):
        await db.store.reserve(replace(c, amount=D("2")))
    assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == D("1.001")


@pytest.mark.asyncio
async def test_concurrent_commands_cannot_double_allocate(db):
    result = await asyncio.gather(db.store.reserve(command("a", amount=D("750"))),
                                  db.store.reserve(command("b", amount=D("750"))), return_exceptions=True)
    assert sum(x is True for x in result) == 1
    assert await db.admin.fetchval("SELECT count(*) FROM attempts") == 1
    assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == D("750.75")


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("owner", "other"), ("epoch", 2), ("config_version", 2),
                                         ("risk_epoch", 2), ("session", "other"), ("lineage", "other")])
async def test_changed_authority_denies_final_without_releasing_hold(db, field, value):
    c = command()
    await db.store.reserve(c)
    await db.store.authorize(c, arm=True)
    await db.admin.execute(f'UPDATE account_state SET "{field}"=$1', value)
    with pytest.raises(PermissionError):
        await db.store.authorize(c)
    assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == D("1.001")
    assert await db.admin.fetchval("SELECT state FROM attempts") == "SEND_UNKNOWN"


class FakeSession:
    def __init__(self):
        self.sent = []
        self.cancels = []
        self.lose_ack = False

    async def request(self, *, method, url, params, data, headers):
        assert method == "POST" and url == "https://synthetic.invalid/api/v5/trade/order"
        wire = json.loads(data)
        self.sent.append(wire)
        if self.lose_ack:
            raise ConnectionError("synthetic accepted request, lost ACK")

        async def response_json():
            return {"ordId": "exchange-" + wire["clOrdId"]}

        return SimpleNamespace(json=response_json, content_type="application/json")

    async def cancel(self, wire, exchange_id):
        assert any(row["clOrdId"] == wire for row in self.sent)
        self.cancels.append((wire, exchange_id, time.perf_counter()))
        return True  # ACK of request only, never terminal/release proof.


def dispatch(db, c, session=None):
    session = session or FakeSession()
    adapter = SpikeDispatcher(db.store, c, session)
    strategy = SimpleNamespace(controllers={"life": adapter}, connectors={"okx": MagicMock()}, logger=adapter.logger)
    config = OrderExecutorConfig(id=c.intent, controller_id="life", timestamp=0, connector_name="okx",
                                 trading_pair="LIFE-USDT", side=TradeType[c.side], amount=c.amount, price=c.price,
                                 execution_strategy=ExecutionStrategy.LIMIT_MAKER)
    action = CreateExecutorAction(controller_id="life", executor_config=config)

    def enqueue():
        assert StrategyV2Base._filter_authorized_actions(strategy, [action]) == [action]
        executor = OrderExecutor(strategy, config)
        # Use a qualified synthetic price; no real order book/quote model proof is claimed.
        executor.get_order_price = lambda: c.price
        executor.place_open_order()
        assert executor._order.order_id == c.wire
        return executor

    return adapter, session, enqueue


@pytest.mark.asyncio
async def test_actual_executor_and_rest_boundary_commit_before_fake_io(db):
    c = command()
    adapter, session, enqueue = dispatch(db, c)
    enqueue()
    assert not session.sent
    await adapter.pending
    assert session.sent[0]["clOrdId"] == c.wire
    assert await db.admin.fetchval("SELECT state FROM attempts") == "ACKED"
    await adapter.cancel_owned()
    assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == D("1.001")
    assert await db.store.settle_zero_fill(c, "exchange-" + c.wire) is True
    assert await db.store.settle_zero_fill(c, "exchange-" + c.wire) is False
    assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == 0


@pytest.mark.asyncio
async def test_unknown_send_survives_new_authority_and_cannot_retry(db):
    c = command()
    session = FakeSession()
    session.lose_ack = True
    adapter, _, enqueue = dispatch(db, c, session)
    enqueue()
    with pytest.raises(ConnectionError):
        await adapter.pending
    restored = PgAuthority(db.pool)
    assert await restored.reserve(c) is False
    with pytest.raises(PermissionError):
        await restored.authorize(c, arm=True)
    assert len(session.sent) == 1
    assert await db.admin.fetchval("SELECT state FROM attempts") == "SEND_UNKNOWN"
    assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == D("1.001")


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["stop", "config", "expiry", "unarmed", "observation"])
async def test_revocation_during_throttle_blocks_actual_rest(db, fault):
    c = command()
    adapter, session, enqueue = dispatch(db, c)
    adapter.throttle.clear()
    enqueue()
    await asyncio.wait_for(adapter.armed.wait(), 1)
    if fault == "stop":
        await adapter.stop()
    else:
        sql = {"config": "config_version=2", "expiry": "expires_at=clock_timestamp()-interval '1 second'",
               "unarmed": "armed=false", "observation": "observed_at=clock_timestamp()-interval '1 hour'"}[fault]
        await db.admin.execute("UPDATE account_state SET " + sql)
        adapter.throttle.set()
        with pytest.raises(PermissionError):
            await adapter.pending
    assert not session.sent
    assert await db.admin.fetchval("SELECT state FROM attempts") == "SEND_UNKNOWN"
    assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == D("1.001")


@pytest.mark.asyncio
async def test_stop_during_db_wait_and_bounded_enqueue(db):
    c = command()
    adapter, session, enqueue = dispatch(db, c)
    transaction = db.admin.transaction()
    await transaction.start()
    await db.admin.fetchrow("SELECT * FROM account_state FOR UPDATE")
    try:
        enqueue()
        with pytest.raises(PermissionError):
            adapter.submit_executor_spot_order(SimpleNamespace(), amount=c.amount, price=c.price, order_type=None)
        await asyncio.sleep(.03)
        await adapter.stop()
    finally:
        await transaction.rollback()
    assert not session.sent
    assert await db.admin.fetchval("SELECT count(*) FROM attempts") == 0
    assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == 0


@pytest.mark.asyncio
async def test_pool_exhaustion_times_out_without_starving_loop(db):
    async with db.pool.acquire() as first, db.pool.acquire() as second:
        assert first is not second
        ticks = []

        async def heartbeat():
            while True:
                ticks.append(time.perf_counter())
                await asyncio.sleep(.01)

        monitor = asyncio.create_task(heartbeat())
        try:
            with pytest.raises(TimeoutError):
                await db.store.reserve(command())
        finally:
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
        assert len(ticks) >= 5
    assert await db.admin.fetchval("SELECT count(*) FROM attempts") == 0


@pytest.mark.asyncio
async def test_event_history_is_not_mutable_by_runtime_role(db):
    await db.store.reserve(command())
    async with db.pool.acquire() as connection:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute("DELETE FROM events")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute("UPDATE events SET kind='OTHER'")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await connection.execute("UPDATE account_state SET armed=true")


@pytest.mark.asyncio
async def test_ack_failure_and_foreign_wire_do_not_refund(db):
    c = command()
    await db.store.reserve(c)
    await db.store.authorize(c, arm=True)
    with pytest.raises(PermissionError):
        await db.store.settle_zero_fill(c, "unproven")
    with pytest.raises(asyncpg.UniqueViolationError):
        await db.store.reserve(command("different", wire=c.wire))
    assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == D("1.001")
    assert await db.admin.fetchval("SELECT count(*) FROM attempts") == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("amount", [D("NaN"), D("Infinity"), D("0"), D("1.000000001")])
async def test_invalid_units_do_not_round_into_a_valid_command(db, amount):
    with pytest.raises(ValueError):
        await db.store.reserve(command(amount=amount))
    assert await db.admin.fetchval("SELECT count(*) FROM attempts") == 0


@pytest.mark.asyncio
async def test_lost_reservation_return_reads_back_committed_identity(db):
    c = command()

    async def lost_reply():
        await db.store.reserve(c)
        raise ConnectionError("synthetic lost commit response")

    with pytest.raises(ConnectionError):
        await lost_reply()
    restored = PgAuthority(db.pool)
    assert await restored.reserve(c) is False
    assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == D("1.001")
    assert await db.admin.fetchval("SELECT count(*) FROM events") == 1


@pytest.mark.asyncio
async def test_active_capacity_and_budget_refusal_keep_existing_holds(db):
    for n in range(32):
        await db.store.reserve(command("pending-" + str(n)))
    with pytest.raises(PermissionError):
        await db.store.reserve(command("overflow"))
    assert len(await db.store.poll()) == 32
    assert await db.admin.fetchval("SELECT active_count FROM account_state") == 32
    assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == D("32.032")


@pytest.mark.asyncio
async def test_sell_holds_base_and_budget_exhaustion_is_atomic(db):
    c = command(side="SELL", amount=D("10"))
    await db.store.reserve(c)
    assert await db.admin.fetchval("SELECT reserved_base FROM account_state") == 10
    await db.admin.execute("UPDATE account_state SET budget_spent=99.99")
    with pytest.raises(PermissionError):
        await db.store.reserve(command("budget-overflow"))
    assert await db.admin.fetchval("SELECT count(*) FROM attempts") == 1
    assert await db.admin.fetchval("SELECT reserved_base FROM account_state") == 10


@pytest.mark.asyncio
async def test_db_disconnect_after_arm_denies_send_and_retains_hold(db):
    c = command()
    adapter, session, enqueue = dispatch(db, c)
    adapter.throttle.clear()
    enqueue()
    await asyncio.wait_for(adapter.armed.wait(), 1)
    await db.pool.close()
    adapter.throttle.set()
    with pytest.raises(asyncpg.InterfaceError):
        await adapter.pending
    assert not session.sent
    assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == D("1.001")
    assert await db.admin.fetchval("SELECT state FROM attempts") == "SEND_UNKNOWN"


async def seed_history(db, history):
    # Corresponding settled roundtrip identities/events, with known fee totals.
    # This is fixture construction, not an importer or full financial replay.
    await db.admin.execute("""INSERT INTO attempts
        SELECT 'synthetic-life','history-'||n,'wire-history-'||n,CASE WHEN n%2=0 THEN 'BUY' ELSE 'SELL' END,
               1,1,0,0,0,'TERMINAL','process-a','synthetic-lineage',1,1,1,'past',
               clock_timestamp()-interval '1 day','exchange-history-'||n
        FROM generate_series(1,$1::int) n""", history)
    await db.admin.execute("""INSERT INTO events(account,event_id,intent,kind,quote_delta,base_delta)
        SELECT 'synthetic-life','fill-'||n,'history-'||n,'FILL',
               CASE WHEN n%2=0 THEN -1 ELSE 1 END,CASE WHEN n%2=0 THEN 1 ELSE -1 END
        FROM generate_series(1,$1::int) n""", history)
    await db.admin.execute("""INSERT INTO events(account,event_id,intent,kind,quote_delta,budget_delta)
        SELECT 'synthetic-life','fee-'||n,'history-'||n,'FEE',-0.001,0.001 FROM generate_series(1,$1::int) n""", history)
    await db.admin.execute("UPDATE account_state SET cash=1000-$1::numeric,budget_spent=$1,observed_at=clock_timestamp()",
                           D(history) * D("0.001"))
    await db.admin.execute("ANALYZE attempts")
    await db.admin.execute("ANALYZE events")


@pytest.mark.asyncio
@pytest.mark.parametrize("history", [0, 1000, 10000])
async def test_scale_actual_executor_send_and_spike_watchdog(db, history, record_property):
    await seed_history(db, history)
    # Independent cold fixture audit. Never called on the measured admission path.
    sums = await db.admin.fetchrow("SELECT sum(quote_delta) AS cash,sum(base_delta) AS base FROM events")
    assert (sums["cash"] or 0) == -D(history) * D("0.001") and (sums["base"] or 0) == 0
    plans = {}
    for name, query in {
        "identity": "SELECT intent FROM attempts WHERE account='synthetic-life' AND intent='history-5000'",
        "active": """SELECT intent,wire,state,exchange_id FROM attempts WHERE account='synthetic-life'
                     AND state IN ('PREPARED','SEND_UNKNOWN','ACKED') ORDER BY intent LIMIT 33""",
    }.items():
        plans[name] = json.loads(await db.admin.fetchval("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query))
    timings, lags, canceled = [], [], []
    adapter = None

    async def heartbeat():
        while True:
            before = time.perf_counter()
            await asyncio.sleep(.01)
            lags.append(max(0, (time.perf_counter() - before) * 1000 - 10))

    async def watchdog():
        while True:
            if adapter is not None and adapter.stopped and not canceled:
                await adapter.cancel_owned()
                canceled.append(time.perf_counter())
            await asyncio.sleep(.01)

    monitor, watch = asyncio.create_task(heartbeat()), asyncio.create_task(watchdog())
    before_queries, before_rows = db.store.queries, db.store.rows
    try:
        await asyncio.sleep(.015)
        poll_start = time.perf_counter()
        assert await db.store.poll() == ()
        poll_ms = (time.perf_counter() - poll_start) * 1000
        sample_counts = []
        for n in range(5):
            c = command("scale-" + str(n))
            adapter, session, enqueue = dispatch(db, c)
            q, r = db.store.queries, db.store.rows
            start = time.perf_counter()
            enqueue()
            await adapter.pending
            timings.append((time.perf_counter() - start) * 1000)
            sample_counts.append({"queries": db.store.queries - q, "returned_rows": db.store.rows - r})
            assert len(session.sent) == 1
            if n != 4:
                assert await db.store.settle_zero_fill(c, "exchange-" + c.wire)
            await asyncio.sleep(.015)
        started = time.perf_counter()
        await adapter.stop()
        while not canceled:
            if watch.done():
                await watch  # Surface cancellation-path exceptions immediately.
            if time.perf_counter() - started > 5:
                pytest.fail("spike watchdog did not reach fake cancel within 5s")
            await asyncio.sleep(.001)
        cancel_ms = (adapter.session.cancels[0][2] - started) * 1000
        assert await db.admin.fetchval("SELECT reserved_cash FROM account_state") == D("1.001")
        await asyncio.sleep(.015)
        result = {"history": history, "poll_ms": poll_ms, "send_samples_ms": timings,
                  "event_loop_lag_ms": max(lags), "cancel_start_ms": cancel_ms,
                  "sample_query_counts": sample_counts, "queries_total": db.store.queries - before_queries,
                  "rows_total": db.store.rows - before_rows, "plans": plans,
                  "scope": "DISPOSABLE_SPOT_SPIKE_NOT_FULL_O8_ASSEMBLY"}
        record_property("life_pg_spike_scale", json.dumps(result, sort_keys=True))
        breaches = {key: (observed, limit(budget)) for key, observed, budget in (
            ("poll_ms", poll_ms, "joint_qualification_settlement"),
            ("send_ms", max(timings), "authorization_final_send"),
            ("lag_ms", max(lags), "event_loop_lag"),
            ("cancel_ms", cancel_ms, "healthy_product_cancel_start")) if observed >= limit(budget)}
        assert not breaches, breaches
        assert len({(x["queries"], x["returned_rows"]) for x in sample_counts}) == 1
    finally:
        for task in (monitor, watch):
            task.cancel()
        await asyncio.gather(monitor, watch, return_exceptions=True)
        if adapter is not None:
            await adapter.stop()
