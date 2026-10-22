"""Real backend loss across configured SWAP lifecycle; fresh signed SDK reopen."""
import asyncio
import json
import time
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.service_process import (
    cold_service_context,
    encoded_config,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_hedge import policy
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_swap_process import joint_money
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import PgHedgeRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import PgHedgeSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_ack import PgSwapAckIngress


def policy_clock_diagnostics(monkeypatch):
    """Observe the real guard; never change its clocks, policy or outcome."""
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_market import PgSpotMarketSource
    current = ContextVar('backend_policy_clock_sample', default=None)
    read, check = PgSpotRepository.quote_admission_policy, PgSpotMarketSource._policy_snapshot

    async def observed(repository, binding):
        value = await read(repository, binding)
        sample = current.get()
        if sample is not None:
            sample['observed_at'] = value.observed_at
            sample['binding_matches'] = value.policy.binding == binding
        return value

    async def guarded(source):
        sample = dict(before=source._now(), started=time.monotonic())
        token = current.set(sample)
        try:
            return await check(source)
        except StorageError as error:
            if str(error) == 'PG_SPOT_MARKET_POLICY_UNQUALIFIED':
                after, elapsed = source._now(), time.monotonic() - sample['started']
                observed_at = sample.get('observed_at')
                error.add_note('policy clock diagnostics (ms): ' + json.dumps(dict(
                    skew_bound=source.reader.source.max_clock_skew_ms,
                    binding_matches=sample.get('binding_matches'),
                    observed_minus_before=None if observed_at is None else (observed_at - sample['before']).total_seconds() * 1000,
                    observed_minus_after=None if observed_at is None else (observed_at - after).total_seconds() * 1000,
                    wall_elapsed=(after - sample['before']).total_seconds() * 1000,
                    monotonic_elapsed=elapsed * 1000), sort_keys=True))
            raise
        finally:
            current.reset(token)
    monkeypatch.setattr(PgSpotRepository, 'quote_admission_policy', observed)
    monkeypatch.setattr(PgSpotMarketSource, '_policy_snapshot', guarded)


@pytest.mark.asyncio
@pytest.mark.parametrize('phase,committed', [
    pytest.param(phase, committed, id=phase + ('_after' if committed else '_before'))
    for phase in ('source', 'preflight', 'prepare', 'arm', 'capture', 'apply', 'delivery', 'terminal', 'settlement', 'release')
    for committed in (False, True)])
async def test_configured_swap_backend_loss_preserves_original_state_without_resend(database, monkeypatch, phase, committed):
    policy_clock_diagnostics(monkeypatch)
    t, service = await configured(database, hedge_policy=policy(),
                                  spot_overrides=dict(owner_duration=timedelta(seconds=300), session_duration=timedelta(seconds=360)))
    closing = phase in ('terminal', 'settlement', 'release')
    stream = None
    if closing:
        await service.execute_hedge('backend-original-hedge')
        order, = t.service_orders
        order['state'] = 'canceled'
        now = datetime.now(timezone.utc).timestamp()
        stream = asyncio.create_task(t.swap_connector._user_stream_event_listener())
        t.swap_connector._set_current_timestamp(now)
        await t.swap_connector._user_stream_tracker.user_stream.put(dict(
            arg=dict(channel='orders'), data=[dict(order, tradeId='', uTime=str(int(now * 1000)))]))
        async with asyncio.timeout(5):
            while order['clOrdId'] in t.swap_connector._order_tracker.active_orders:
                await asyncio.sleep(.001)
    admin = await asyncpg.connect(database[1])
    original, killed = service.runtime._commit, []
    original_transaction, captured_pages = service.runtime._transaction, []

    async def submitted(query, args, *values, **options):
        if phase == 'source' and query == 'SELECT life_financial.spot_capture_provider_collection($1,$2)':
            assert not captured_pages, 'Original refused collection was retried'
            captured_pages.extend(deepcopy(args[1]))
        return await original_transaction(query, args, *values, **options)
    if phase == 'source':
        # Observe exact original page bytes without replacing any arguments,
        # source verdict, SQL statement or physical publication implementation.
        monkeypatch.setattr(service.runtime, '_transaction', submitted)
    target = dict(source='spot_capture_provider_collection', preflight='hedge_command', prepare='hedge_command',
                  arm='swap_boundary_arm', capture='swap_capture_ack', apply='swap_command', delivery='swap_mark_ack',
                  terminal='swap_terminal_command', settlement='hedge_command', release='shared_release_command')[phase]
    active_plan = None
    original_plan = PgHedgeRepository.plan

    async def planned(repository, command, **kwargs):
        nonlocal active_plan
        active_plan = command.operation_id
        try:
            return await original_plan(repository, command, **kwargs)
        finally:
            active_plan = None
    monkeypatch.setattr(PgHedgeRepository, 'plan', planned)

    async def lost(transaction):
        if _current_trace.get().operation != target:
            return await original(transaction)
        if phase in ('preflight', 'prepare'):
            selected = (PgHedgeSource.preflight_operation_id('backend-original-hedge') if phase == 'preflight'
                        else 'backend-original-hedge')
            if active_plan != selected:
                return await original(transaction)
        assert not killed, 'Configured service retried an uncertain COMMIT'
        # Source-consuming terminal/release passes its real CheckedCommit
        # wrapper here. Keep that wrapper intact; the owning physical trace
        # already pins the acquired backend before any command executes.
        pid = _current_trace.get().backend_pid
        assert type(pid) is int and pid > 0
        if committed:
            await original(transaction)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        killed.append(pid)
        raise ConnectionError('synthetic configured-service lost COMMIT reply')
    monkeypatch.setattr(service.runtime, '_commit', lost)
    try:
        # SDK-owned submission reports unknown; direct source/settlement calls
        # expose COMMIT uncertainty. Every case must hit its physical backend.
        sdk_phase = phase in ('arm', 'capture', 'apply', 'delivery')
        with pytest.raises(StorageError if sdk_phase else CommitUnknown,
                           match='PG_HEDGE_SDK_SUBMISSION_UNKNOWN' if sdk_phase else 'PG_COMMIT_UNKNOWN'):
            if closing:
                await service.settle_hedge('backend-original-close', 'backend-original-hedge')
            else:
                await service.execute_hedge('backend-original-hedge')
        assert len(killed) == 1 and not service.runtime.healthy and service._revoked
        physical = [d for d in service.runtime.diagnostics
                    if d.operation == target and d.backend_pid == killed[0] and d.outcome == 'commit_unknown']
        assert len(physical) == 1
        assert physical[0].outcome == 'commit_unknown' and physical[0].error_phase == 'commit'
        assert physical[0].commit_attempted and not physical[0].commit_confirmed
        wire_count = int(phase not in ('source', 'preflight', 'prepare', 'arm'))
        assert len(t.service_orders) == wire_count
        manifest = dict(config=encoded_config(t.cfg), owner=t.owner.json(), controller=service.config.model_dump_json(),
                        sources=[asdict(r.source) for r in service.reporting.events], account_config=t.config,
                        balance=t.balance, positions=t.positions, orders=t.service_orders)
    finally:
        await admin.close()
        try:
            if phase == 'arm' and committed:
                # Report missing unknown scope while still closing all pools.
                with pytest.raises(StorageError, match='PG_JOINT_SOURCE_ORDER_SCOPE'):
                    await service.close()
            else:
                await service.close()
        finally:
            if stream is not None:
                stream.cancel()
                await asyncio.gather(stream, return_exceptions=True)
        assert service._close_task.done() and service.runtime._closing and not service.runtime._tasks
        assert not service.events._registered
    before = await joint_money(database[0], t.cfg.account_id)
    original_arm = await database[0].fetch("SELECT * FROM life_financial.swap_intent_events WHERE account_id=$1 AND kind='SWAP_ARM'", t.cfg.account_id)
    source_rows = None
    if phase == 'source':
        assert captured_pages and len({p['collection_id'] for p in captured_pages}) == 1
        source_rows = await database[0].fetch(
            'SELECT * FROM life_financial.spot_provider_pages WHERE account_id=$1 AND collection_id=$2 ORDER BY sequence',
            t.cfg.account_id, captured_pages[0]['collection_id'])
        assert len(source_rows) == (len(captured_pages) if committed else 0)
        for row, page in zip(source_rows, captured_pages):
            assert json.loads(row['body']) == page
    context, cold = cold_service_context(manifest), None
    try:
        if phase == 'arm' and committed:
            # The database committed ARM but HTTP never ran. The cold service
            # cannot infer definite absence from a missing exchange order:
            # preserve SEND_UNKNOWN and refuse complete source qualification.
            with pytest.raises(StorageError, match='PG_JOINT_SOURCE_ORDER_SCOPE'):
                await context.reopen(False)
            assert await joint_money(database[0], t.cfg.account_id) == before
            assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts WHERE account_id=$1', t.cfg.account_id) == 'SEND_UNKNOWN'
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims WHERE account_id=$1', t.cfg.account_id) == 1
            assert context.calls and all(call['method'] == 'GET' for call in context.calls)
            assert all(not sdk._order_tracker.active_orders for sdk in context.connectors.values())
            return
        # Observation startup never gains a new sender from a received ACK.
        cold = await context.reopen(False)
        assert await joint_money(database[0], t.cfg.account_id) == before
        if source_rows is not None:
            assert await database[0].fetch('SELECT * FROM life_financial.spot_provider_pages WHERE account_id=$1 AND collection_id=$2 ORDER BY sequence',
                                           t.cfg.account_id, captured_pages[0]['collection_id']) == source_rows
        terminal = int(phase in ('settlement', 'release') or phase == 'terminal' and committed)
        settled = int(phase == 'release' or phase == 'settlement' and committed)
        released = int(phase == 'release' and committed)
        prepared = phase not in ('source', 'preflight') and (phase != 'prepare' or committed)
        attempts = await database[0].fetch('SELECT * FROM life_financial.send_attempts WHERE account_id=$1', t.cfg.account_id)
        if prepared:
            attempt, = attempts
            expected = ('TERMINAL' if terminal else 'ACKED' if closing else
                        'PREPARED' if phase == 'prepare' or phase == 'arm' and not committed else
                        'ACKED' if phase == 'delivery' or phase == 'apply' and committed else 'SEND_UNKNOWN')
            assert attempt['state'] == expected
        else:
            assert not attempts
        assert len(await cold.source.capital.claims(t.cfg.binding)) == int(prepared) - released
        for table, count in (('swap_terminal_facts', terminal), ('hedge_settlements', settled), ('shared_claim_releases', released)):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table + ' WHERE account_id=$1', t.cfg.account_id) == count
        if phase in ('source', 'preflight', 'prepare'):
            root = await cold.hedge.repository.lookup('backend-original-hedge', t.cfg.binding)
            assert (root is not None) == (phase == 'prepare' and committed)
            preflight = await cold.hedge.repository.lookup(PgHedgeSource.preflight_operation_id('backend-original-hedge'), t.cfg.binding)
            assert (preflight is not None) == (phase == 'prepare' or phase == 'preflight' and committed)
        balance = await cold.financial.balance(t.cfg.binding)
        assert balance.total_base_life == 5 and balance.total_quote_usdt == 10
        assert cold.hedge._sdk_child is None and not cold._owned_executors and not cold.journal._entries
        assert all(not sdk._order_tracker.active_orders for sdk in context.connectors.values())
        ingress = PgSwapAckIngress(cold.hedge.source.boundary)
        pending = await ingress.pending()
        assert len(pending) == int(phase in ('capture', 'apply', 'delivery') and not (
            phase == 'capture' and not committed or phase == 'delivery' and committed))
        for received in pending:
            assert (await ingress.deliver(received)).state == 'ACKED'
            assert (await ingress.deliver(received)).replayed
        assert not await ingress.pending()
        after = await cold.financial.balance(t.cfg.binding)
        assert after == balance
        # Original receipt delivery can update ACK metadata, never monetary
        # buckets, claims, terminal/release records or any order on the wire.
        repaired = await joint_money(database[0], t.cfg.account_id)
        for table in before.keys() - {'send_attempts', 'swap_ack_deliveries', 'swap_intent_events'}:
            assert repaired[table] == before[table], table
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.swap_intent_events WHERE account_id=$1 AND kind='SWAP_ACK'", t.cfg.account_id) == int(
            phase not in ('source', 'preflight', 'prepare', 'arm') and (phase != 'capture' or committed))
        assert await database[0].fetch("SELECT * FROM life_financial.swap_intent_events WHERE account_id=$1 AND kind='SWAP_ARM'", t.cfg.account_id) == original_arm
        if attempts:
            saved = await database[0].fetchrow('SELECT * FROM life_financial.send_attempts WHERE account_id=$1', t.cfg.account_id)
            for key in set(attempt.keys()) - {'state', 'exchange_order_id', 'version'}:
                assert saved[key] == attempt[key], key
            assert saved['version'] >= attempt['version']
            if pending:
                assert saved['state'] == 'ACKED' and saved['exchange_order_id'] == manifest['orders'][0]['ordId']
        with pytest.raises(StorageError, match='PG_SERVICE_HEDGE_NOT_ENABLED'):
            await cold.execute_hedge('backend-original-hedge')
        assert all(call['method'] == 'GET' for call in context.calls)
        assert len(t.service_orders) == wire_count
    finally:
        if cold is not None:
            await cold.close()
