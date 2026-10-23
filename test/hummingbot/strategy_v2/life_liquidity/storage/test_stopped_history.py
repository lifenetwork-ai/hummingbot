"""Original File publications, including late and repeated economic identities."""
import json
from copy import deepcopy
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts import ACCOUNTING, nav, transfer

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.stopped_history import StoppedMoneyHistory

pytest_plugins = ['test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_capital_contracts']


def verifier(state, *, policy=ACCOUNTING, max_replay_rows=10000):
    def validate(cash):
        row = next(row for row in policy.approved_cashflows if row.bill_id == cash.bill_id)
        assert (row.currency, row.signed_amount, row.transfer_at_ms) == (cash.currency, cash.signed_amount, cash.transfer_at_ms)
    return StoppedMoneyHistory(state, policy=policy, validate_cashflow=validate, max_rows=10000, max_replay_rows=max_replay_rows)


def state_of(store):
    return json.loads(store._files.binding.read_bytes())['state']


@pytest.mark.asyncio
async def test_late_cashflow_is_absent_from_earlier_nav_and_repeated_bill_is_not_reapplied(capital_bundle, monkeypatch):
    store, _, binding, owner, _ = capital_bundle
    await store.observe_nav(nav(operation_id='before'), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.apply_cashflow(transfer(), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.observe_nav(nav(operation_id='after'), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.apply_cashflow(transfer(operation_id='duplicate'), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.observe_nav(nav(operation_id='repeated'), cut=(await store.snapshot(binding)).cut, owner=owner)
    state = state_of(store)
    proof = verifier(state)
    import hummingbot.strategy_v2.life_liquidity.storage.stopped_history as module
    original, calls = module.replay_capital, []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(module, 'replay_capital', counted)
    for name, balance in (('before', '10'), ('after', '12'), ('repeated', '12')):
        sha, ledger = proof.before(state['operations']['nav:' + name]['revision'])
        assert sha == state['resources']['nav_operations'][name]['financial_fingerprint']
        assert ledger.usdt_balance == D(balance)
    assert len(calls) == 2  # Same bill/money cut does not hash/replay full history again.
    assert proof.cached_capital.net_cashflows_quote == D('2')


@pytest.mark.asyncio
async def test_cashflow_first_receipt_cannot_be_replaced_by_later_duplicate(capital_bundle):
    store, _, binding, owner, _ = capital_bundle
    await store.apply_cashflow(transfer(), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.apply_cashflow(transfer(operation_id='duplicate'), cut=(await store.snapshot(binding)).cut, owner=owner)
    state = state_of(store)
    state['resources']['cashflows']['101']['operation_id'] = 'duplicate'
    with pytest.raises(StorageError, match='HISTORY_CASHFLOW_CONFLICT'):
        verifier(state)


@pytest.mark.asyncio
async def test_explicit_offline_replay_work_bound_is_not_silently_ignored(capital_bundle):
    store, _, binding, owner, _ = capital_bundle
    await store.observe_nav(nav(operation_id='before'), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.apply_cashflow(transfer(), cut=(await store.snapshot(binding)).cut, owner=owner)
    await store.observe_nav(nav(operation_id='after'), cut=(await store.snapshot(binding)).cut, owner=owner)
    state = state_of(store)
    proof = verifier(state, max_replay_rows=1)
    proof.before(state['operations']['nav:before']['revision'])
    with pytest.raises(StorageError, match='HISTORY_WORK_EXCEEDED'):
        proof.before(state['operations']['nav:after']['revision'])


@pytest.mark.asyncio
async def test_joint_settled_fees_keep_original_publication_and_no_deposit_attribution(tmp_path):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_joint_cash_repository import fixture
    from test.hummingbot.strategy_v2.life_liquidity.test_joint_recovery import report, send_close

    from hummingbot.strategy_v2.life_liquidity.storage.capital import ObserveSpotNAV
    repo, store, recovery, values, bundle, owner = await fixture(tmp_path)
    try:
        assert await repo.reconcile(store.binding, owner)
        now = values[6].wall
        at = int(now.timestamp() * 1000)
        before = ObserveSpotNAV('before', D('1'), at, at, 'independent_market', now)
        await store.observe_nav(before, cut=(await store.snapshot(store.binding)).cut, owner=owner)
        claim = await store._call(send_close, values)
        report(values, bundle, claim, terminal=False, fee='-0.00100')
        assert await repo.reconcile(store.binding, owner), recovery.reason_code
        from dataclasses import replace
        await store.observe_nav(replace(before, operation_id='after'), cut=(await store.snapshot(store.binding)).cut, owner=owner)
        state = state_of(store)
        proof = verifier(state, policy=store._accounting_policy)
        for name, balance in (('before', '10'), ('after', '9.999')):
            sha, ledger = proof.before(state['operations']['nav:' + name]['revision'])
            assert sha == state['resources']['nav_operations'][name]['financial_fingerprint']
            assert ledger.usdt_balance == D(balance)
            assert ledger.net_cashflows_quote == 0
        assert ledger.execution_loss_quote == D('.001')
        damaged = deepcopy(state)
        event = next(iter(damaged['resources']['joint_cashflows'].values()))
        event['operation_id'] = 'foreign'
        with pytest.raises(StorageError, match='HISTORY_EVENT_CONFLICT'):
            verifier(damaged, policy=store._accounting_policy)
    finally:
        await store.close()
