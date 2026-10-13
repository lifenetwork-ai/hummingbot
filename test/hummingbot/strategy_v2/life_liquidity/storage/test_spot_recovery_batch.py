"""Recovery includes later account activity without changing the original command."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_ack_contracts import ack
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_ack_recovery import pending_ack
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts import transfer
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import command as fill_command
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_recovery import (
    bundle as _bundle_fixture,
    cold_open,
    facts,
    pending_cashflow,
    pending_fill,
)
from unittest.mock import patch
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, thaw
from hummingbot.strategy_v2.life_liquidity.storage.financial import AttemptState
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotAckRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.recovery_batch import SpotRecoveryBatch

bundle = _bundle_fixture


def batch(owner, *, pending_kind='FILL'):
    original = fill_command(owner)
    later = replace(original.fills[0], trade_id='trade2', signed_fee=D('-.01'))
    full = replace(original, operation_id='batch-fill', cumulative_base_life=D('2'), fills=original.fills + (later,))
    return SpotRecoveryBatch('batch1', (full,), (), (ack(owner),) if pending_kind == 'ACK' else ())


def full_facts(binding):
    return facts(binding, account_base_life=D('7'), account_quote_usdt=D('7.97'),
                 account_trade_ids=('trade1', 'trade2'))


def ack_facts(binding):
    return (SpotAckRecoveryFacts(full_facts(binding), 'wire1', '123456789', 'LIFE-USDT', 'BUY',
                                 D('2'), D('1'), D('2'), 'FILLED'),)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['ACK', 'FILL'])
async def test_later_fill_recovery_retains_original_operation_and_cold_send_gate(bundle, tmp_path, kind):
    store, files, binding, owner, _ = bundle
    await (pending_ack(store, binding, owner) if kind == 'ACK' else pending_fill(store, binding, owner))
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        origin = await cold.pending_spot_commands(binding)
        request = batch(owner, pending_kind=kind)
        receipt = await cold.recover_spot_batch(request, facts=full_facts(binding),
                                                acknowledgements=ack_facts(binding) if kind == 'ACK' else (),
                                                cut=(await cold.snapshot(binding)).cut, owner=owner)
        after = await cold.snapshot(binding)
        assert receipt.state == 'APPLIED' and not receipt.replayed
        assert after.balance.total_base_life == D('7') and after.balance.total_quote_usdt == D('7.97')
        assert after.budget.session_execution_loss_usdt == D('.23')
        assert after.budget.session_subsidy_committed_usdt == D('.23')
        assert after.recovery_required and (await cold.attempt('intent1', binding)).state.value == 'ACKED'
        assert await cold.pending_spot_commands(binding) == ()
        resource = 'acknowledgements' if kind == 'ACK' else 'fill_operations'
        original = await cold._call(lambda: cold._read().resources[resource][origin[0].command.operation_id]['command'])
        assert thaw(original) == origin[0].command.json()
        replay = await cold.recover_spot_batch(request, facts=full_facts(binding), acknowledgements=(),
                                               cut=after.cut, owner=owner)
        assert replay.replayed and (await cold.snapshot(binding)).cut == after.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['native', 'receipt'])
async def test_interrupted_batch_keeps_barrier_and_resumes_exactly_once(bundle, tmp_path, stage):
    store, files, binding, owner, _ = bundle
    await pending_fill(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    request = batch(owner)
    original = cold._publish_recovery_batch

    def interrupted(key, *args):
        if (stage == 'native' and key.startswith('batch-native:')
                or stage == 'receipt' and key.startswith('batch-done:')):
            raise OSError('synthetic interrupted recovery batch')
        return original(key, *args)

    try:
        with patch.object(cold, '_publish_recovery_batch', side_effect=interrupted):
            with pytest.raises(CommitUnknown):
                await cold.recover_spot_batch(request, facts=full_facts(binding), acknowledgements=(),
                                              cut=(await cold.snapshot(binding)).cut, owner=owner)
    finally:
        await cold.close()
    restored = await cold_open(files, binding, tmp_path)
    try:
        assert await restored.pending_recovery_batches(binding) == (request,)
        await restored.recover_spot_batch(request, facts=full_facts(binding), acknowledgements=(),
                                          cut=(await restored.snapshot(binding)).cut, owner=owner)
        view = await restored.snapshot(binding)
        assert view.balance.total_quote_usdt == D('7.97') and view.budget.session_execution_loss_usdt == D('.23')
        assert await restored.pending_recovery_batches(binding) == ()
    finally:
        await restored.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', ['price', 'wallet', 'coverage', 'owner', 'cut'])
async def test_batch_rejects_changed_original_value_or_unqualified_source_before_mutation(bundle, tmp_path, bad):
    store, files, binding, owner, _ = bundle
    stale = (await store.snapshot(binding)).cut
    await pending_fill(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        before = await cold.snapshot(binding)
        request, evidence, token, cut = batch(owner), full_facts(binding), owner, before.cut
        if bad == 'price':
            full = request.fills[0]
            request = replace(request, fills=(replace(full, fills=(replace(full.fills[0], independent_price_usdt=D('.8')),
                                                                   full.fills[1])),))
        elif bad == 'wallet':
            evidence = replace(evidence, account_quote_usdt=D('99'))
        elif bad == 'coverage':
            evidence = replace(evidence, bill_scope_complete=False)
        elif bad == 'owner':
            token = replace(owner, generation=uuid4())
        else:
            cut = stale
        with pytest.raises(StorageError):
            await cold.recover_spot_batch(request, facts=evidence, acknowledgements=(), cut=cut, owner=token)
        assert (await cold.snapshot(binding)).cut == before.cut
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_pending_transfer_with_new_withdrawal_uses_original_value_and_preserves_budgets(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_cashflow(store, binding)
    await store.close()
    later = owner.expires_at + timedelta(seconds=1)
    cold = await cold_open(files, binding, tmp_path, now=later)
    try:
        at = transfer().transfer_at_ms + 1
        withdrawal = transfer(operation_id='cash2', bill_id='102', signed_amount=D('-1'), transfer_at_ms=at,
                              value_at_ms=at, reference_observed_at_ms=at, observed_at=later)
        command = SpotRecoveryBatch('cash-batch', (), (transfer(), withdrawal), ())
        evidence = facts(binding, kind='cash', observed_at=later, account_quote_usdt=D('11'),
                         account_cashflow_ids=('101', '102'))
        before = await cold.snapshot(binding)
        await cold.recover_spot_batch(command, facts=evidence, acknowledgements=(), cut=before.cut, owner=owner)
        after = await cold.snapshot(binding)
        assert after.balance.total_quote_usdt == D('11') and after.budget == before.budget
        assert after.recovery_required and await cold.pending_spot_commands(binding) == ()
        original = await cold._call(lambda: cold._read().resources['cashflow_operations']['cash1']['command'])
        assert thaw(original) == transfer().json()
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_batch_fill_before_ack_preserves_unknown_send_and_fee_holds(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_fill(store, binding, owner, acknowledge=False)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    try:
        await cold.recover_spot_batch(batch(owner), facts=full_facts(binding), acknowledgements=(),
                                      cut=(await cold.snapshot(binding)).cut, owner=owner)
        assert (await cold.attempt('intent1', binding)).state is AttemptState.SEND_UNKNOWN
        assert (await cold.snapshot(binding)).recovery_required
        with pytest.raises(StorageError):
            await cold.arm('operation1', cut=(await cold.snapshot(binding)).cut, owner=owner)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_new_activity_after_interrupted_batch_keeps_its_immutable_history(bundle, tmp_path):
    store, files, binding, owner, _ = bundle
    await pending_fill(store, binding, owner)
    await store.close()
    cold = await cold_open(files, binding, tmp_path)
    first = SpotRecoveryBatch('first-batch', (replace(fill_command(owner), operation_id='first-fill'),), (), ())
    original = cold._publish_recovery_batch

    def fail(key, *args):
        if key.startswith('batch-done:'):
            raise OSError('synthetic receipt loss')
        return original(key, *args)
    try:
        with patch.object(cold, '_publish_recovery_batch', side_effect=fail):
            with pytest.raises(CommitUnknown):
                await cold.recover_spot_batch(first, facts=facts(binding), acknowledgements=(),
                                              cut=(await cold.snapshot(binding)).cut, owner=owner)
    finally:
        await cold.close()
    restored = await cold_open(files, binding, tmp_path)
    try:
        second = replace(batch(owner), operation_id='second-batch', supersedes=('first-batch',))
        await restored.recover_spot_batch(second, facts=full_facts(binding), acknowledgements=(),
                                          cut=(await restored.snapshot(binding)).cut, owner=owner)
        assert await restored.pending_recovery_batches(binding) == ()
        raw = await restored._call(lambda: restored._read().resources['recovery_batches']['first-batch'])
        assert thaw(raw['command']) == first.json() and raw['completed_by'] == 'second-batch'
        assert (await restored.lookup_fill('first-fill', binding)).state == 'APPLIED'
        after = await restored.snapshot(binding)
        assert after.budget.session_execution_loss_usdt == D('.23') and after.balance.total_quote_usdt == D('7.97')
        assert (await restored.recover_spot_batch(first, facts=facts(binding), acknowledgements=(),
                                                  cut=after.cut, owner=owner)).replayed
    finally:
        await restored.close()
