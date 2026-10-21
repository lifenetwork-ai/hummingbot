"""A new native capture is born once after auditing its immutable business intent."""
import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_actions import setup, transition
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_runner_journal import assembled

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.actions import RunnerActionFacts, SpotQuoteAction
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError


def template(owner):
    cfg = OrderExecutorConfig(id='intent1', controller_id='controller1', timestamp=0.0,
                              connector_name='okx', trading_pair='LIFE-USDT', side=TradeType.BUY,
                              amount=D('2'), price=D('1'), level_id='0', execution_strategy=ExecutionStrategy.LIMIT_MAKER)
    return SpotQuoteAction(cfg.id, cfg.controller_id, str(owner.session_id), owner.epoch,
                           1, cfg.trading_pair, cfg.side.name, 0, cfg.model_dump_json())


@pytest.mark.asyncio
@pytest.mark.parametrize('offset_ms', [-50, 50])
async def test_native_capture_dispatch_samples_new_runner_facts_with_a_bounded_lagging_app_clock(database, offset_ms):
    import time
    repo, cfg, owner, _, app, _, _, dispatch, active, _ = await assembled(database, native_runner_scope=True)
    # A moving synthetic app clock, calibrated once before this new action.
    # Its 50-ms offset is within the configured service's existing 100-ms skew
    # allowance. No issued action or previously sampled fact is re-dated.
    primary = await database[0].fetchval('SELECT clock_timestamp()')
    started = time.monotonic()

    def lagging_clock():
        return primary + timedelta(seconds=time.monotonic() - started, milliseconds=offset_ms)

    sampled = []

    async def facts():
        value = RunnerActionFacts(cfg.binding, lagging_clock(), 'controller1', tuple(sorted(active)), True)
        sampled.append(value)
        return value
    app.clock, app.runner_facts = lagging_clock, facts
    try:
        ticket = await app.preflight_publication('native-clock-capture')
        action = await app.capture_and_publish('native-clock-capture', template(owner), _publication=ticket)
        original_timestamp = action.executor_config.timestamp
        dispatch.submit([action])
        await dispatch.drain()
        assert app._entry(action).accepted, 'new runner scope was compared across unsynchronized clock domains'
        assert action.executor_config.timestamp == original_timestamp
        assert not sampled  # Native transitions never re-date a host-clock fact.
        times = await database[0].fetchrow('''SELECT
            max(recorded_at) FILTER(WHERE command->>'kind'='SPOT_ACTION_CLAIM') AS claimed,
            max((command#>>'{command,facts,observed_at}')::timestamptz)
                FILTER(WHERE command->>'kind'='SPOT_ACTION_TRANSITION') AS sampled
            FROM life_financial.operations WHERE account_id=$1''', cfg.account_id)
        assert times['sampled'] >= times['claimed']
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.reservations') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_native_runner_clock_anchor_does_not_refresh_a_delayed_scope_or_issue_a_new_capture(database):
    repo, cfg, owner, _, app, _, _, dispatch, active, _ = await assembled(
        database, native_runner_scope=True, native_scope_delay=1.05)
    try:
        ticket = await app.preflight_publication('clock-capture')
        action = await app.capture_and_publish('clock-capture', template(owner), _publication=ticket)
        original = app._capture(action)
        dispatch.submit([action])
        await dispatch.drain()
        assert not app._entry(action).accepted and app._entry(action).revoked
        # The executor was created before its SQL acceptance failed. Retain
        # that original uncertain scope for explicit cleanup/reconciliation;
        # a stale fact cannot erase it or grant another dispatch/send.
        assert active == {'intent1'}
        assert not app.local_runner_create_fence(action)
        assert not await app.async_authorize_runner_create_action(action)
        stored, = await repo.quote_actions(cfg.binding)
        assert stored.state == 'PROPOSED' and stored.action == original
        assert app._capture(action) == original
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.reservations') == 0
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_native_scope_sampling_still_allows_reconciliation_after_local_risk_revocation(database):
    repo, cfg, owner, _, app, _, _, dispatch, active, ready = await assembled(database, native_runner_scope=True)
    try:
        ticket = await app.preflight_publication('clock-capture')
        action = await app.capture_and_publish('clock-capture', template(owner), _publication=ticket)
        dispatch.submit([action])
        await dispatch.drain()
        assert app._entry(action).accepted
        active.clear()  # The original fake runner has stopped its executor.
        ready[0] = False
        app.revoke()
        await app.reconcile(action)
        stored, = await repo.quote_actions(cfg.binding)
        # An accepted executor with no PREPARE/attempt is an unused capture;
        # native abandonment closes it as REJECTED without creating money.
        assert stored.state == 'REJECTED'
        assert not app.local_runner_create_fence(action)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['task', 'journal', 'used'])
async def test_runner_scope_clock_capture_is_original_one_use_and_grants_no_action(database, fault):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import _RunnerFactCapture
    repo, cfg, _, _, app, _, _, dispatch, active, ready = await assembled(database)
    try:
        at = await database[0].fetchval('SELECT clock_timestamp()')
        capture = _RunnerFactCapture(app, at)
        journal = object() if fault == 'journal' else app
        if fault == 'used':
            assert capture.take(app) == at

        async def consume():
            return capture.take(journal)
        with pytest.raises(StorageError, match='SCOPE_CAPTURE_ORIGIN_REQUIRED'):
            await (asyncio.create_task(consume()) if fault == 'task' else consume())
        assert not app._entries and not active and not await repo.quote_actions(cfg.binding)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_native_capture_is_created_after_current_audit_and_preserves_all_business_fields(database):
    repo, cfg, owner = await setup(database)
    try:
        original = template(owner)
        before = await repo.snapshot(cfg.binding)
        c = database[0]
        await c.execute('CREATE SEQUENCE life_financial.test_capture_audits; CREATE SEQUENCE life_financial.test_capture_audited_at MINVALUE 0 START 0')
        definition = await c.fetchval("SELECT pg_get_functiondef('life_financial.spot_actions_verified(uuid)'::regprocedure)")
        await c.execute(definition.replace('BEGIN', '''BEGIN
            IF nextval('life_financial.test_capture_audits')=1 THEN
                PERFORM pg_sleep(.05);
                PERFORM setval('life_financial.test_capture_audited_at',floor(extract(epoch FROM clock_timestamp())*1000)::bigint);
            END IF;''', 1))
        receipt, captured = await repo.capture_quote_action('native-capture', original, cut=before.cut, owner=owner)
        assert receipt.states == ((original.intent_id, 'PROPOSED'),) and not receipt.replayed
        old, actual = json.loads(original.payload_json), json.loads(captured.payload_json)
        timestamp = actual.pop('timestamp')
        assert D(str(timestamp)) * 1000 >= await c.fetchval('SELECT last_value FROM life_financial.test_capture_audited_at')
        assert timestamp > 0 and old.pop('timestamp') == 0
        assert actual == old and json.loads(original.payload_json)['timestamp'] == 0
        assert captured.json() | {'payload_json': original.payload_json} == original.json()
        now = await database[0].fetchval('SELECT clock_timestamp()')
        assert now.timestamp() - 1 < timestamp <= now.timestamp()
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.reservations') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_capture_rejects_an_issued_timestamp_and_permanent_identity_reuse(database):
    repo, cfg, owner = await setup(database)
    try:
        original = template(owner)
        _, captured = await repo.capture_quote_action('native-capture', original, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        cut = (await repo.observation_cut(cfg.binding)).cut
        with pytest.raises(StorageError, match='ACTION_IDENTITY_USED'):
            await repo.capture_quote_action('native-capture', original, cut=cut, owner=owner)
        rejected = await transition(database, cfg, captured, 'REJECTED')
        await repo.transition_quote_action(rejected, cut=cut, owner=owner)
        cut = (await repo.observation_cut(cfg.binding)).cut
        old = json.loads(captured.payload_json)
        old['id'] = 'second'
        issued = replace(captured, intent_id='second', payload_json=json.dumps(old, separators=(',', ':')))
        with pytest.raises(StorageError, match='CAPTURE_TEMPLATE_INVALID'):
            await repo.capture_quote_action('cannot-renew', issued, cut=cut, owner=owner)
        rows = await repo.quote_actions(cfg.binding)
        assert len(rows) == 1 and rows[0].action == captured
        assert await repo.lookup_quote_action('cannot-renew', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_a_native_capture_still_expires_with_fresh_admission_facts(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import command
    repo, cfg, owner = await setup(database)
    try:
        await database[0].execute('''INSERT INTO life_financial.spot_quote_admission_policies VALUES(
            $1,$2,'liquidity_service',0,0,.01,.01,.01,1000,1000,1000,1000)''', cfg.account_id, cfg.binding.policy_hash)
        _, captured = await repo.capture_quote_action('native-capture', template(owner), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        dispatched = await transition(database, cfg, captured, 'DISPATCHED')
        await repo.transition_quote_action(dispatched, cut=(await repo.observation_cut(cfg.binding)).cut, owner=owner)
        await asyncio.sleep(1.05)
        fresh = await command(database, cfg, owner, captured)
        with pytest.raises(StorageError, match='QUOTE_CAPTURE_STALE'):
            await repo.prepare_quote_action(fresh, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.attempt(captured.intent_id, cfg.binding) is None
        assert (await repo.quote_actions(cfg.binding))[0].action == captured
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['copied', 'task', 'operation', 'ingress'])
async def test_native_capture_consumes_the_original_task_and_ingress_sealed_publication(database, fault):
    repo, cfg, owner, _, app, _, _, dispatch, active, _ = await assembled(database)
    try:
        ticket = await app.preflight_publication('native-capture')
        if fault == 'ingress':
            repo._runtime._ingress_epoch += 1
        with pytest.raises(StorageError, match='PREFLIGHT_ORIGIN_REQUIRED'):
            work = app.capture_and_publish(
                'other' if fault == 'operation' else 'native-capture', template(owner),
                _publication=object() if fault == 'copied' else ticket)
            await (asyncio.create_task(work) if fault == 'task' else work)
        with pytest.raises(StorageError, match='PREFLIGHT_ORIGIN_REQUIRED'):
            await app.capture_and_publish('native-capture', template(owner), _publication=ticket)
        assert not app._entries and not active and not await repo.quote_actions(cfg.binding)
        assert await repo.attempt('intent1', cfg.binding) is None
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_native_capture_requires_actual_runner_acceptance_and_is_not_a_send_grant(database):
    repo, cfg, owner, _, app, _, _, dispatch, active, _ = await assembled(database)
    try:
        ticket = await app.preflight_publication('native-capture')
        action = await app.capture_and_publish('native-capture', template(owner), _publication=ticket)
        assert action.executor_config.timestamp > 0
        assert not app._entry(action).accepted and not active
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PROPOSED'
        dispatch.submit([action])
        await dispatch.drain()
        assert active == {'intent1'} and app._entry(action).accepted
        assert (await repo.quote_actions(cfg.binding))[0].state == 'DISPATCHED'
        assert await repo.attempt('intent1', cfg.binding) is None
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.reservations') == 0
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_unknown_native_capture_commit_keeps_proposal_without_local_dispatch_adoption(database, monkeypatch):
    repo, cfg, owner, _, app, _, _, dispatch, active, _ = await assembled(database)
    commit = repo._runtime._commit

    async def lost(transaction):
        await commit(transaction)
        raise OSError('synthetic reply lost after physical commit')

    try:
        ticket = await app.preflight_publication('native-capture')
        monkeypatch.setattr(repo._runtime, '_commit', lost)
        with pytest.raises(CommitUnknown):
            await app.capture_and_publish('native-capture', template(owner), _publication=ticket)
        monkeypatch.setattr(repo._runtime, '_commit', commit)
        assert not app._entries and not active
        rows = await repo.quote_actions(cfg.binding)
        assert len(rows) == 1 and rows[0].state == 'PROPOSED'
        assert json.loads(rows[0].action.payload_json)['timestamp'] > 0
        assert await repo.lookup_quote_action('native-capture', cfg.binding) is not None
        with pytest.raises(StorageError):
            await app.capture_and_publish('native-capture', template(owner), _publication=ticket)
        assert not app._entries and not active and await repo.attempt('intent1', cfg.binding) is None
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_native_capture_rechecks_current_generation_at_physical_commit(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    repo, cfg, owner, _, app, _, _, dispatch, active, _ = await assembled(database)
    try:
        ticket = await app.preflight_publication('native-capture')
        await install_generation_fault(database)
        original = repo._runtime._commit

        async def changed(transaction):
            await transaction._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            await original(transaction)

        monkeypatch.setattr(repo._runtime, '_commit', changed)
        with pytest.raises(StorageError):
            await app.capture_and_publish('native-capture', template(owner), _publication=ticket)
        assert not app._entries and not active
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.quote_actions') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await dispatch.close()
        await repo.close()


@pytest.mark.asyncio
async def test_native_capture_endpoint_is_writer_only_and_revocation_is_checked_on_admission(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.access import NATIVE_CAPTURE_ENTRYPOINT
    repo, cfg, owner = await setup(database)
    try:
        for principal in ('life_pg2_runtime', 'life_pg2_reporter'):
            allowed = await database[0].fetchval(
                "SELECT has_function_privilege($1,$2,'EXECUTE')", principal, NATIVE_CAPTURE_ENTRYPOINT)
            assert allowed is (principal == 'life_pg2_runtime')
        cut = (await repo.observation_cut(cfg.binding)).cut
        await database[0].execute('REVOKE EXECUTE ON FUNCTION ' + NATIVE_CAPTURE_ENTRYPOINT + ' FROM life_pg2_runtime')
        with pytest.raises(StorageError):
            await repo.capture_quote_action('native-capture', template(owner), cut=cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.quote_actions') == 0
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['definite_rejection', 'commit_reply', 'cancel_after_commit'])
async def test_native_account_capture_consumes_business_identity_before_its_first_attempt(database, monkeypatch, fault):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_producer import producer

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent
    t, source = await producer(database)
    journal = source.journal
    repo = journal.repository
    intent = PgSpotQuoteIntent('native-capture', 'main', t.owner, 1, TradeType.BUY, 0, D('1'), D('.97'))
    original = repo.capture_quote_action
    commit = repo._runtime._commit

    async def interrupted(*args, **kwargs):
        if fault == 'definite_rejection':
            raise StorageError('SYNTHETIC_REJECTION')
        await original(*args, **kwargs)
        raise asyncio.CancelledError()

    async def lost(transaction):
        await commit(transaction)
        raise OSError('synthetic lost capture commit reply')

    try:
        allocation = await t.app.allocate_spot('capture-allocation', intent)
        assert allocation.capital.decision.allowed and allocation.carry.decision.allowed
        ticket = await journal.preflight_publication('native-capture')
        if fault == 'commit_reply':
            monkeypatch.setattr(repo._runtime, '_commit', lost)
        else:
            monkeypatch.setattr(repo, 'capture_quote_action', interrupted)
        expected = asyncio.CancelledError if fault == 'cancel_after_commit' else StorageError
        with pytest.raises(expected):
            await source.capture_and_publish_intent_ready(intent, 'native-capture', _publication=ticket)
        monkeypatch.setattr(repo._runtime, '_commit', commit)
        monkeypatch.setattr(repo, 'capture_quote_action', original)
        assert source._intents[intent.intent_id] == (intent, None)
        assert intent.intent_id not in journal._entries and not source._scheduled
        with pytest.raises(StorageError):
            await source.capture_and_publish_intent_ready(intent, 'native-capture', _publication=ticket)
        rows = await repo.quote_actions(t.cfg.binding)
        assert len(rows) == (0 if fault == 'definite_rejection' else 1)
        assert not [r for r in t.requests if r['method'] == 'POST']
        assert await repo.attempt(intent.intent_id, t.cfg.binding) is None
        claims = await t.source.capital.claims(t.cfg.binding)
        assert len(claims) == 1 and claims[0].intent_id == intent.intent_id
    finally:
        await t.financial.close()
