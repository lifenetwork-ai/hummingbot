"""Signed joint source replay; existing native money and synthetic HTTP only."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal as D
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_collateral_source import reconciler

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.actions import RunnerActionFacts
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import PgJointAccountReconciler


async def joint(database, *, spot_fee=None, carry_options=None, apply_batch=True, spot_options=None, spot_prepare=None):
    async def before_apply(values, command):
        from dataclasses import replace
        from decimal import Decimal as D
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import flow
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import (
            native_claim,
        )
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import filled

        from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot
        from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
        financial, cfg, owner = values[:3]
        await values[5].reserve(ReserveCapital('spotclaim', replace(native_claim(owner, 'intent1', amount='2'), wire_id='wire1')),
                                await values[5].cut(cfg.binding), owner)
        await values[8].check_carry(cfg.binding)
        prepared = order(owner, risk_epoch=1, fee_buffer_base_life=D('.001'), fee_buffer_quote_usdt=D('.001'))
        if spot_prepare is None:
            await financial.prepare(prepared, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
            await financial.arm(prepared.operation_id, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        else:
            await spot_prepare(values, prepared)
        received = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(received.timestamp() * 1000)
        ack = AckSpot('spotack', prepared.operation_id, prepared.intent_id, prepared.client_order_id, prepared.market,
                      prepared.side, str(owner.session_id), owner.epoch, '123456789', received)
        await financial.acknowledge(ack, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        trade = filled(owner, received, operation_id='spotfill')
        trade = replace(trade, fills=(replace(trade.fills[0], fee_currency=spot_fee, signed_fee=D('-.001'), independent_price_usdt=D('1')),))
        transfer = flow(received, at, bill_id='779')
        await database[0].execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                                  cfg.account_id, transfer.bill_id, transfer.currency, transfer.signed_amount, at, b'synthetic-preverified-import')
        b = command.funding.bundle
        snapshot = replace(b.account.snapshot, observed_at_ms=at, spot_cash_quote=D('10.889') if spot_fee == 'USDT' else D('10.890'),
                           spot_life_base=D('6') if spot_fee == 'USDT' else D('5.999'))
        b = replace(b, account=replace(b.account, snapshot=snapshot), mark=replace(b.mark, observed_at_ms=at),
                    index=replace(b.index, observed_at_ms=at), funding=replace(b.funding, observed_at_ms=at, coverage_through_ms=at),
                    terms=replace(b.terms, observed_at_ms=at))
        return replace(command, spot_fills=(trade,), cashflows=(transfer,), funding=replace(command.funding, bundle=b))
    t = await reconciler(database, before_apply=None if spot_fee is None else before_apply, carry_options=carry_options,
                         apply_batch=apply_batch, spot_options=spot_options)
    t.reader = PgSpotAccountReader(t.account, max_reads=128, max_bytes=4194304, timeout_ms=4000)
    t.journal.runner_facts = lambda: None

    async def facts():
        return RunnerActionFacts(t.cfg.binding, datetime.now(timezone.utc), 'main', (), True)
    t.journal.runner_facts = facts
    t.joint = PgJointAccountReconciler(t.reader, t.batch.swap, max_pages=2, max_age_ms=1000)
    fill = t.command.swap_fills[0].fills[0]
    payment = t.command.funding.bundle.funding.payments[0]
    t.order = dict(instType='SWAP', instId='LIFE-USDT-SWAP', clOrdId='SwapWire1', ordId='987654321',
                   state='filled', side='sell', sz='4', accFillSz='4', px='1', tdMode='cross',
                   ordType='post_only', posSide='short', reduceOnly=False, lever='2')
    t.fill = dict(instType='SWAP', instId='LIFE-USDT-SWAP', clOrdId='SwapWire1', ordId='987654321',
                  tradeId='trade1', billId='778', side='sell', fillSz='4', fillPx='1.01', feeCcy='USDT',
                  fee='-.01', fillPnl='0', ts=str(fill.fill_at_ms))
    t.bills = [dict(billId='778', instType='SWAP', instId='LIFE-USDT-SWAP', clOrdId='SwapWire1', ordId='987654321',
                    tradeId='trade1', type='2', subType='4', ccy='USDT', balChg='-.01', fee='-.01', pnl='0', ts=str(fill.fill_at_ms)),
               dict(billId='777', instType='SWAP', instId='LIFE-USDT-SWAP', ordId='', tradeId='', type='8', subType='173',
                    ccy='USDT', balChg='-.1', pnl='-.1', fee='0', ts=str(payment.settled_at_ms)),
               dict(billId='100')]
    t.spot_order, t.spot_fill = None, None
    if spot_fee is not None:
        from decimal import Decimal as D
        fact, transfer = t.command.spot_fills[0].fills[0], t.command.cashflows[0]
        t.spot_order = dict(
            instType='SPOT', instId='LIFE-USDT', clOrdId='wire1', ordId='123456789', state='canceled',
            side='buy', sz='2', accFillSz='1', px='1', tdMode='cash', ordType='post_only')
        t.spot_fill = dict(
            instType='SPOT', instId='LIFE-USDT', clOrdId='wire1', ordId='123456789', tradeId=fact.trade_id,
            billId='782', side='buy', fillSz='1', fillPx='1', feeCcy=spot_fee, fee='-.001', ts=str(fact.fill_at_ms))
        for bill_id, ccy, principal in [('782', 'LIFE', D('1')), ('781', 'USDT', D('-1'))]:
            fee = D('-.001') if ccy == spot_fee else D('0')
            t.bills.append(dict(billId=bill_id, instType='SPOT', instId='LIFE-USDT', clOrdId='wire1', ordId='123456789',
                                tradeId=fact.trade_id, type='2', subType='1', ccy=ccy, balChg=str(principal + fee), fee=str(fee), ts=str(fact.fill_at_ms)))
        t.bills.append(dict(billId=transfer.bill_id, type='1', subType='11', ccy='USDT', balChg='2', ts=str(transfer.transfer_at_ms), **{'from': '6', 'to': '18'}))
        t.bills.sort(key=lambda v: int(v['billId']), reverse=True)
        snapshot = t.command.funding.bundle.account.snapshot
        t.balance['details'][0].update(cashBal=str(snapshot.spot_life_base), eq=str(snapshot.spot_life_base), uTime=str(fact.fill_at_ms))
        t.balance['details'][1].update(cashBal=str(snapshot.spot_cash_quote), eq=str(snapshot.spot_cash_quote + D('.01')), uTime=str(fact.fill_at_ms))
        t.balance['uTime'] = str(fact.fill_at_ms)
    t.pending = {'SPOT': [], 'SWAP': []}
    t.algo = {'SPOT': [], 'SWAP': []}
    t.account_fills = {'SPOT': [] if t.spot_fill is None else [t.spot_fill], 'SWAP': [t.fill]}
    original = t.session.request

    async def http(**kwargs):
        tail, params = kwargs['url'].rsplit('/', 1)[1], kwargs.get('params') or {}
        if tail not in ('fills-history', 'bills-archive', 'order', 'orders-pending', 'orders-algo-pending'):
            return await original(**kwargs)
        t.requests.append(kwargs)
        response = Response()

        async def body():
            if tail == 'bills-archive':
                rows = t.bills
            elif tail == 'order':
                rows = [t.order if params.get('instId') == 'LIFE-USDT-SWAP' else t.spot_order]
            elif tail == 'fills-history':
                rows = ([t.fill] if params.get('instType') == 'SWAP' else [t.spot_fill]) if params.get('ordId') else t.account_fills[params['instType']]
            elif tail == 'orders-pending':
                rows = [v for values in t.pending.values() for v in values]
            else:
                rows = [v for values in t.algo.values() for v in values]
            return {'code': '0', 'data': deepcopy(rows)}
        response.json = body
        return response
    t.session.request = http
    return t


@pytest.mark.asyncio
async def test_original_swap_fee_and_funding_require_full_signed_scope_without_changing_cash_or_claims(database):
    t = await joint(database)
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        claims = await t.values[5].claims(t.cfg.binding)
        report = await t.joint.collect()
        assert report.cut == before.cut and report.account.cash_usdt == before.balance.total_quote_usdt
        assert report.product_trade_ids == (('SWAP', 'trade1'),) and len(report.orders) == 1
        assert not report.trading_permission and await t.financial.snapshot(t.cfg.binding) == before
        assert await t.values[5].claims(t.cfg.binding) == claims
        assert all(row['method'] == 'GET' and row['data'] is None for row in t.requests)
        assert len([r for r in t.requests if r['url'].endswith('/orders-pending')]) == 2
        algos = [r for r in t.requests if r['url'].endswith('/orders-algo-pending')]
        assert len(algos) == 7 and {r['params']['ordType'] for r in algos} == set(t.reader.SWAP_ALGO_TYPES)
        assert not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault,reason', [
    ('foreign_spot_pending', 'PENDING_SCOPE'), ('foreign_swap_pending', 'PENDING_SCOPE'),
    ('algo', 'ALGO_UNOWNED'), ('missing_fill', 'TRADE_SCOPE'), ('foreign_fill', 'TRADE_SCOPE'),
    ('contract_units', 'TRADE_CONFLICT'), ('pnl', 'WIRE_INVALID'), ('bill', 'BILL_UNOWNED'),
    ('fee_bill', 'BILL_CONFLICT'), ('funding_amount', 'FUNDING_CONFLICT'), ('missing_funding', 'BILL_SCOPE'),
    ('runner', 'RUNNER_SCOPE'),
])
async def test_unknown_product_order_trade_bill_or_runner_scope_denies_joint_source_without_releasing_any_claim(database, fault, reason):
    t = await joint(database)
    try:
        if fault.startswith('foreign_') and fault.endswith('_pending'):
            t.pending['SPOT' if fault == 'foreign_spot_pending' else 'SWAP'] = [dict(ordId='999', clOrdId='foreign')]
        elif fault == 'algo':
            t.algo['SWAP'] = [dict(algoId='unknown')]
        elif fault == 'missing_fill':
            t.account_fills['SWAP'] = []
        elif fault == 'foreign_fill':
            t.account_fills['SWAP'] = [{**t.fill, 'tradeId': 'foreign'}]
        elif fault == 'contract_units':
            t.fill['fillSz'] = '1'
        elif fault == 'pnl':
            t.fill.pop('fillPnl')
        elif fault == 'bill':
            t.bills.insert(0, dict(billId='999', type='7', subType='9', ccy='USDT', balChg='-.1', ts=t.fill['ts']))
        elif fault == 'fee_bill':
            t.bills[0]['fee'] = '0'
        elif fault == 'funding_amount':
            t.bills[1]['balChg'] = '.1'
        elif fault == 'missing_funding':
            t.bills.pop(1)
        else:
            async def facts():
                return RunnerActionFacts(t.cfg.binding, datetime.now(timezone.utc), 'main', ('foreign',), True)
            t.journal.runner_facts = facts
        before = await t.financial.snapshot(t.cfg.binding)
        claims = await t.values[5].claims(t.cfg.binding)
        with pytest.raises(StorageError, match='PG_JOINT_SOURCE_' + reason):
            await t.joint.collect()
        assert await t.financial.snapshot(t.cfg.binding) == before
        assert await t.values[5].claims(t.cfg.binding) == claims
        assert t.joint._report is None and not t.financial._runtime.healthy and not t.session.sent
    finally:
        await t.financial.close()


async def release_application(database, *, spot_fee=None):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_portfolio import refresh

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_release import PgJointReleaseApplication
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository
    t = await joint(database, spot_fee=spot_fee)
    await database[0].execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)', t.cfg.account_id, t.cfg.binding.policy_hash)
    await database[0].execute('INSERT INTO life_financial.swap_terminal_policies VALUES($1,$2,64,1048576)', t.cfg.account_id, t.cfg.binding.policy_hash)
    await database[0].execute('INSERT INTO life_financial.shared_release_policies VALUES($1,$2,64,1048576)', t.cfg.account_id, t.cfg.binding.policy_hash)
    t.values[4][0] = t.command.funding.bundle.account.snapshot
    t.values[7][0] = t.command.funding.bundle
    await refresh(database, t.values, short='1', cash=str(t.command.funding.bundle.account.snapshot.spot_cash_quote))
    t.portfolio = PgPortfolioRepository(t.batch.swap)
    assert not (await t.portfolio.measure(MeasurePortfolio('nav1'), cut=(await t.financial.snapshot(t.cfg.binding)).cut, owner=t.owner)).replayed
    t.application = PgJointReleaseApplication(t.joint)
    return t


@pytest.mark.asyncio
async def test_signed_source_is_consumed_at_original_swap_terminal_and_shared_retirement_without_wallet_refund(database):
    t = await release_application(database)
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        terminal = await t.application.confirm_terminal('qualified-terminal', 'swap1')
        assert terminal.state == 'TERMINAL' and not terminal.claim_released
        assert t.joint._report is None
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
        terminal_cut = await t.financial.snapshot(t.cfg.binding)
        assert terminal_cut.balance == before.balance and terminal_cut.budget == before.budget
        retired = await t.application.retire_claim('qualified-release', 'swap1', 'qualified-terminal')
        assert retired.released and not retired.trading_permission and not retired.replayed
        assert t.joint._report is None and await t.values[5].claims(t.cfg.binding) == ()
        after = await t.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert (await t.application.confirm_terminal('qualified-terminal', 'swap1')).replayed
        assert (await t.application.retire_claim('qualified-release', 'swap1', 'qualified-terminal')).replayed
        with pytest.raises(StorageError, match='PG_OPERATION_CONFLICT'):
            await t.application.retire_claim('qualified-release', 'swap1', 'other-terminal')
        assert not t.session.sent and t.financial._runtime._observation_inflight == 0
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['terminal', 'release'])
@pytest.mark.parametrize('fault', ['foreign_order', 'epoch_at_commit', 'clock_at_commit', 'database_deadline', 'lost_reply'])
async def test_joint_release_consumes_current_source_at_physical_boundary_and_retains_original_unknown_commit_identity(database, monkeypatch, stage, fault):
    from datetime import timedelta

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    t = await release_application(database)
    runtime = t.financial._runtime
    try:
        if stage == 'release':
            assert (await t.application.confirm_terminal('qualified-terminal', 'swap1')).state == 'TERMINAL'
        before = await t.financial.snapshot(t.cfg.binding)
        claims = await t.values[5].claims(t.cfg.binding)
        original_commit = runtime._commit
        clock = t.journal.clock
        operation_id = 'guarded-' + stage
        if fault == 'foreign_order':
            t.pending['MARGIN'] = [dict(ordId='999', clOrdId='foreign', instType='MARGIN')]
        elif fault == 'database_deadline':
            # Delay inside actual deferred server work, after the client's last
            # fence check and before the final deadline constraint. Never widen
            # the 1 s statement timeout to accommodate this fault.
            await database[0].execute('''CREATE FUNCTION life_financial.zx_test_source_delay() RETURNS trigger
                LANGUAGE plpgsql SET search_path=pg_catalog AS $$ DECLARE wait double precision;BEGIN
                    wait:=extract(epoch FROM ((current_setting('life.joint_source_deadline')::jsonb->>'expires_at')::timestamptz-clock_timestamp()))+.04;
                    IF wait>=.9 THEN RAISE EXCEPTION 'TEST_DELAY_EXCEEDS_STATEMENT_BOUND';END IF;
                    IF wait>0 THEN PERFORM pg_sleep(wait);END IF;RETURN NEW;END; $$;
                CREATE CONSTRAINT TRIGGER zx_test_source_delay AFTER UPDATE ON life_financial.accounts
                    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW WHEN(NEW.version<>OLD.version)
                    EXECUTE FUNCTION life_financial.zx_test_source_delay()''')
        else:
            async def commit(transaction):
                if type(transaction).__name__ == 'CheckedCommit':
                    if fault == 'epoch_at_commit':
                        runtime._ingress_epoch += 1
                    elif fault == 'clock_at_commit':
                        t.journal.clock = lambda: clock() + timedelta(seconds=2)
                    await original_commit(transaction)
                    if fault == 'lost_reply':
                        raise ConnectionError('synthetic lost committed reply')
                else:
                    await original_commit(transaction)
            monkeypatch.setattr(runtime, '_commit', commit)
        action = (t.application.confirm_terminal(operation_id, 'swap1') if stage == 'terminal'
                  else t.application.retire_claim(operation_id, 'swap1', 'qualified-terminal'))
        if fault == 'lost_reply':
            with pytest.raises(CommitUnknown, match='PG_COMMIT_UNKNOWN'):
                await action
            requests = len(t.requests)
            recovered = (await t.application.confirm_terminal(operation_id, 'swap1') if stage == 'terminal'
                         else await t.application.retire_claim(operation_id, 'swap1', 'qualified-terminal'))
            assert recovered.replayed and len(t.requests) == requests
            assert await t.values[5].claims(t.cfg.binding) == (() if stage == 'release' else claims)
        else:
            reason = {'foreign_order': 'PG_JOINT_SOURCE_PENDING_SCOPE', 'database_deadline': 'PG_JOINT_SOURCE_EXPIRED_AT_COMMIT'}.get(fault, 'PG_JOINT_SOURCE_REPORT_REVOKED')
            with pytest.raises(StorageError, match=reason):
                await action
            assert await t.values[5].claims(t.cfg.binding) == claims
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations WHERE operation_id=$1', operation_id) == 0
        after = await t.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert t.joint._report is None and runtime._observation_inflight == 0 and not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_equal_decoded_report_and_cold_collector_cannot_recreate_source_consumption_authority(database):
    from dataclasses import replace

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.source_fence import JointSourceFence
    t = await joint(database)
    try:
        report = await t.joint.collect()
        with pytest.raises(StorageError, match='PG_JOINT_SOURCE_REPORT_REVOKED'):
            JointSourceFence(t.joint, replace(report), operation_id='fake', intent_id='swap1')
        cold_collector = PgJointAccountReconciler(t.reader, t.batch.swap, max_pages=2, max_age_ms=1000)
        with pytest.raises(StorageError, match='PG_JOINT_SOURCE_REPORT_REVOKED'):
            JointSourceFence(cold_collector, report, operation_id='fake', intent_id='swap1')
        assert len(await t.values[5].claims(t.cfg.binding)) == 1 and not t.session.sent
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('spot_fee', ['USDT', 'LIFE'])
async def test_both_products_transfer_and_paid_funding_reconcile_before_each_terminal_and_original_claim_retirement(database, spot_fee):
    t = await release_application(database, spot_fee=spot_fee)
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        original_claims = await t.values[5].claims(t.cfg.binding)
        assert {v.intent_id for v in original_claims} == {'intent1', 'swap1'}
        report = await t.joint.collect()
        assert report.product_trade_ids == (('SPOT', 'trade1'), ('SWAP', 'trade1'))
        assert report.cashflow_ids == ('779',)
        # Every effect collects a new source. A previous diagnostic report is
        # not authority for the new native cut after a terminal/release.
        for intent in ('intent1', 'swap1'):
            terminal = await t.application.confirm_terminal('terminal-' + intent, intent)
            assert terminal.state == 'TERMINAL'
            assert (await t.financial.balance(t.cfg.binding)).total_base_life == before.balance.total_base_life
            assert (await t.financial.balance(t.cfg.binding)).total_quote_usdt == before.balance.total_quote_usdt
            receipt = await t.application.retire_claim('release-' + intent, intent, 'terminal-' + intent)
            assert receipt.released and not receipt.trading_permission
        after = await t.financial.snapshot(t.cfg.binding)
        assert await t.values[5].claims(t.cfg.binding) == ()
        assert after.balance.total_base_life == before.balance.total_base_life
        assert after.balance.total_quote_usdt == before.balance.total_quote_usdt
        assert after.budget.session_execution_loss_usdt == before.budget.session_execution_loss_usdt
        assert after.budget.campaign_execution_loss_usdt == before.budget.campaign_execution_loss_usdt
        assert after.budget.session_subsidy_committed_usdt == before.budget.session_subsidy_committed_usdt
        assert await database[0].fetchval('SELECT gross_debits_usdt FROM life_financial.funding_observations') == D('.1')
        assert not t.session.sent and t.joint._report is None
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['terminal', 'release'])
@pytest.mark.parametrize('committed', [False, True])
async def test_cancelled_joint_release_caller_cannot_restore_source_or_repeat_a_committed_effect(database, monkeypatch, stage, committed):
    import asyncio

    t = await release_application(database)
    runtime = t.financial._runtime
    entered, finish = asyncio.Event(), asyncio.Event()
    original_commit = runtime._commit
    operation_id = 'cancelled-' + stage
    task = None
    try:
        if stage == 'release':
            await t.application.confirm_terminal('qualified-terminal', 'swap1')
        before = await t.financial.snapshot(t.cfg.binding)
        claims = await t.values[5].claims(t.cfg.binding)

        async def commit(transaction):
            if type(transaction).__name__ != 'CheckedCommit':
                return await original_commit(transaction)
            if committed:
                await original_commit(transaction)
            entered.set()
            await finish.wait()
            if not committed:
                await original_commit(transaction)

        monkeypatch.setattr(runtime, '_commit', commit)
        action = (t.application.confirm_terminal(operation_id, 'swap1') if stage == 'terminal'
                  else t.application.retire_claim(operation_id, 'swap1', 'qualified-terminal'))
        task = asyncio.create_task(action)
        waiting = asyncio.create_task(entered.wait())
        try:
            done, _ = await asyncio.wait((waiting, task), timeout=5, return_when=asyncio.FIRST_COMPLETED)
            if task in done:
                # A source/SQL failure before the injection must stay RED and
                # expose its actual reason, rather than a five-second timeout.
                await task
                raise AssertionError('release completed before its owned COMMIT injection')
            if waiting not in done:
                raise TimeoutError('owned COMMIT injection was not reached')
        finally:
            waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert t.joint._report is None and runtime._observation_inflight == 0 and not runtime.healthy
        # The caller's cancellation must not abandon the physical transaction.
        # Before COMMIT the revoked original source forces rollback; after it,
        # only original-ID receipt readback can recover the one monetary effect.
        physical = tuple(runtime._tasks)
        assert physical
        finish.set()
        results = await asyncio.gather(*physical, return_exceptions=True)
        if committed:
            assert not any(isinstance(v, BaseException) for v in results)
            requests = len(t.requests)
            receipt = (await t.application.confirm_terminal(operation_id, 'swap1') if stage == 'terminal'
                       else await t.application.retire_claim(operation_id, 'swap1', 'qualified-terminal'))
            assert receipt.replayed and len(t.requests) == requests
        else:
            assert any(isinstance(v, StorageError) and str(v) == 'PG_JOINT_SOURCE_REPORT_REVOKED' for v in results)
        count = await database[0].fetchval('SELECT count(*) FROM life_financial.operations WHERE operation_id=$1', operation_id)
        assert count == int(committed)
        assert await t.values[5].claims(t.cfg.binding) == (() if committed and stage == 'release' else claims)
        after = await t.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget and not t.session.sent
    finally:
        finish.set()
        if task is not None:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('boundary', ['initial_scope', 'provider_round', 'final_scope'])
async def test_ingress_arriving_during_joint_collection_cannot_be_adopted_as_the_report_epoch(database, monkeypatch, boundary):
    t = await joint(database)
    runtime = t.financial._runtime
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        claims = await t.values[5].claims(t.cfg.binding)
        target = '_sample' if boundary == 'provider_round' else '_scope'
        original = getattr(t.joint, target)
        calls = 0

        async def changed(*args, **kwargs):
            nonlocal calls
            result = await original(*args, **kwargs)
            calls += 1
            if calls == (2 if boundary == 'final_scope' else 1):
                # Mirrors intake's synchronous invalidation before its first
                # await. Its SQL capture can still be pending with no cut change.
                runtime._ingress_epoch += 1
            return result

        monkeypatch.setattr(t.joint, target, changed)
        with pytest.raises(StorageError, match='PG_JOINT_SOURCE_INGRESS_CHANGED'):
            await t.joint.collect()
        assert t.joint._report is None and not runtime.healthy
        assert await t.values[5].claims(t.cfg.binding) == claims
        after = await t.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget and not t.session.sent
    finally:
        await t.financial.close()
