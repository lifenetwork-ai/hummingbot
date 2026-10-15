"""Signed next-session revisions retain the current financial namespace."""
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import facts
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts import ACCOUNTING
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import (
    POLICY,
    command as fill_command,
    prepare_arm,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
    BUDGET,
    CAPACITY,
    NOW,
    RISK,
    create_fixture,
    order,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_recovery import (
    RECOVERY,
    facts as fill_facts,
)
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.config import StrategyConfig, apply_update
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    OwnerMode,
    StorageError,
    StorageUnavailable,
    fingerprint,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys, OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot


def strategy():
    return StrategyConfig.model_validate({'session': {'duration': '1h'}, 'reference': {'mode': 'market', 'lookback': '15m'},
                                         'quotes': {'spreads_bps': ['30'], 'sizes_base': ['2']}})


def signer():
    return OperatorKeys({'operator': b'f' * 32}, {'operator': frozenset(('CONFIGURE', 'ACTIVATE', 'DRAIN', 'RECOVER', 'REVOKE'))})


async def open_store(tmp_path, *, files=None, binding=None, owner=None, clock=None):
    if files is None:
        files, binding, owner = create_fixture(tmp_path)
        binding = replace(binding, config_hash=fingerprint(strategy().model_dump(mode='json')))
    clock = clock or [NOW]
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, config_version=1,
                                   account_lock_root=tmp_path / 'account-locks', create_binding=owner is not None, owner=owner,
                                   clock=lambda: clock[0], fill_policy=POLICY, accounting_policy=ACCOUNTING,
                                   recovery_policy=RECOVERY, operator_keys=signer().verifier(),
                                   control_seed=(json.dumps(signer().seed(binding, {'mode': 'ACTIVE', 'token': owner.json()})).encode()
                                                 if owner is not None else None))
    return store, files, binding, owner, clock


def proposal(owner, changes=None, *, operation_id='stage1', expected_sequence=0):
    from hummingbot.strategy_v2.life_liquidity.storage.financial_configuration import StageFinancialConfiguration
    source = strategy()
    return StageFinancialConfiguration(operation_id, expected_sequence, owner, owner.expires_at,
                                       source.model_dump_json(), apply_update(source, changes or {'session': {'duration': '4h'}}).model_dump_json())


def sign(store, command, view, evidence, clock, *, role='CONFIGURE'):
    return signer().sign('operator', store.binding, role, command.json(), view.cut, evidence,
                         issued_at=clock[0], expires_at=clock[0] + timedelta(seconds=1))


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [{'session': {'duration': '4h'}}, {'reference': {'lookback': '30m'}},
                                     {'quotes': {'spreads_bps': ['40', '80'], 'sizes_base': ['1', '2']}}])
async def test_signed_revision_drains_without_changing_deadline_money_or_active_config(tmp_path, changes):
    store, files, binding, owner, clock = await open_store(tmp_path)
    try:
        await prepare_arm(store, binding, owner)
        await store.apply_fills(fill_command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
        before = await store.snapshot(binding)
        evidence = fill_facts(binding)
        command = proposal(owner, changes)
        signature = sign(store, command, before, evidence, clock)
        images = await store._call(store._raw)
        result = await store.stage_financial_configuration(command, approval=signature, facts=evidence, cut=before.cut)
        assert result.state == 'STAGED' and not result.replayed
        after = await store.snapshot(binding)
        assert after.session == before.session and after.balance == before.balance and after.budget == before.budget
        assert after.session.expires_at == owner.expires_at and after.session.config_version == 1
        assert await store._call(store._raw) == images
        assert (await store.current_owner(binding)).mode is OwnerMode.DRAINING
        state = await store.financial_configuration(binding)
        assert state.pending == command and state.sequence == 1 and state.active_config_version == 1
        assert (await store.stage_financial_configuration(command, approval=signature, facts=evidence, cut=before.cut)).replayed
        with pytest.raises(StorageError, match='SPOT_OWNER_OR_RECOVERY_GATE'):
            await store.prepare(replace(order(owner), operation_id='new', intent_id='new', client_order_id='newwire'), cut=after.cut, owner=owner)
        await store.close()
        store, _, _, _, _ = await open_store(tmp_path, files=files, binding=binding, clock=clock)
        assert (await store.financial_configuration(binding)).pending == command
        assert (await store.snapshot(binding)).budget == before.budget and store._cold_unarmed
        clock[0] += timedelta(seconds=2)
        assert (await store.stage_financial_configuration(command, approval=signature, facts=evidence, cut=before.cut)).replayed
        assert store._cold_unarmed
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'role', 'balance', 'debt', 'scope', 'stale_facts', 'source', 'owner', 'sequence', 'expired'])
async def test_unqualified_revision_does_not_change_any_durable_state(tmp_path, fault):
    store, _, binding, owner, clock = await open_store(tmp_path)
    try:
        command, evidence = proposal(owner), facts(binding)
        before = await store.snapshot(binding)
        if fault == 'balance':
            evidence = replace(evidence, account_quote_usdt=D('99'))
        elif fault == 'debt':
            evidence = replace(evidence, liability_quote_usdt=D('1'))
        elif fault == 'scope':
            evidence = replace(evidence, bill_scope_complete=False)
        elif fault == 'stale_facts':
            evidence = replace(evidence, observed_at=NOW - timedelta(seconds=2))
        elif fault == 'source':
            other = strategy().model_copy(update={'session': strategy().session.model_copy(update={'duration': '2h'})})
            command = replace(command, source_json=other.model_dump_json(), candidate_json=apply_update(other, {'session': {'duration': '4h'}}).model_dump_json())
        elif fault == 'owner':
            command = replace(command, owner=replace(owner, generation=UUID(int=999)))
        elif fault == 'sequence':
            command = replace(command, expected_sequence=1)
        signature = sign(store, command, before, evidence, clock, role='ACTIVATE' if fault == 'role' else 'CONFIGURE')
        if fault == 'signature':
            signature = replace(signature, signature='0' * 128)
        elif fault == 'expired':
            clock[0] += timedelta(seconds=1)
        with pytest.raises(StorageError):
            await store.stage_financial_configuration(command, approval=signature, facts=evidence, cut=before.cut)
        assert (await store.snapshot(binding)).cut == before.cut
        assert (await store.financial_configuration(binding)).pending is None
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_discard_is_signed_append_only_and_never_reactivates_or_resets_versions(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.financial_configuration import DiscardFinancialConfiguration
    store, files, binding, owner, clock = await open_store(tmp_path)
    try:
        command, evidence = proposal(owner), facts(binding)
        before = await store.snapshot(binding)
        signature = sign(store, command, before, evidence, clock)
        await store.stage_financial_configuration(command, approval=signature, facts=evidence, cut=before.cut)
        staged = await store.snapshot(binding)
        activation = OwnerTransition('activate', OwnerMode.ACTIVE, replace(owner, generation=UUID(int=99)))
        with pytest.raises(StorageError, match='CONFIGURATION_HANDOFF_REQUIRED'):
            await store.transition_owner(activation, approval=sign(store, activation, staged, evidence, clock, role='ACTIVATE'),
                                         facts=evidence, cut=staged.cut)
        discard = DiscardFinancialConfiguration('discard1', 1, 'stage1', owner)
        receipt = await store.discard_financial_configuration(discard, approval=sign(store, discard, staged, evidence, clock),
                                                              facts=evidence, cut=staged.cut)
        assert receipt.state == 'DISCARDED'
        state = await store.financial_configuration(binding)
        assert state.pending is None and state.sequence == 2 and state.active_config_version == 1
        assert (await store.current_owner(binding)).mode is OwnerMode.DRAINING and store._cold_unarmed
        assert (await store.read(binding)).resources['control_operations']['stage1']['payload'] == command.json()
        assert (await store.stage_financial_configuration(command, approval=signature, facts=evidence, cut=before.cut)).replayed
        assert (await store.financial_configuration(binding)).pending is None
        current = await store.snapshot(binding)
        with pytest.raises(StorageError):
            await store.stage_financial_configuration(command, approval=sign(store, command, current, evidence, clock), facts=evidence, cut=current.cut)
        await store.close()
        store, _, _, _, _ = await open_store(tmp_path, files=files, binding=binding)
        assert (await store.financial_configuration(binding)).sequence == 2 and store._cold_unarmed
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('written', [False, True])
async def test_unknown_publication_cold_readback_never_replays_native_money(tmp_path, monkeypatch, written):
    store, files, binding, owner, clock = await open_store(tmp_path)
    try:
        command, evidence = proposal(owner), facts(binding)
        before = await store.snapshot(binding)
        signature = sign(store, command, before, evidence, clock)
        images, native = await store._call(store._raw), store._state.commit

        def crash(state):
            if written:
                native(state)
            raise OSError('synthetic lost control reply')
        with monkeypatch.context() as patch:
            patch.setattr(store._state, 'commit', crash)
            with pytest.raises(CommitUnknown):
                await store.stage_financial_configuration(command, approval=signature, facts=evidence, cut=before.cut)
        assert store._uncertain
        await store.close()
        store, _, _, _, _ = await open_store(tmp_path, files=files, binding=binding)
        state = await store.financial_configuration(binding)
        assert (state.pending is not None) == written
        result = await store.stage_financial_configuration(command, approval=signature, facts=evidence, cut=before.cut)
        assert result.replayed == written
        assert (await store.financial_configuration(binding)).sequence == 1
        assert await store._call(store._raw) == images and store._cold_unarmed
    finally:
        await store.close()


@pytest.mark.parametrize('section,value', [('risk', {'max_net_base': '99'}), ('perpetual', {'enabled': True}),
                                           ('economics', {'min_net_edge_bps': '0'}), ('config_version', 99)])
def test_revision_rejects_undeclared_policy_changes(tmp_path, section, value):
    from hummingbot.strategy_v2.life_liquidity.storage.financial_configuration import StageFinancialConfiguration
    config = strategy()
    candidate = apply_update(config, {'session': {'duration': '4h'}}).model_dump(mode='json')
    candidate[section] = {**candidate[section], **value} if isinstance(value, dict) else value
    _, _, owner = create_fixture(tmp_path)
    with pytest.raises(StorageError, match='FINANCIAL_CONFIG_CANDIDATE_INVALID'):
        StageFinancialConfiguration('invalid', 0, owner, owner.expires_at, config.model_dump_json(), json.dumps(candidate))


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['head', 'candidate', 'ownership'])
async def test_projection_audit_rejects_unsigned_draft_or_admission_changes(tmp_path, fault):
    store, _, binding, owner, clock = await open_store(tmp_path)
    try:
        command, evidence = proposal(owner), facts(binding)
        before = await store.snapshot(binding)
        await store.stage_financial_configuration(command, approval=sign(store, command, before, evidence, clock), facts=evidence, cut=before.cut)

        def tamper():
            with store._state.locked() as state:
                if fault == 'head':
                    state['resources']['financial_configuration']['pending_operation_id'] = None
                elif fault == 'candidate':
                    state['resources']['control_operations']['stage1']['payload']['candidate_json'] = strategy().model_dump_json()
                else:
                    state['ownership']['mode'] = 'ACTIVE'
                store._state.commit(state)
        await store._call(tamper)
        with pytest.raises(StorageUnavailable, match='CONTROL_HISTORY_UNVERIFIED'):
            await store.financial_configuration(binding)
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_second_draft_and_wrong_discard_cannot_replace_approved_candidate(tmp_path):
    from hummingbot.strategy_v2.life_liquidity.storage.financial_configuration import DiscardFinancialConfiguration
    store, _, binding, owner, clock = await open_store(tmp_path)
    try:
        command, evidence = proposal(owner), facts(binding)
        before = await store.snapshot(binding)
        await store.stage_financial_configuration(command, approval=sign(store, command, before, evidence, clock), facts=evidence, cut=before.cut)
        current = await store.snapshot(binding)
        for other in (proposal(owner, {'session': {'duration': '8h'}}, operation_id='stage2', expected_sequence=1),
                      DiscardFinancialConfiguration('discard-wrong', 1, 'other-draft', owner)):
            method = store.stage_financial_configuration if hasattr(other, 'candidate_json') else store.discard_financial_configuration
            with pytest.raises(StorageError):
                await method(other, approval=sign(store, other, current, evidence, clock), facts=evidence, cut=current.cut)
        assert (await store.financial_configuration(binding)).pending == command
        assert (await store.snapshot(binding)).cut == current.cut
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_session_expiry_is_rechecked_at_physical_publication(tmp_path, monkeypatch):
    store, _, binding, owner, clock = await open_store(tmp_path)
    try:
        command, evidence = proposal(owner), facts(binding)
        before = await store.snapshot(binding)
        approval = signer().sign('operator', binding, 'CONFIGURE', command.json(), before.cut, evidence,
                                 issued_at=NOW, expires_at=NOW + timedelta(hours=2))
        native = store._control_commit

        def expire(*args, **kwargs):
            clock[0] = owner.expires_at
            return native(*args, **kwargs)
        monkeypatch.setattr(store, '_control_commit', expire)
        with pytest.raises(StorageError, match='FINANCIAL_CONFIG_EXPIRED_AT_COMMIT'):
            await store.stage_financial_configuration(command, approval=approval, facts=evidence, cut=before.cut)
        assert (await store.financial_configuration(binding)).pending is None
        assert (await store.snapshot(binding)).cut == before.cut
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancelled_waiter_retains_worker_exclusion_and_cold_signed_readback(tmp_path):
    import asyncio
    import threading
    store, files, binding, owner, clock = await open_store(tmp_path)
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    try:
        command, evidence = proposal(owner), facts(binding)
        before = await store.snapshot(binding)
        signature = sign(store, command, before, evidence, clock)

        def block():
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(5)
        blocker = asyncio.create_task(store._call(block))
        await asyncio.wait_for(entered.wait(), 2)
        task = asyncio.create_task(store.stage_financial_configuration(command, approval=signature, facts=evidence, cut=before.cut))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store._uncertain and len(store._pending) == 2
        closing = asyncio.create_task(store.close())
        await asyncio.sleep(0)
        assert not closing.done()
        release.set()
        await blocker
        await closing
        store, _, _, _, _ = await open_store(tmp_path, files=files, binding=binding)
        assert (await store.financial_configuration(binding)).pending == command
        assert (await store.stage_financial_configuration(command, approval=signature, facts=evidence, cut=before.cut)).replayed
        assert store._cold_unarmed and (await store.snapshot(binding)).balance == before.balance
    finally:
        release.set()
        await store.close()
