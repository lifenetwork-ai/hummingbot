"""A staged revision's source image is evidence, never import/send authority."""
import asyncio
import io
import json
import threading
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_control_plane import facts
from test.hummingbot.strategy_v2.life_liquidity.storage.test_financial_configuration import open_store, proposal, sign
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts import nav, transfer
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import (
    command as fill_command,
    prepare_arm,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import order
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_recovery import facts as fill_facts

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.configuration_handoff import FinancialConfigurationSource
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial_configuration import DiscardFinancialConfiguration
from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand


async def stage(store, owner, clock, evidence):
    before = await store.snapshot(store.binding)
    command = proposal(owner)
    await store.stage_financial_configuration(command, approval=sign(store, command, before, evidence, clock),
                                              facts=evidence, cut=before.cut)
    return command, await store.snapshot(store.binding)


@pytest.mark.asyncio
async def test_source_retains_exact_history_holds_loss_and_original_deadline_across_restart(tmp_path):
    store, files, binding, owner, clock = await open_store(tmp_path)
    try:
        await prepare_arm(store, binding, owner)
        await store.apply_fills(fill_command(owner), cut=(await store.snapshot(binding)).cut, owner=owner)
        evidence = fill_facts(binding)
        command, view = await stage(store, owner, clock, evidence)
        images = await store._call(store._raw)
        checkpoint = files.binding.read_bytes()
        source = await store.configuration_handoff_source(binding, facts=evidence, cut=view.cut)
        assert source.checkpoint_bytes == checkpoint and dict(source.journal_images) == images
        assert source.pending == command and source.cut == view.cut and source.scope == 'CORE_SPOT'
        assert source.unresolved_intents == ('intent1',)
        assert view.balance.total_quote_usdt == D('8.98') and view.balance.reserved_quote_usdt > 0
        assert view.budget.session_execution_loss_usdt == D('.12')
        assert json.loads(dict(source.journal_images)['session'])['primary']['expires_at'] == owner.expires_at.isoformat()
        state = json.loads(source.checkpoint_bytes)['state']
        assert 'trade1' in state['resources']['fills'] and 'control:stage1' in state['operations']
        assert source.content_digest == fingerprint(source.manifest())
        assert (await store.snapshot(binding)).cut == view.cut
        assert (await store.current_owner(binding)).mode is OwnerMode.DRAINING
        assert not hasattr(source, 'send_authorized') and not hasattr(source, 'activate')
        await store.close()
        store, _, _, _, _ = await open_store(tmp_path, files=files, binding=binding, clock=clock)
        cold = await store.configuration_handoff_source(binding, facts=evidence, cut=view.cut)
        assert cold == source and store._cold_unarmed
        clock[0] += timedelta(milliseconds=100)
        refreshed = await store.validate_configuration_handoff_source(source, facts=replace(evidence, observed_at=clock[0]), cut=view.cut)
        assert refreshed.content_digest == source.content_digest and refreshed.facts != source.facts
        assert (await store.snapshot(binding)).cut == view.cut
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing_stage', 'stale_cut', 'balance', 'debt', 'scope', 'age'])
async def test_source_denies_unqualified_facts_without_writes(tmp_path, fault):
    store, files, binding, owner, clock = await open_store(tmp_path)
    try:
        evidence = facts(binding)
        original = await store.snapshot(binding)
        if fault != 'missing_stage':
            _, view = await stage(store, owner, clock, evidence)
        else:
            view = original
        if fault == 'balance':
            evidence = replace(evidence, account_quote_usdt=D('99'))
        elif fault == 'debt':
            evidence = replace(evidence, liability_base_life=D('1'))
        elif fault == 'scope':
            evidence = replace(evidence, runner_scope_complete=False)
        elif fault == 'age':
            clock[0] += timedelta(seconds=2)
        checkpoint, images = files.binding.read_bytes(), await store._call(store._raw)
        with pytest.raises(StorageError):
            await store.configuration_handoff_source(binding, facts=evidence, cut=original.cut if fault == 'stale_cut' else view.cut)
        assert files.binding.read_bytes() == checkpoint and await store._call(store._raw) == images
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_facts_must_remain_fresh_through_physical_capture(tmp_path, monkeypatch):
    store, files, binding, owner, clock = await open_store(tmp_path)
    try:
        evidence = facts(binding)
        _, view = await stage(store, owner, clock, evidence)
        before = files.binding.read_bytes()
        original = store._verify_terminal_projection

        def delayed(resources):
            original(resources)
            clock[0] += timedelta(seconds=2)
        monkeypatch.setattr(store, '_verify_terminal_projection', delayed)
        with pytest.raises(StorageError, match='FINANCIAL_CONFIG_FACTS_UNQUALIFIED'):
            await store.configuration_handoff_source(binding, facts=evidence, cut=view.cut)
        assert files.binding.read_bytes() == before
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_discard_and_later_revision_invalidate_previously_captured_source(tmp_path):
    store, _, binding, owner, clock = await open_store(tmp_path)
    try:
        evidence = facts(binding)
        _, view = await stage(store, owner, clock, evidence)
        source = await store.configuration_handoff_source(binding, facts=evidence, cut=view.cut)
        command = DiscardFinancialConfiguration('discard', 1, 'stage1', owner)
        await store.discard_financial_configuration(command, approval=sign(store, command, view, evidence, clock), facts=evidence, cut=view.cut)
        view = await store.snapshot(binding)
        next_command = proposal(owner, {'reference': {'lookback': '30m'}}, operation_id='stage2', expected_sequence=2)
        await store.stage_financial_configuration(next_command, approval=sign(store, next_command, view, evidence, clock), facts=evidence, cut=view.cut)
        current = await store.snapshot(binding)
        with pytest.raises(StorageError, match='CONFIG_HANDOFF_SOURCE_CHANGED'):
            await store.validate_configuration_handoff_source(source, facts=evidence, cut=current.cut)
        assert (await store.financial_configuration(binding)).pending == next_command
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_self_consistent_hashes_do_not_authorize_adopting_a_different_checkpoint(tmp_path):
    store, files, binding, owner, clock = await open_store(tmp_path)
    try:
        evidence = facts(binding)
        _, view = await stage(store, owner, clock, evidence)
        source = await store.configuration_handoff_source(binding, facts=evidence, cut=view.cut)
        checkpoint = json.loads(source.checkpoint_bytes)
        checkpoint['state']['ownership']['token']['generation'] = '00000000-0000-0000-0000-000000000999'
        # An attacker can recompute hashes; only the original audited worker
        # source and later signed handoff can supply provenance/authority.
        forged = replace(source, checkpoint_bytes=json.dumps(checkpoint).encode(), cut=replace(
            source.cut, checkpoint=replace(source.cut.checkpoint, state_digest=fingerprint(checkpoint['state']))))
        before = files.binding.read_bytes()
        with pytest.raises(StorageError, match='CONFIG_HANDOFF_SOURCE_CHANGED'):
            await store.validate_configuration_handoff_source(forged, facts=evidence, cut=view.cut)
        assert files.binding.read_bytes() == before
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_captured_policy_must_match_audited_runtime_even_with_identical_state_cut(tmp_path, monkeypatch):
    store, files, binding, owner, clock = await open_store(tmp_path)
    try:
        evidence = facts(binding)
        _, view = await stage(store, owner, clock, evidence)
        original = files.binding.read_bytes()
        envelope = json.loads(original)
        envelope['policy']['capacity']['max_operations'] += 1

        class ChangedRead:
            # Simulate different policy bytes at the capture read; the owning
            # PolicyState continues to verify the original audited envelope.
            def __getattr__(self, name):
                return getattr(files.binding, name)

            def open(self, mode):
                assert mode == 'rb'
                return io.BytesIO(json.dumps(envelope).encode())

        monkeypatch.setattr(store, '_path', ChangedRead())
        with pytest.raises(StorageError, match='CONFIG_HANDOFF_POLICY_MISMATCH'):
            await store.configuration_handoff_source(binding, facts=evidence, cut=view.cut)
        assert files.binding.read_bytes() == original
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['checkpoint', 'native', 'omit_native', 'duplicate_native', 'binding', 'cut_revision'])
async def test_source_object_rejects_byte_or_namespace_substitution(tmp_path, fault):
    store, _, binding, owner, clock = await open_store(tmp_path)
    try:
        _, view = await stage(store, owner, clock, facts(binding))
        source = await store.configuration_handoff_source(binding, facts=facts(binding), cut=view.cut)
        changes = {}
        if fault == 'checkpoint':
            envelope = json.loads(source.checkpoint_bytes)
            envelope['state']['operations'] = {}
            changes['checkpoint_bytes'] = json.dumps(envelope).encode()
        elif fault == 'native':
            changes['journal_images'] = ((source.journal_images[0][0], b'{}'),) + source.journal_images[1:]
        elif fault == 'omit_native':
            changes['journal_images'] = source.journal_images[1:]
        elif fault == 'duplicate_native':
            changes['journal_images'] = source.journal_images + source.journal_images[:1]
        elif fault == 'binding':
            changes['facts'] = replace(source.facts, binding=replace(binding, risk_pool='other'))
        else:
            changes['cut'] = replace(source.cut, checkpoint=replace(source.cut.checkpoint, revision=999))
        with pytest.raises(StorageError, match='CONFIG_HANDOFF_SOURCE_INVALID'):
            replace(source, **changes)
    finally:
        await store.close()


def test_source_rejects_mutable_bytes_and_untyped_input():
    with pytest.raises(StorageError, match='CONFIG_HANDOFF_SOURCE_INVALID'):
        FinancialConfigurationSource(None, bytearray(b'{}'), (), None)


@pytest.mark.asyncio
async def test_source_preserves_aborted_wire_ids_and_cashflow_adjusted_capital(tmp_path):
    store, _, binding, owner, clock = await open_store(tmp_path)
    try:
        command = order(owner)
        await store.prepare(command, cut=(await store.snapshot(binding)).cut, owner=owner)
        await store.apply_lifecycle(SpotLifecycleCommand('abort', command.operation_id, command.intent_id,
                                                         command.client_order_id, 'ABORT_PREPARED'),
                                    cut=(await store.snapshot(binding)).cut, owner=owner)
        await store.apply_cashflow(transfer(), cut=(await store.snapshot(binding)).cut, owner=owner)
        capital = (await store.observe_nav(nav(), cut=(await store.snapshot(binding)).cut, owner=owner)).capital
        evidence = replace(facts(binding), account_quote_usdt=D('12'), account_cashflow_ids=('101',))
        _, view = await stage(store, owner, clock, evidence)
        source = await store.configuration_handoff_source(binding, facts=evidence, cut=view.cut)
        state = json.loads(source.checkpoint_bytes)['state']
        assert source.unresolved_intents == ()
        assert state['resources']['commands'][command.operation_id]['state'] == 'ABORTED_BEFORE_SEND'
        assert state['resources']['commands'][command.operation_id]['command']['client_order_id'] == command.client_order_id
        assert state['resources']['cashflows']['101']['signed_amount'] == '2'
        current_capital = await store.capital(binding)
        assert current_capital == replace(capital, cut=current_capital.cut)
        assert capital.net_cashflows_usdt == D('2') and capital.adjusted_nav_usdt == D('15')
        assert (await store.snapshot(binding)).cut == view.cut
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cancelled_capture_retains_physical_worker_and_never_writes(tmp_path, monkeypatch):
    store, files, binding, owner, clock = await open_store(tmp_path)
    entered, release = threading.Event(), threading.Event()
    task = None
    try:
        evidence = facts(binding)
        _, view = await stage(store, owner, clock, evidence)
        checkpoint, images = files.binding.read_bytes(), await store._call(store._raw)
        native = store._configuration_handoff_source

        def blocked(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError('test worker not released')
            return native(*args)
        monkeypatch.setattr(store, '_configuration_handoff_source', blocked)
        task = asyncio.create_task(store.configuration_handoff_source(binding, facts=evidence, cut=view.cut))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store._pending and not store._uncertain
        release.set()
        monkeypatch.setattr(store, '_configuration_handoff_source', native)
        source = await store.configuration_handoff_source(binding, facts=evidence, cut=view.cut)
        assert source.checkpoint_bytes == checkpoint and dict(source.journal_images) == images
        assert (await store.snapshot(binding)).cut == view.cut
    finally:
        release.set()
        if task is not None and not task.done():
            await task
        await store.close()
