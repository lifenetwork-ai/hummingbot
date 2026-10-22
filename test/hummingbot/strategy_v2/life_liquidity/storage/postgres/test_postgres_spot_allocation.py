"""Original signed capital/carry preflight before a new SPOT action capture."""
import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_market import market

import pytest

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_intent import PgSpotQuoteIntent


def intent(t, *, side='BUY', quantity='1'):
    return PgSpotQuoteIntent('native-quote', 'main', t.owner, 1, TradeType[side], 0, D(quantity), D('1'))


@pytest.mark.asyncio
async def test_allocation_uses_one_original_reserve_and_one_complete_carry_check(database):
    t = await market(database, clean=True)
    try:
        result = await t.app.allocate_spot('single-source-allocation', intent(t))
        assert result.capital.decision.allowed and result.carry.decision.allowed
        rows = await database[0].fetch('''SELECT command->>'kind' AS kind,command#>>'{command,kind}' AS action
            FROM life_financial.operations WHERE account_id=$1
            AND command->>'kind' IN ('SHARED_CAPITAL','CARRY_CHECK') ORDER BY committed_version''', t.cfg.account_id)
        assert [(r['kind'], r['action']) for r in rows] == [('SHARED_CAPITAL', 'RESERVE'), ('CARRY_CHECK', None)]
        assert len(await t.source.capital.claims(t.cfg.binding)) == 1
        assert not [r for r in t.requests if r['method'] == 'POST']
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('shared_quota', [False, True])
async def test_signed_allocation_keeps_original_sources_and_precedes_action_capture(database, side, shared_quota):
    t = await market(database, clean=True, shared_quota=shared_quota)
    try:
        command = intent(t, side=side)
        capital, carry = t.source.capital.observation, t.source.carry.observation
        started = datetime.now(timezone.utc)
        receipt = await t.app.allocate_spot('new-quote', command)
        assert receipt.trading_permission is False
        assert receipt.capital.decision.allowed and receipt.carry.decision.allowed
        assert t.source.capital.observation == capital and t.source.carry.observation == carry
        claims = await t.source.capital.claims(t.cfg.binding)
        assert len(claims) == 1 and claims[0].wire_id == command.wire_id
        assert claims[0].intent_id == command.intent_id and claims[0].side == side
        frame = await t.source.carry.frame(t.cfg.binding)
        assert int(started.timestamp() * 1000) <= frame['bundle']['account']['snapshot']['observed_at_ms']
        assert frame['bundle']['mark']['observed_at_ms'] == t.public_received_at
        assert not t.source._allocation_basis and not t.source._allocating and not t.source._capture
        assert not t.reader.source.journal._entries
        assert not [r for r in t.requests if r['method'] == 'POST']
        with pytest.raises(StorageError, match='ORIGINAL_OPERATION_REQUIRES_RECONCILIATION'):
            await t.app.allocate_spot('new-quote', command)
        assert len(await t.source.capital.claims(t.cfg.binding)) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['owner', 'controller', 'version'])
async def test_invalid_intent_cannot_trigger_source_refresh_or_money_writes(database, fault):
    t = await market(database, clean=True)
    command = intent(t)
    command = replace(command, **dict(owner=replace(t.owner, epoch=t.owner.epoch + 1)) if fault == 'owner'
                      else dict(controller_id='foreign') if fault == 'controller' else dict(config_version=2))
    try:
        with pytest.raises(StorageError, match='INTENT_INVALID'):
            await t.app.allocate_spot('bad-quote', command)
        assert not t.requests
        assert not await t.source.capital.claims(t.cfg.binding)
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_native_capital_denial_never_becomes_a_new_action_or_http_grant(database):
    t = await market(database, clean=True)
    try:
        receipt = await t.app.allocate_spot('too-large', intent(t, quantity='100000'))
        assert not receipt.capital.decision.allowed and receipt.carry is None
        assert not receipt.trading_permission and not await t.source.capital.claims(t.cfg.binding)
        assert not t.reader.source.journal._entries
        assert not [r for r in t.requests if r['method'] != 'GET']
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['before_reserve', 'after_reserve', 'after_carry', 'cancel_after_reserve'])
async def test_interrupted_allocation_does_not_refresh_or_release_original_claim(database, monkeypatch, fault):
    t = await market(database, clean=True)
    runtime = t.financial._runtime
    carry_method = 'observe_after_capital' if any(m.version == 112 for m in runtime._bundle) else 'observe'
    original_reserve, original_carry = t.source.capital.reserve, getattr(t.source.carry, carry_method)
    mutated = []

    async def reserve(*args, **kwargs):
        if fault == 'before_reserve':
            runtime._ingress_epoch += 1
            mutated.append(True)
        result = await original_reserve(*args, **kwargs)
        if fault in ('after_reserve', 'cancel_after_reserve'):
            mutated.append(True)
            if fault == 'cancel_after_reserve':
                raise asyncio.CancelledError
            runtime._ingress_epoch += 1
        return result

    async def carry(operation, *args, **kwargs):
        result = await original_carry(operation, *args, **kwargs)
        if fault == 'after_carry' and operation.startswith('spot-carry:'):
            runtime._ingress_epoch += 1
            mutated.append(True)
        return result
    monkeypatch.setattr(t.source.capital, 'reserve', reserve)
    monkeypatch.setattr(t.source.carry, carry_method, carry)
    try:
        with pytest.raises(asyncio.CancelledError if fault == 'cancel_after_reserve' else StorageError):
            await t.app.allocate_spot('interrupted', intent(t))
        assert mutated == [True]
        if fault == 'after_carry':
            assert await database[0].fetchval("SELECT count(*) FROM life_financial.operations WHERE account_id=$1 AND command->>'kind'='CARRY_CHECK'", t.cfg.account_id) == 1
        assert t.source._stopped and not runtime.healthy
        assert not t.source._allocation_basis and not t.source._capture
        assert len(await t.source.capital.claims(t.cfg.binding)) == (0 if fault == 'before_reserve' else 1)
        assert not t.reader.source.journal._entries
        assert not [r for r in t.requests if r['method'] != 'GET']
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_original_allocation_callback_cannot_be_consumed_by_another_task(database, monkeypatch):
    t = await market(database, clean=True)
    original, reached = t.source.capital.reserve, []

    async def reserve(*args, **kwargs):
        async def foreign():
            with pytest.raises(StorageError, match='ORIGINAL_TASK_REQUIRED'):
                t.source.capital.observation()
            reached.append(True)
        await asyncio.create_task(foreign())
        return await original(*args, **kwargs)
    monkeypatch.setattr(t.source.capital, 'reserve', reserve)
    try:
        receipt = await t.app.allocate_spot('original-task', intent(t))
        assert reached == [True] and receipt.capital.decision.allowed and receipt.carry.decision.allowed
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_changed_intent_cannot_reuse_operation_to_refresh_or_allocate_again(database):
    t = await market(database, clean=True)
    try:
        command = intent(t)
        first = await t.app.allocate_spot('same-operation', command)
        assert first.capital.decision.allowed and first.carry.decision.allowed
        requests = len(t.requests)
        with pytest.raises(StorageError, match='ORIGINAL_OPERATION_REQUIRES_RECONCILIATION'):
            await t.app.allocate_spot('same-operation', replace(command, intent_id='different', price_usdt=D('1.01')))
        assert len(t.requests) == requests
        assert len(await t.source.capital.claims(t.cfg.binding)) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_unknown_reserve_commit_keeps_native_money_without_an_action_or_recapture(database, monkeypatch):
    t = await market(database, clean=True)
    original, operation = t.source.capital.reserve, []

    async def reserve(command, *args, **kwargs):
        await original(command, *args, **kwargs)
        operation.append(command.operation_id)
        raise StorageUnavailable('SYNTHETIC_LOST_COMMIT_REPLY')
    monkeypatch.setattr(t.source.capital, 'reserve', reserve)
    try:
        with pytest.raises(StorageUnavailable, match='LOST_COMMIT_REPLY'):
            await t.app.allocate_spot('unknown-commit', intent(t))
        assert len(operation) == 1
        assert (await t.source.capital.lookup(operation[0], t.cfg.binding)).replayed
        assert len(await t.source.capital.claims(t.cfg.binding)) == 1
        requests = len(t.requests)
        with pytest.raises(StorageError):
            await t.app.allocate_spot('unknown-commit', intent(t))
        assert len(t.requests) == requests and not t.reader.source.journal._entries
        assert not [r for r in t.requests if r['method'] != 'GET']
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
async def test_allocation_refuses_existing_sdk_makers_instead_of_assuming_quiescence(database, product):
    t = await market(database, clean=True)
    sdk = t.connector if product == 'SPOT' else t.swap_connector
    sdk.start_tracking_order('foreign', '999', 'LIFE-USDT', TradeType.BUY, D('1'), D('1'), OrderType.LIMIT_MAKER)
    try:
        with pytest.raises(StorageError, match='RUNNER_NOT_QUIESCENT'):
            await t.app.allocate_spot('not-quiescent', intent(t))
        assert not t.requests and not await t.source.capital.claims(t.cfg.binding)
        assert not t.reader.source.journal._entries
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_allocation_cannot_renew_an_expired_signed_observation(database, monkeypatch):
    t = await market(database, clean=True)
    original, stamps = t.source.capital.reserve, []

    async def reserve(*args, **kwargs):
        stamps.append(t.source._capture[3].observed_at_ms)
        await asyncio.sleep(1.01)
        return await original(*args, **kwargs)
    monkeypatch.setattr(t.source.capital, 'reserve', reserve)
    try:
        with pytest.raises(StorageError, match='REPORT_REVOKED'):
            await t.app.allocate_spot('stale', intent(t))
        assert len(stamps) == 1
        frame = await t.source.capital.frame(t.cfg.binding)
        # The failed RESERVE is the first financial effect in this path; its
        # expired original source cannot publish even an observation snapshot.
        assert frame['snapshot'] is None
        assert not await t.source.capital.claims(t.cfg.binding)
        assert not t.reader.source.journal._entries and not [r for r in t.requests if r['method'] != 'GET']
    finally:
        await t.financial.close()
