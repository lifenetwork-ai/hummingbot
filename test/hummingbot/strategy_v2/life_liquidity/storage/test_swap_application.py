"""Actual executor dispatch waits for worker authorization and async ACK."""
import asyncio
import threading
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_repository import fixture
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import dispatch
from test.hummingbot.strategy_v2.life_liquidity.test_swap_executor import wire_for

import pytest

from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode
from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.storage.swap import open_file_swap
from hummingbot.strategy_v2.life_liquidity.storage.swap_application import FileSwapApplication


async def scenario(tmp_path):
    joint, store, recovery, values, bundle, _, owner = await fixture(tmp_path)
    assert await joint.reconcile(store.binding, owner)
    repo = await open_file_swap(joint)
    app = FileSwapApplication(repo, max_pending=2, ack_timeout_s=1, shutdown_timeout_s=1)

    async def uid():
        return store.binding.account_uid
    values[4].get_account_uid = uid
    config = values[11].model_copy(update={'amount': values[11].amount / 12, 'position_action': PositionAction.CLOSE})
    return app, repo, joint, store, recovery, values, bundle, owner, config


@pytest.mark.asyncio
async def test_real_executor_submit_uses_only_worker_journals_and_async_ack(tmp_path, monkeypatch):
    app, repo, _, store, _, values, _, owner, config = await scenario(tmp_path)
    thread = threading.get_ident()
    original = PolicyState.locked

    def worker_only(journal, *args, **kwargs):
        assert threading.get_ident() != thread
        return original(journal, *args, **kwargs)

    try:
        with monkeypatch.context() as patches:
            patches.setattr(PolicyState, 'locked', worker_only)
            action = await app.propose(config)
            dispatch(values[0], values[9], action.executor_config, action)
            for _ in range(100):
                if values[4].sent:
                    break
                await asyncio.sleep(.01)
            sent = values[4].sent[0]
            assert values[5].get(config.id).state == 'SEND_UNKNOWN'
            wire = wire_for(config, sent['order_id'], PositionMode.ONEWAY)
            await sent['async_pre_send_check'](wire)
            sent['pre_send_check'](wire)
            assert 'on_ack' not in sent
            await sent['async_on_ack']('123456789')
            await app.drain()
            assert values[5].get(config.id).state == 'ACKED'
            assert await store._call(values[1].claim_ids) == (config.id,)
        assert app.failure_reason(config.id) is None
        with pytest.raises(Exception):
            app.submit(config, amount=config.amount, price=config.price, order_type=sent['order_type'])
    finally:
        await app.close()
        await store.close()


async def wait_transport(values):
    async def ready():
        while not values[4].sent:
            await asyncio.sleep(.001)
        return values[4].sent[0]
    return await asyncio.wait_for(ready(), 2)


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stop', 'expiry', 'config', 'uid', 'wire', 'mode', 'metadata', 'revoke'])
async def test_changed_final_gate_prevents_request_and_retains_hold(tmp_path, change):
    from datetime import timedelta
    from decimal import Decimal

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    app, repo, _, store, _, values, _, owner, config = await scenario(tmp_path)
    try:
        action = await app.propose(config)
        app.submit(action.executor_config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
        sent = await wait_transport(values)
        wire = wire_for(config, sent['order_id'], PositionMode.ONEWAY)
        if change == 'stop':
            values[0]._order_safety_stopped = True
        elif change == 'expiry':
            values[6].wall = owner.expires_at + timedelta(milliseconds=1)
        elif change == 'config':
            values[0].config.id = 'changed'
        elif change == 'uid':
            async def wrong_uid():
                return 'foreign'
            values[4].get_account_uid = wrong_uid
        elif change == 'wire':
            wire['sz'] = '999'
        elif change == 'mode':
            values[4].position_mode = PositionMode.HEDGE
        elif change == 'metadata':
            values[4]._contract_sizes[config.trading_pair] = Decimal('100')
        else:
            app.revoke_intent(config.id)
        with pytest.raises(StorageError):
            await sent['async_pre_send_check'](wire)
        with pytest.raises(StorageError):
            sent['pre_send_check'](wire)
        assert values[5].get(config.id).state == 'SEND_UNKNOWN'
        assert await store._call(values[1].claim_ids) == (config.id,)
    finally:
        await app.close()
        await store.close()


@pytest.mark.asyncio
async def test_late_ack_after_stop_is_observational_and_boundary_is_one_shot(tmp_path):
    from hummingbot.core.data_type.common import OrderType
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    app, _, _, store, _, values, _, _, config = await scenario(tmp_path)
    try:
        await app.propose(config)
        app.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
        sent = await wait_transport(values)
        wire = wire_for(config, sent['order_id'], PositionMode.ONEWAY)
        await sent['async_pre_send_check'](wire)
        sent['pre_send_check'](wire)
        with pytest.raises(StorageError):
            sent['pre_send_check'](wire)
        with pytest.raises(StorageError):
            await sent['async_pre_send_check'](wire)
        app.revoke()
        await sent['async_on_ack']('123456789')
        await app.drain()
        assert values[5].get(config.id).state == 'ACKED'
        assert await store._call(values[1].claim_ids) == (config.id,)
        assert not app.local_ready()
    finally:
        await app.close()
        await store.close()


@pytest.mark.asyncio
async def test_missing_ack_times_out_without_releasing_or_resending(tmp_path):
    from hummingbot.core.data_type.common import OrderType
    app, _, _, store, _, values, _, _, config = await scenario(tmp_path)
    app.ack_timeout_s = .02  # Synthetic timeout; no production default.
    try:
        await app.propose(config)
        app.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
        await wait_transport(values)
        await app.drain()
        assert app.failure_reason(config.id) == 'SWAP_APPLICATION_ATTEMPT_UNKNOWN'
        assert len(values[4].sent) == 1
        assert values[5].get(config.id).state == 'SEND_UNKNOWN'
        assert await store._call(values[1].claim_ids) == (config.id,)
    finally:
        await app.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['none', 'stop', 'expiry', 'config'])
async def test_repository_application_uses_actual_rest_throttler_and_async_ack(tmp_path, change):
    from datetime import timedelta
    from test.hummingbot.strategy_v2.life_liquidity.test_protected_swap_send import connector_with_transport

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    app, _, _, store, _, values, _, owner, config = await scenario(tmp_path)
    connector, throttle, requests = connector_with_transport({'data': [{'sCode': '0', 'ordId': '123456789'}]})
    connector.enable_protected_trading_pair('LIFE-USDT')
    transports = []

    def submit(**kwargs):
        wire_id = kwargs.pop('order_id')
        side = kwargs.pop('trade_type')
        transports.append(asyncio.create_task(connector._place_order(order_id=wire_id, trade_type=side, **kwargs)))
        return wire_id

    values[4].submit_protected_order = submit
    try:
        await app.propose(config)
        app.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
        await asyncio.wait_for(throttle.entered.wait(), 2)
        assert requests == [] and values[5].get(config.id).state == 'SEND_UNKNOWN'
        if change == 'stop':
            app.revoke()
        elif change == 'expiry':
            values[6].wall = owner.expires_at + timedelta(milliseconds=1)
        elif change == 'config':
            values[0].config.id = 'changed'
        throttle.release.set()
        if change == 'none':
            assert (await asyncio.wait_for(transports[0], 2))[0] == '123456789'
            await app.drain()
            assert len(requests) == 1 and values[5].get(config.id).state == 'ACKED'
        else:
            with pytest.raises(StorageError):
                await asyncio.wait_for(transports[0], 2)
            assert requests == [] and values[5].get(config.id).state == 'SEND_UNKNOWN'
        assert await store._call(values[1].claim_ids) == (config.id,)
    finally:
        for task in transports:
            task.cancel()
        await asyncio.gather(*transports, return_exceptions=True)
        await app.close()
        await store.close()


@pytest.mark.asyncio
async def test_uid_mismatch_before_proposal_never_creates_money_or_wal(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    app, _, _, store, _, values, _, _, config = await scenario(tmp_path)

    async def wrong_uid():
        return 'foreign'
    values[4].get_account_uid = wrong_uid
    try:
        with pytest.raises(StorageError, match='SWAP_APPLICATION_UID_MISMATCH'):
            await app.propose(config)
        assert values[5].all_records() == ()
        assert await store._call(values[1].claim_ids) == () and values[4].sent == []
    finally:
        await app.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['stale', 'joint_unhealthy'])
async def test_final_fence_rejects_qualification_that_expired_or_was_revoked(tmp_path, change):
    from datetime import timedelta

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    app, _, _, store, recovery, values, _, _, config = await scenario(tmp_path)
    try:
        await app.propose(config)
        app.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
        sent = await wait_transport(values)
        wire = wire_for(config, sent['order_id'], PositionMode.ONEWAY)
        await sent['async_pre_send_check'](wire)
        if change == 'stale':
            values[6].wall += timedelta(milliseconds=app.sender.max_age_ms + 1)
        else:
            recovery._healthy = False
        with pytest.raises(StorageError):
            sent['pre_send_check'](wire)
        assert values[5].get(config.id).state == 'SEND_UNKNOWN'
        assert await store._call(values[1].claim_ids) == (config.id,)
    finally:
        await app.close()
        await store.close()


@pytest.mark.asyncio
async def test_timeout_keeps_dispatch_capacity_until_qualified_ack(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    app, _, _, store, _, values, _, _, config = await scenario(tmp_path)
    app.max_pending, app.ack_timeout_s = 1, .02  # Synthetic bounds.
    try:
        await app.propose(config)
        app.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)
        sent = await wait_transport(values)
        wire = wire_for(config, sent['order_id'], PositionMode.ONEWAY)
        await sent['async_pre_send_check'](wire)
        sent['pre_send_check'](wire)
        await app.drain()
        second = config.model_copy(update={'id': 'swap2'})
        with pytest.raises(StorageError, match='SWAP_APPLICATION_BACKPRESSURE'):
            await app.propose(second)
        # A late original-writer ACK accounts the first order but does not
        # erase its timeout or grant a resend. It ends this unknown slot.
        await sent['async_on_ack']('123456789')
        assert app._unknown_attempts == set()
        assert values[5].get(config.id).state == 'ACKED'
    finally:
        await app.close()
        await store.close()
