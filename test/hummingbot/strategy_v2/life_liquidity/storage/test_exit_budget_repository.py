"""Exit reserves cannot be consumed by quoting; one repository owns both budgets."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
    BUDGET,
    CAPACITY,
    NOW,
    RISK,
    create_fixture,
    order,
)

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.financial import BudgetPolicy
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot

EXIT_BUDGET = BudgetPolicy('synthetic-exit', D('10'), D('5'), D('2'))


async def exit_repository(tmp_path, *, create=True, files=None, binding=None, owner=None):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_terminal_contracts import (
        ACCOUNTING,
        POLICY,
        TERMINAL_POLICY,
    )
    if files is None:
        files, binding, owner = create_fixture(tmp_path)
        files = replace(files, exit_subsidy=tmp_path / 'exit_budget.json')
    store = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK,
                                   subsidy_policy=BUDGET, loss_policy=BUDGET, exit_subsidy_policy=EXIT_BUDGET,
                                   config_version=1, account_lock_root=tmp_path / 'locks',
                                   clock=lambda: NOW, create_binding=create, owner=owner if create else None,
                                   fill_policy=POLICY, accounting_policy=ACCOUNTING, terminal_policy=TERMINAL_POLICY)
    return store, files, binding, owner


@pytest.mark.asyncio
async def test_quote_exhaustion_cannot_spend_exit_capacity_and_cold_retains_both_holds(tmp_path):
    store, files, binding, owner = await exit_repository(tmp_path)
    try:
        await store.prepare_current(order(owner, subsidy_quote_usdt=D('2')), owner=owner)
        exit_order = order(owner, operation_id='exit1', intent_id='exit1', client_order_id='exitwire1', side='SELL',
                           budget_scope='EXIT', subsidy_quote_usdt=D('.1'))
        await store.prepare_current(exit_order, owner=owner)
        assert (await store.snapshot(binding)).budget.session_subsidy_committed_usdt == D('2')
        assert await store._call(lambda: store._subsidy.exit.verified_status(
            session_id=str(owner.session_id), at_utc=NOW).session_committed_quote) == D('.1')
        assert await store._call(store._subsidy.verified_intent_ids) == frozenset({'intent1', 'exit1'})
        await store.close()
        store, _, _, _ = await exit_repository(tmp_path, create=False, files=files, binding=binding, owner=owner)
        assert store._cold_unarmed
        assert (await store.snapshot(binding)).budget.session_subsidy_committed_usdt == D('2')
        assert await store._call(lambda: store._subsidy.reserved_for('exit1', session_id=str(owner.session_id))) == D('.1')
    finally:
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('crash', [None, 'before', 'after'])
async def test_terminal_exit_settles_actual_independent_loss_once_and_repairs_original_barrier(tmp_path, monkeypatch, crash):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_fill_contracts import command, fill, prepare_arm
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_terminal_contracts import facts, terminal

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    store, files, binding, owner = await exit_repository(tmp_path)
    try:
        await prepare_arm(store, binding, owner, side='SELL', budget_scope='EXIT', subsidy_quote_usdt=D('.5'))
        event = fill(independent_price_usdt=D('1.1'))
        await store.apply_fills(command(owner, side='SELL', fills=(event,)), cut=(await store.snapshot(binding)).cut, owner=owner)
        source = facts(binding, cumulative_base_life=D('1'), fills=(event,), account_base_life=D('4'),
                       account_quote_usdt=D('10.98'), account_trade_ids=('trade1',))
        end = terminal(binding, owner, source, side='SELL')
        if crash is not None:
            native = store._subsidy.exit._reconcile

            def interrupted(*args, **kwargs):
                if crash == 'after':
                    native(*args, **kwargs)
                raise OSError('synthetic exit settlement interruption')
            monkeypatch.setattr(store._subsidy.exit, '_reconcile', interrupted)
            with pytest.raises(CommitUnknown):
                await store.confirm_terminal(end, cut=(await store.snapshot(binding)).cut, owner=owner)
            await store.close()
            store, _, _, _ = await exit_repository(tmp_path, create=False, files=files, binding=binding, owner=owner)
            await store.resume_terminal(end, cut=(await store.snapshot(binding)).cut, owner=owner)
            assert store._cold_unarmed
        else:
            await store.confirm_terminal(end, cut=(await store.snapshot(binding)).cut, owner=owner)
        assert await store._call(lambda: store._subsidy.exit.matches_settled('intent1', D('.12'), session_id=str(owner.session_id)))
        view = await store.snapshot(binding)
        assert view.budget.session_execution_loss_usdt == D('.12')
        assert view.budget.session_subsidy_committed_usdt == 0
        repeat = store.resume_terminal if crash is not None else store.confirm_terminal
        assert (await repeat(end, cut=view.cut, owner=owner)).replayed
        assert (await store.snapshot(binding)).balance == view.balance
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_exit_budget_cannot_be_replaced_with_identical_bytes_at_a_foreign_path(tmp_path):
    from shutil import copyfile

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    store, _, _, owner = await exit_repository(tmp_path)
    try:
        await store.prepare_current(order(owner, side='SELL', budget_scope='EXIT'), owner=owner)
        foreign = tmp_path / 'foreign_budget.json'
        copyfile(store._files.exit_subsidy, foreign)
        store._subsidy.exit.path = foreign
        with pytest.raises(StorageError):
            await store.snapshot(store.binding)
    finally:
        await store.close()
