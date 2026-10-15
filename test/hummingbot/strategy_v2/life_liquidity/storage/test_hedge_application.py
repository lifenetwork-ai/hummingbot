"""Hedge children share the repository SWAP path; feeds are synthetic."""
import asyncio
import threading
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import CAPACITY, create_fixture
from test.hummingbot.strategy_v2.life_liquidity.test_hedge_coordinator import setup_coordinator
from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import setup_recovery
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
from hummingbot.strategy_v2.life_liquidity.storage.contracts import Backend, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.file import open_store
from hummingbot.strategy_v2.life_liquidity.storage.joint import JointLimits, open_file_joint
from hummingbot.strategy_v2.life_liquidity.storage.swap import open_file_swap
from hummingbot.strategy_v2.life_liquidity.storage.swap_application import FileSwapApplication


async def scenario(tmp_path):
    hedge, values, market, _ = setup_coordinator(tmp_path / 'native', budget_capacity=50)
    recovery, _, bundle, _ = setup_recovery(tmp_path / 'native', values=values)
    _, binding, owner = create_fixture(tmp_path / 'seed')
    session = values[0]._order_safety_manager.current_session
    binding = replace(binding, config_hash=fingerprint(values[0].config.strategy.model_dump(mode='json')))
    owner = replace(owner, session_id=UUID(session.session_id), epoch=session.epoch,
                    valid_from=values[6].wall, expires_at=session.expires_at)
    limits = replace(CAPACITY, max_operations=128)  # Synthetic acceptance capacity.
    store = await open_store(Backend.FILE, path=tmp_path / 'state.json', binding=binding,
                             limits=limits, create=True, initial={}, owner=owner, clock=lambda: values[6].wall)
    joint = await open_file_joint(store, recovery=recovery, owner=owner, limits=JointLimits(16, 64, 64 * 1024))
    assert await joint.reconcile(binding, owner)
    swap = await open_file_swap(joint)
    app = FileSwapApplication(swap, max_pending=2, ack_timeout_s=1, shutdown_timeout_s=1)

    async def uid():
        return binding.account_uid
    values[4].get_account_uid = uid
    return store, joint, app, hedge, values, market, bundle, owner


@pytest.mark.asyncio
async def test_hedge_plan_prepares_on_worker_then_uses_actual_swap_application(tmp_path, monkeypatch):
    from hummingbot.core.data_type.common import OrderType
    from hummingbot.strategy_v2.life_liquidity.storage.hedge_application import FileHedgeApplication
    from hummingbot.strategy_v2.life_liquidity.storage.swap import expected_wire
    store, _, swap, native, values, market, _, _ = await scenario(tmp_path)
    loop_thread = threading.get_ident()
    original = PolicyState.locked

    def worker_only(journal, *args, **kwargs):
        assert threading.get_ident() != loop_thread
        return original(journal, *args, **kwargs)
    try:
        hedge = await FileHedgeApplication.install(swap, native)
        with monkeypatch.context() as patches:
            patches.setattr(PolicyState, 'locked', worker_only)
            actions = await hedge.plan('plan1', market['value'])
            assert len(actions) == 1
            config = actions[0].executor_config
            assert swap.authorizes_config(config)
            assert await hedge.plan('plan1', market['value']) == []
            swap.submit(config, amount=config.amount, price=config.price, order_type=OrderType.LIMIT_MAKER)

            async def ready():
                while not values[4].sent:
                    await asyncio.sleep(.001)
                return values[4].sent[0]
            sent = await asyncio.wait_for(ready(), 2)
            receipt = swap._issued[config.id][1]
            wire = expected_wire(config, receipt.permit)
            await sent['async_pre_send_check'](wire)
            sent['pre_send_check'](wire)
            await sent['async_on_ack']('123456789')
            await swap.drain()
        assert values[5].get(config.id).state == 'ACKED'
        assert await store._call(values[1].claim_ids) == (config.id,)
    finally:
        await swap.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['uid', 'stale', 'scope', 'cost', 'stop'])
async def test_invalid_hedge_input_never_allocates_or_sends(tmp_path, fault):
    from decimal import Decimal

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.hedge_application import FileHedgeApplication
    store, _, swap, native, values, market, _, _ = await scenario(tmp_path)
    try:
        app = await FileHedgeApplication.install(swap, native)
        observation = market['value']
        if fault == 'uid':
            async def foreign():
                return 'foreign'
            values[4].get_account_uid = foreign
        elif fault == 'stale':
            observation = replace(observation, observed_at_ms=observation.observed_at_ms - 501)
        elif fault == 'scope':
            observation = replace(observation, scope_complete=False)
        elif fault == 'cost':
            observation = replace(observation, available_edge_quote=Decimal('0'))
        else:
            swap.revoke()
        if fault in ('uid', 'stop'):
            with pytest.raises(StorageError):
                await app.plan('plan1', observation)
        else:
            assert await app.plan('plan1', observation) == []
        assert values[1].claim_ids() == () and values[5].all_records() == () and values[4].sent == []
    finally:
        await swap.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['child_origin', 'claim', 'wal', 'swap_receipt', 'hedge_issued', 'plan_receipt'])
async def test_cold_hedge_readback_preserves_attempt_cost_and_never_resends(tmp_path, monkeypatch, stage):
    from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import cold_restore

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.hedge_application import FileHedgeApplication
    store, joint, swap, native, values, market, _, owner = await scenario(tmp_path)
    app = await FileHedgeApplication.install(swap, native)
    target, method = {'child_origin': (app, '_publish'), 'claim': (values[1], 'swap'),
                      'wal': (values[5], 'begin'), 'swap_receipt': (swap.repository, '_publish'),
                      'hedge_issued': (native.journal, 'commit'), 'plan_receipt': (app, '_publish')}[stage]
    original = getattr(target, method)

    def interrupted(*args, **kwargs):
        if stage == 'child_origin' and args[1] != 'child':
            return original(*args, **kwargs)
        if stage == 'plan_receipt' and args[1] != 'done':
            return original(*args, **kwargs)
        if stage == 'swap_receipt' and '-done:' not in args[2]:
            return original(*args, **kwargs)
        if stage == 'hedge_issued' and not any(o['status'] == 'ISSUED' for o in args[0]['orders'].values()):
            return original(*args, **kwargs)
        if stage in ('claim', 'wal'):
            original(*args, **kwargs)
        raise OSError('synthetic hedge interruption')

    with monkeypatch.context() as patches:
        patches.setattr(target, method, interrupted)
        with pytest.raises(CommitUnknown):
            await app.plan('plan1', market['value'])
    before = native.journal._read()
    assert len(before['orders']) == before['attempts'] == 1
    limits, binding = store.limits, store.binding
    recovery = joint._recovery
    await swap.close()
    await store.close()
    restored_native, fresh = cold_restore(recovery, values)
    restored = await open_store(Backend.FILE, path=tmp_path / 'state.json', binding=binding, limits=limits,
                                create=False, clock=lambda: fresh[6].wall)
    try:
        coldjoint = await open_file_joint(restored, recovery=restored_native, owner=owner, limits=joint.limits)
        coldrepo = await open_file_swap(coldjoint)
        coldswap = FileSwapApplication(coldrepo, max_pending=2, ack_timeout_s=1, shutdown_timeout_s=1)
        cold = await FileHedgeApplication.install(coldswap, fresh[0]._hedge_coordinator)
        result = await cold.resume('plan1')
        assert result.children == result.stops == ()
        assert (await cold.resume('plan1')).replayed
        after = fresh[0]._hedge_coordinator.journal._read()
        assert after['attempts'] == 1 and after['cost_hold'] == before['cost_hold']
        assert fresh[4].sent == [] and fresh[2]._permits == {}
        assert not fresh[0]._hedge_coordinator._runtime_enabled
        with pytest.raises(StorageError):
            await cold.plan('new-plan', market['value'])
    finally:
        await restored.close()


@pytest.mark.asyncio
async def test_partial_terminal_hedge_retry_keeps_original_cost_and_only_remaining_position(tmp_path):
    from decimal import Decimal as D

    from hummingbot.core.data_type.common import OrderType
    from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryOrder, RecoveryTrade
    from hummingbot.strategy_v2.life_liquidity.storage.hedge_application import FileHedgeApplication
    from hummingbot.strategy_v2.life_liquidity.storage.swap import expected_wire
    store, joint, swap, native, values, market, bundle, owner = await scenario(tmp_path)
    try:
        app = await FileHedgeApplication.install(swap, native)
        first = (await app.plan('plan1', market['value']))[0].executor_config
        swap.submit(first, amount=first.amount, price=first.price, order_type=OrderType.LIMIT_MAKER)

        async def ready():
            while not values[4].sent:
                await asyncio.sleep(.001)
            return values[4].sent[0]
        sent = await asyncio.wait_for(ready(), 2)
        wire = expected_wire(first, swap._issued[first.id][1].permit)
        await sent['async_pre_send_check'](wire)
        sent['pre_send_check'](wire)
        await sent['async_on_ack']('123456789')
        await swap.drain()
        claim = (await store._call(values[1].claims))[first.id]
        held = native.journal._read()['cost_hold']
        values[6].advance(.1)
        at = values[2].clock_ms()
        values[8]['snapshot'] = replace(values[8]['snapshot'], sequence=2, long_base=D('.75'),
                                        observed_at_ms=at, pending=())
        values[7]['value'] = replace(values[7]['value'], net_contracts=D('3'), snapshot_sequence=2, observed_at_ms=at)
        trade = RecoveryTrade('hedge-trade1', D('.25'), first.price, 'USDT', D('-.00025'), D('.00025'), at)
        proof = RecoveryOrder(claim, '123456789', 'canceled', (trade,))
        bundle['value'] = replace(bundle['value'], snapshot_sequence=2, observed_at_ms=at, orders=(proof,), pending_wire_ids=())
        assert await joint.reconcile(store.binding, owner), joint._recovery.reason_code
        assert values[1].claim_ids() == ()
        assert native.journal._read()['orders'][first.id]['status'] == 'TERMINAL'
        market['value'] = replace(market['value'], sequence=2, observed_at_ms=at)
        second = (await app.plan('plan2', market['value']))[0].executor_config
        assert second.id != first.id and second.amount == D('.75')
        state = native.journal._read()
        assert state['attempts'] == 2 and D(state['cost_hold']) > D(held)
        assert state['orders'][first.id]['trades']['hedge-trade1']['quantity_base'] == '0.25'
        assert values[5].get(first.id).state == 'TERMINAL'
        assert len(values[4].sent) == 1
    finally:
        await swap.close()
        await store.close()
