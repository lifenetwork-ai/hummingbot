"""Signed ordinary intake of unbooked facts, synthetic private/public HTTP only."""
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_hedge_source import sources

import pytest

from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError


def observer(t):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_observer import PgJointFinancialObserver

    def value(product, trade_id, at_ms):
        facts = t.command.swap_fills if product == 'SWAP' else t.command.spot_fills
        fill = next(f for command in facts for f in command.fills if f.trade_id == trade_id)
        return IndependentFillObservation(
            fill.independent_price_usdt, fill.independent_value_at_ms, fill.independent_observed_at_ms, fill.source_kind)
    return PgJointFinancialObserver(t.source, independent_fill_value=value, independent_cashflow_value=lambda *args: None)


@pytest.mark.asyncio
@pytest.mark.parametrize('spot_fee', [None, 'LIFE', 'USDT'])
async def test_signed_joint_observer_books_new_facts_atomically_and_replay_never_credits_twice(database, spot_fee):
    t = await sources(database, apply_batch=False, spot_fee=spot_fee)
    intake = observer(t)
    try:
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == 10
        claims = await t.values[5].claims(t.cfg.binding)
        receipt = await intake.observe('ordinary-joint')
        assert receipt.state == 'APPLIED' and not receipt.trading_permission
        expected = D('9.89') if spot_fee is None else D('10.889') if spot_fee == 'USDT' else D('10.890')
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == expected
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert (await t.financial.snapshot(t.cfg.binding)).budget.session_execution_loss_usdt == (
            D('.01') if spot_fee is None else D('.011'))
        requests = len(t.requests)
        assert await t.values[5].claims(t.cfg.binding) == claims
        assert (await intake.observe('ordinary-joint')).replayed
        assert len(t.requests) == requests
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == expected
        market_reads = sum(r['url'].rsplit('/', 1)[1] in t.public for r in t.requests)
        assert await intake.observe('nothing-new') is None
        assert sum(r['url'].rsplit('/', 1)[1] in t.public for r in t.requests) == market_reads
        assert (await t.financial.snapshot(t.cfg.binding)).recovery_required
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['foreign', 'fee', 'pnl', 'history', 'approval', 'duplicate', 'callback', 'value', 'credentials'])
async def test_unqualified_joint_source_never_publishes_money_or_releases_original_claims(database, fault):
    t = await sources(database, apply_batch=False, spot_fee='USDT')
    intake = observer(t)
    if fault == 'foreign':
        t.fill['clOrdId'] = 'someone-else'
    elif fault == 'fee':
        t.fill['fee'] = '-.1'
    elif fault == 'pnl':
        t.fill['fillPnl'] = '100'
    elif fault == 'history':
        t.account_fills['SWAP'] = []
    elif fault == 'approval':
        next(v for v in t.bills if v['type'] == '1')['billId'] = '999'
        t.bills.sort(key=lambda v: int(v['billId']), reverse=True)
    elif fault == 'duplicate':
        t.account_fills['SWAP'].append(dict(t.fill))
    elif fault == 'callback':
        intake.fill_value = lambda *args: None
    elif fault == 'value':
        intake = type(intake)(t.source, independent_fill_value=lambda *args: None, independent_cashflow_value=lambda *args: None)
    else:
        t.connector.okx_api_key = 'foreign'
    try:
        before = await t.financial.balance(t.cfg.binding)
        claims = await t.values[5].claims(t.cfg.binding)
        with pytest.raises(StorageError):
            await intake.observe('refused')
        assert not t.financial._runtime.healthy
        assert await t.financial.balance(t.cfg.binding) == before
        assert await t.values[5].claims(t.cfg.binding) == claims
        assert not await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts')
        assert not await database[0].fetchval('SELECT count(*) FROM life_financial.spot_fill_facts')
        assert not await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments')
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_stopped_hedge_source_still_accounts_original_late_financial_truth_without_capacity_refresh(database):
    t = await sources(database, apply_batch=False, spot_fee='USDT')
    try:
        t.source.stop()
        intake = observer(t)
        claims = await t.values[5].claims(t.cfg.binding)
        assert (await intake.observe('late-after-stop')).state == 'APPLIED'
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == D('10.889')
        assert not t.financial._runtime.healthy
        assert await t.values[5].claims(t.cfg.binding) == claims
        with pytest.raises(StorageError, match='STOPPED'):
            await t.source.refresh('cannot-reactivate')
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_successive_paid_funding_uses_full_original_history_and_deduplicates_previous_cash(database):
    from datetime import datetime, timezone
    t = await sources(database, apply_batch=False)
    intake = observer(t)
    try:
        assert (await intake.observe('first')).state == 'APPLIED'
        at = int(datetime.now(timezone.utc).timestamp() * 1000)
        t.bills.insert(0, dict(billId='780', instType='SWAP', instId='LIFE-USDT-SWAP', ordId='', tradeId='', type='8', subType='173',
                               ccy='USDT', balChg='-.2', pnl='-.2', fee='0', ts=str(at)))
        t.balance['details'][1].update(cashBal='9.69', eq='9.70', uTime=str(at))
        t.balance['uTime'] = str(at)
        # An actual second synthetic market packet. Original native fill/bill
        # timestamps and the first persisted batch stay unchanged.
        t.public_received_at = None
        try:
            result = await intake.observe('next-funding')
        except StorageError as error:
            error.add_note(str(await database[0].fetchval(
                "SELECT reason FROM life_financial.joint_financial_batches WHERE operation_id='next-funding'")))
            raise
        assert result.state == 'APPLIED' and result.cash_delta_usdt == D('-.2')
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == D('9.69')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 2
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts') == 1
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_fresh_capital_carry_capture_after_ordinary_intake_preserves_paid_history_and_claims(database):
    t = await sources(database, apply_batch=False)
    intake = observer(t)
    try:
        assert (await intake.observe('ordinary')).state == 'APPLIED'
        before = await t.financial.balance(t.cfg.binding)
        claims = await t.values[5].claims(t.cfg.binding)
        t.public_received_at = None  # A new synthetic market HTTP response.
        result = await t.source.refresh('after-accounting')
        assert result.capital.decision.allowed and result.carry.decision.allowed
        assert await t.financial.balance(t.cfg.binding) == before
        assert await t.values[5].claims(t.cfg.binding) == claims
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['cash', 'missing_funding'])
async def test_native_economic_rejection_keeps_original_batch_pending_without_partial_credit(database, fault):
    t = await sources(database, apply_batch=False)
    intake = observer(t)
    if fault == 'cash':
        t.balance['details'][1].update(cashBal='10.89', eq='10.90')
    else:
        t.bills[:] = [v for v in t.bills if v.get('billId') != '777']
    try:
        with pytest.raises(StorageError, match='PG_JOINT_OBSERVER_RECOVERY_REQUIRED'):
            await intake.observe('bad-economics')
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == 10
        saved = (await intake.batch.frame(t.cfg.binding))['batches']
        assert len(saved) == 1 and saved[0]['state'] == 'RECOVERY_REQUIRED'
        assert saved[0]['command']['operation_id'] == 'bad-economics'
        assert not await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts')
        assert not await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments')
        requests = len(t.requests)
        assert (await intake.observe('bad-economics')).state == 'RECOVERY_REQUIRED'
        assert len(t.requests) == requests
        with pytest.raises(StorageError, match='RECOVERY_REQUIRED|RESOLUTION_REQUIRED'):
            await intake.observe('another-id')
        assert len(t.requests) == requests
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_unknown_batch_commit_retains_original_money_and_diagnostic_replay_without_new_reads(database, monkeypatch):
    t = await sources(database, apply_batch=False, spot_fee='USDT')
    intake = observer(t)
    original_apply, commit = intake.batch.apply, t.financial._runtime._commit

    async def uncertain(transaction):
        await commit(transaction)
        raise ConnectionError('synthetic lost original COMMIT reply')

    async def apply(*args, **kwargs):
        monkeypatch.setattr(t.financial._runtime, '_commit', uncertain)
        return await original_apply(*args, **kwargs)
    monkeypatch.setattr(intake.batch, 'apply', apply)
    try:
        with pytest.raises(CommitUnknown):
            await intake.observe('unknown-batch')
        assert not t.financial._runtime.healthy
        monkeypatch.setattr(t.financial._runtime, '_commit', commit)
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == D('10.889')
        requests = len(t.requests)
        result = await intake.observe('unknown-batch')
        assert result.replayed and result.state == 'APPLIED' and not result.trading_permission
        assert len(t.requests) == requests
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_financial_batches') == 1
        assert not t.session.sent
    finally:
        monkeypatch.setattr(t.financial._runtime, '_commit', commit)
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['ingress', 'provider'])
async def test_change_during_signed_intake_prevents_batch_publication(database, monkeypatch, fault):
    t = await sources(database, apply_batch=False)
    intake = observer(t)
    original = t.joint._sample
    calls = []

    async def changed(*args, **kwargs):
        result = await original(*args, **kwargs)
        calls.append(True)
        if len(calls) == 1:
            if fault == 'ingress':
                t.financial._runtime._ingress_epoch += 1
            else:
                t.fill['fee'] = '-.2'
        return result
    monkeypatch.setattr(t.joint, '_sample', changed)
    try:
        with pytest.raises(StorageError):
            await intake.observe('changed-source')
        assert not await database[0].fetchval('SELECT count(*) FROM life_financial.joint_financial_batches')
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == 10
        assert not t.financial._runtime.healthy and not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_cancelled_intake_waiter_cannot_cancel_physical_batch_commit_or_repeat_original_credit(database, monkeypatch):
    import asyncio
    t = await sources(database, apply_batch=False, spot_fee='USDT')
    intake = observer(t)
    entered, release = asyncio.Event(), asyncio.Event()
    original_apply, commit = intake.batch.apply, t.financial._runtime._commit

    async def held(transaction):
        entered.set()
        await release.wait()
        await commit(transaction)

    async def apply(*args, **kwargs):
        monkeypatch.setattr(t.financial._runtime, '_commit', held)
        return await original_apply(*args, **kwargs)
    monkeypatch.setattr(intake.batch, 'apply', apply)
    task = asyncio.create_task(intake.observe('cancelled-intake'))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        release.set()
        if t.financial._runtime._tasks:
            await asyncio.wait_for(asyncio.gather(*tuple(t.financial._runtime._tasks), return_exceptions=True), 3)
        monkeypatch.setattr(t.financial._runtime, '_commit', commit)
        assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == D('10.889')
        requests = len(t.requests)
        assert (await intake.observe('cancelled-intake')).replayed
        assert len(t.requests) == requests and not t.session.sent
        assert not t.financial._runtime.healthy
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        monkeypatch.setattr(t.financial._runtime, '_commit', commit)
        await t.financial.close()


@pytest.mark.asyncio
async def test_cold_runtime_keeps_original_ids_and_values_without_rebooking_or_refreshing_capacity(database):
    from datetime import datetime, timezone

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.carry import PgCarryRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import PgHedgeSource
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_observer import PgJointFinancialObserver
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import PgJointAccountReconciler
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_account_source import (
        PgObservationAccountSource,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_send import PgSwapBoundary
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_transport import PgSwapAccountSource
    t = await sources(database, apply_batch=False, spot_fee='USDT')
    cold = None
    try:
        intake = observer(t)
        assert (await intake.observe('before-restart')).state == 'APPLIED'
        balance, claims = await t.financial.balance(t.cfg.binding), await t.values[5].claims(t.cfg.binding)
        await t.financial.close()

        cold = PgSpotRepository(await PgRuntime.open(t.cfg))
        capital = PgSharedCapitalRepository(cold, owner=t.owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        carry = PgCarryRepository(capital, observation=lambda: None, max_rows=64, max_bytes=1048576)
        swap = PgSwapRepository(capital)
        journal = PgQuoteRunnerJournal(cold, owner=t.owner, controller_id='main', config_version=1,
                                       runner_facts=t.journal.runner_facts, local_ready=lambda: True,
                                       clock=lambda: datetime.now(timezone.utc), max_actions=64)
        account = await PgObservationAccountSource.create(journal, connector=t.connector, max_uid_age_ms=1000,
                                                          max_auth_age_ms=1000, max_clock_skew_ms=100,
                                                          max_pending=2, collection_timeout_ms=1000)
        reader = PgSpotAccountReader(account, max_reads=128, max_bytes=4194304, timeout_ms=4000)
        collector = PgJointAccountReconciler(reader, swap, max_pages=2, max_age_ms=1000)
        send = await PgSwapAccountSource.create(PgSwapBoundary(swap), connector=t.swap_connector,
                                                clock=lambda: datetime.now(timezone.utc), local_fence=lambda: True,
                                                max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=100,
                                                collection_timeout_ms=1000, max_pending=1)
        source = PgHedgeSource(collector, carry=carry, swap_source=send, max_levels=8, timeout_ms=5000)

        def no_revaluation(*args):
            pytest.fail('Original monetary observations cannot be revalued on cold restart')
        restored = PgJointFinancialObserver(source, independent_fill_value=no_revaluation, independent_cashflow_value=no_revaluation)
        requests = len(t.requests)
        assert (await restored.observe('before-restart')).replayed
        assert len(t.requests) == requests
        t.public_received_at = None
        assert await restored.observe('cold-no-new-facts') is None
        assert await cold.balance(t.cfg.binding) == balance
        assert await capital.claims(t.cfg.binding) == claims
        assert (await cold.snapshot(t.cfg.binding)).recovery_required
        assert not t.session.sent
    finally:
        if cold is not None:
            await cold.close()
        await t.financial.close()


@pytest.mark.asyncio
async def test_approved_life_transfer_uses_independent_value_without_creating_usdt_cash(database):
    from datetime import datetime, timezone

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_observer import PgJointFinancialObserver
    t = await sources(database, apply_batch=False, spot_fee='USDT')
    original = observer(t)
    at = int(datetime.now(timezone.utc).timestamp() * 1000)
    await database[0].execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                              t.cfg.account_id, '888', 'LIFE', D('2'), at, b'synthetic-preverified-import')
    t.bills.insert(0, dict(billId='888', type='1', subType='11', ccy='LIFE', balChg='2', ts=str(at), **{'from': '6', 'to': '18'}))
    t.balance['details'][0].update(cashBal='8', eq='8', uTime=str(at))
    t.balance['uTime'] = str(at)
    intake = PgJointFinancialObserver(t.source, independent_fill_value=original.fill_value,
                                      independent_cashflow_value=lambda key, when: IndependentFillObservation(D('1.5'), when, when, 'independent_market'))
    try:
        claims = await t.values[5].claims(t.cfg.binding)
        assert (await intake.observe('asset-transfer')).state == 'APPLIED'
        balance = await t.financial.balance(t.cfg.binding)
        assert (balance.total_base_life, balance.total_quote_usdt) == (D('8'), D('10.889'))
        assert (await t.financial.snapshot(t.cfg.binding)).budget.session_execution_loss_usdt == D('.011')
        assert await t.values[5].claims(t.cfg.binding) == claims
        price = await database[0].fetchval("SELECT command#>>'{command,independent_price_usdt}' FROM life_financial.operations WHERE command#>>'{command,bill_id}'='888'")
        assert D(price) == D('1.5')
        assert not t.session.sent
    finally:
        await t.financial.close()
