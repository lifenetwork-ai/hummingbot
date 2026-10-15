"""Original cancel bookkeeping completes before later qualified account activity."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import reopen
from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_fill_repository import scenario
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import setup_shared

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.capital import ApprovedSpotCashflow, CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, thaw
from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['REQUEST_CANCEL', 'RECORD_CANCEL_ATTEMPT'])
@pytest.mark.parametrize('stage', ['wal', 'reservation', 'receipt'])
@pytest.mark.parametrize('written', [False, True])
async def test_cancel_intent_or_attempt_replays_once_with_new_fills_swap_fee_and_transfer(tmp_path, monkeypatch, action, stage, written):
    at = int(setup_shared(tmp_path / 'clock')[6].wall.timestamp() * 1000)
    repo, store, recovery, values, _, owner, _, fill, facts = await scenario(
        tmp_path, approvals=(ApprovedSpotCashflow('101', 'USDT', D('2'), at),))
    command = SpotLifecycleCommand('original-cancel', fill.prepare_operation_id, fill.intent_id, fill.client_order_id, action,
                                   facts.observed_at if action == 'RECORD_CANCEL_ATTEMPT' else None,
                                   0 if action == 'RECORD_CANCEL_ATTEMPT' else None,
                                   100 if action == 'RECORD_CANCEL_ATTEMPT' else None)
    target, name = {'wal': (store._wal, 'mark_cancel_requested' if action == 'REQUEST_CANCEL' else 'mark_cancel_attempt'),
                    'reservation': (store._ledger, 'request_cancel'), 'receipt': (store, '_publish_lifecycle')}[stage]
    original = getattr(target, name)

    def crash(*args, **kwargs):
        if stage == 'receipt' and not args[0].startswith('lifecycle-done:'):
            return original(*args, **kwargs)
        if written:
            original(*args, **kwargs)
        raise OSError('synthetic original cancel interruption')

    with monkeypatch.context() as patches:
        patches.setattr(target, name, crash)
        with pytest.raises(CommitUnknown):
            await store.apply_lifecycle(command, cut=(await store.snapshot(store.binding)).cut, owner=owner)
    flow = CashflowSpot('new-flow', '101', 'USDT', D('2'), at, D('1'), at, at, 'quote_unit', facts.observed_at)
    facts = replace(facts, account_quote_usdt=facts.account_quote_usdt + 2, account_cashflow_ids=('101',))
    values[8]['snapshot'] = replace(values[8]['snapshot'], spot_cash_quote=facts.account_quote_usdt)
    repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
    try:
        assert await repo.reconcile(store.binding, owner, spot_fills=(fill,), spot_facts=facts, spot_cashflows=(flow,)), recovery.reason_code
        after = await store.snapshot(store.binding)
        assert (after.balance.total_base_life, after.balance.total_quote_usdt) == (D('11'), D('10.979'))
        assert after.balance.reserved_quote_usdt == D('1.08')
        assert after.budget.session_execution_loss_usdt == D('.121')
        native = await store._call(store._wal.get, fill.intent_id)
        assert native.cancel_requested
        assert native.cancel_attempts == (1 if action == 'RECORD_CANCEL_ATTEMPT' else 0)
        row = thaw((await store.read(store.binding)).resources['lifecycle_operations'][command.operation_id])
        assert row['command'] == command.json() and row['state'] == 'APPLIED'
        repo, store, recovery, values = await reopen(tmp_path, store, recovery, values, owner)
        assert await repo.reconcile(store.binding, owner, spot_facts=facts), recovery.reason_code
        replay = await store.snapshot(store.binding)
        assert replay.balance == after.balance and replay.budget == after.budget
        assert (await store._call(store._wal.get, fill.intent_id)).cancel_attempts == native.cancel_attempts
        assert store._cold_unarmed and not recovery._armed
        assert values[3].sent == [] and len(values[4].sent) == 1
    finally:
        await store.close()
