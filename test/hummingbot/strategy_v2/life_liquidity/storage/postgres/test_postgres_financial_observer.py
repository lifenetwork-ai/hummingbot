"""Real PG + signed fake HTTP collection -> original financial intake/apply."""
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_account_source import source
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport
from unittest.mock import AsyncMock

import pytest

from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_observer import PgSpotFinancialObserver
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader


async def observed_source(database):
    t = await transport(database)
    await t.place()
    c = database[0]
    at = await c.fetchval('SELECT clock_timestamp()')
    ms = int(at.timestamp() * 1000)
    await c.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', t.cfg.account_id, t.cfg.binding.policy_hash)
    await c.execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)",
                    t.cfg.account_id, t.cfg.binding.policy_hash, at - timedelta(seconds=1))
    await c.execute("INSERT INTO life_financial.bill_approvals VALUES($1,'101','USDT',2,to_timestamp($2::double precision/1000),$3)",
                    t.cfg.account_id, ms, b'synthetic-preverified-import')
    order = {'clOrdId': 'wire1', 'ordId': '123', 'instType': 'SPOT', 'instId': 'LIFE-USDT', 'side': 'buy',
             'tdMode': 'cash', 'ordType': 'post_only', 'sz': '2', 'px': '1', 'accFillSz': '1', 'state': 'partially_filled'}
    fill = {'billId': '104', 'tradeId': 'trade1', 'clOrdId': 'wire1', 'ordId': '123', 'instType': 'SPOT',
            'instId': 'LIFE-USDT', 'side': 'buy', 'fillSz': '1', 'fillPx': '1', 'feeCcy': 'USDT', 'fee': '-.02', 'ts': str(ms)}
    bills = [dict(billId='104', type='2', subType='1', ccy='USDT', balChg='-1.02', fee='-.02', tradeId='trade1',
                  ordId='123', clOrdId='wire1', instId='LIFE-USDT', ts=str(ms)),
             dict(billId='103', type='2', subType='2', ccy='LIFE', balChg='1', fee='0', tradeId='trade1',
                  ordId='123', clOrdId='wire1', instId='LIFE-USDT', ts=str(ms)),
             {'billId': '101', 'type': '1', 'subType': '11', 'from': '6', 'to': '18', 'ccy': 'USDT', 'balChg': '2', 'ts': str(ms)},
             {'billId': '100'}]
    wallet = [{'details': [{'ccy': 'LIFE', 'cashBal': '6', 'liab': '0'}, {'ccy': 'USDT', 'cashBal': '10.98', 'liab': '0'}]}]
    payloads = {'/api/v5/account/config': [{'uid': '12345'}], '/api/v5/account/bills-archive': bills,
                '/api/v5/trade/fills-history': [fill], '/api/v5/trade/order': [order],
                '/api/v5/trade/orders-pending': [order], '/api/v5/trade/orders-algo-pending': [], '/api/v5/account/balance': wallet}
    requests, values = [], []
    t.connector._api_request_url = AsyncMock(side_effect=lambda path_url, **_: 'https://www.okx.com' + path_url)

    async def http(**kwargs):
        from urllib.parse import urlsplit
        requests.append(kwargs)
        result = Response()

        async def body():
            return {'code': '0', 'data': payloads[urlsplit(kwargs['url']).path]}
        result.json = body
        return result
    t.session.request = http

    def value(trade_id, at_ms):
        values.append((trade_id, at_ms))
        return IndependentFillObservation(D('1'), at_ms, at_ms, 'independent_market')
    reader = PgSpotAccountReader(await source(t), max_reads=64, max_bytes=1048576, timeout_ms=1000)
    observer = PgSpotFinancialObserver(reader, independent_fill_value=value, independent_cashflow_value=value,
                                       max_pages=2, max_rows=64, max_bytes=1048576)
    return t, observer, payloads, requests, values


@pytest.mark.asyncio
async def test_authenticated_complete_collection_captures_all_values_before_causal_accounting_then_replays(database, monkeypatch):
    t, observer, payloads, requests, values = await observed_source(database)
    original, captures = observer.intake.capture, []

    async def capture(*args, **kwargs):
        captures.append(await database[0].fetchval('SELECT count(*) FROM life_financial.fills')
                        + await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows'))
        return await original(*args, **kwargs)
    monkeypatch.setattr(observer.intake, 'capture', capture)
    try:
        receipts = await observer.observe()
        assert len(receipts) == 2 and all(row.state == 'APPLIED' for row in receipts)
        assert captures[:2] == [0, 0]
        view = await t.repo.snapshot(t.cfg.binding)
        assert view.balance.total_base_life == D('6') and view.balance.total_quote_usdt == D('10.98')
        assert view.balance.reserved_quote_usdt > 0 and not view.recovery_required
        assert await observer.observe() == () and (await t.repo.snapshot(t.cfg.binding)).cut == view.cut
        assert len(values) == 1 and len(t.session.sent) == 1
        assert all(row['data'] is None and row['allow_redirects'] is False for row in requests)
        assert await database[0].fetchval('SELECT financial_captured FROM life_financial.spot_ingress_progress') == 2
        assert await database[0].fetchval('SELECT financial_delivered FROM life_financial.spot_ingress_progress') == 2
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'ACKED'
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing_anchor', 'unknown_fill', 'wrong_order', 'account_fill', 'bill_fee', 'bill_amount',
                                   'open_order', 'algo', 'wallet', 'liability', 'foreign_asset', 'uid'])
async def test_incomplete_or_conflicting_account_collection_applies_no_financial_facts(database, fault):
    t, observer, p, requests, values = await observed_source(database)
    if fault == 'missing_anchor':
        p['/api/v5/account/bills-archive'].pop()
    elif fault == 'unknown_fill':
        p['/api/v5/trade/fills-history'] = [dict(p['/api/v5/trade/fills-history'][0], tradeId='foreign')]
    elif fault == 'wrong_order':
        p['/api/v5/trade/order'][0]['px'] = '2'
    elif fault == 'account_fill':
        p['/api/v5/trade/fills-history'][0]['side'] = 'sell'
    elif fault == 'bill_fee':
        p['/api/v5/account/bills-archive'][0]['fee'] = '-.03'
    elif fault == 'bill_amount':
        p['/api/v5/account/bills-archive'][0]['balChg'] = '-1.03'
    elif fault == 'open_order':
        p['/api/v5/trade/orders-pending'] = []
    elif fault == 'algo':
        p['/api/v5/trade/orders-algo-pending'] = [{'ordId': 'foreign'}]
    elif fault in ('wallet', 'liability'):
        p['/api/v5/account/balance'][0]['details'][0]['cashBal' if fault == 'wallet' else 'liab'] = '1'
    elif fault == 'foreign_asset':
        p['/api/v5/account/balance'][0]['details'].append({'ccy': 'BTC', 'cashBal': '1'})
    else:
        p['/api/v5/account/config'][0]['uid'] = '999'
    try:
        before = await t.repo.snapshot(t.cfg.binding)
        with pytest.raises(StorageError):
            await observer.observe()
        after = await t.repo.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert not t.repo._runtime.healthy and len(t.session.sent) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_financial_ingress') == 0
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_lost_financial_reply_preserves_original_values_and_repairs_delivery_without_repricing(database, monkeypatch):
    t, observer, payloads, requests, values = await observed_source(database)
    apply = t.repo.apply_cashflow

    async def lost(*args, **kwargs):
        await apply(*args, **kwargs)
        raise CommitUnknown('PG_SYNTHETIC_COMMITTED_REPLY_LOST')
    monkeypatch.setattr(t.repo, 'apply_cashflow', lost)
    try:
        with pytest.raises(CommitUnknown):
            await observer.observe()
        original = await observer.intake.pending(t.cfg.binding, max_rows=64, max_bytes=1048576)
        assert len(original) == 2 and not t.repo._runtime.healthy
        assert (await t.repo.balance(t.cfg.binding)).total_quote_usdt == 12
        monkeypatch.setattr(t.repo, 'apply_cashflow', apply)
        receipts = await observer.observe()
        assert len(receipts) == 2 and receipts[0].replayed
        assert await observer.intake.pending(t.cfg.binding, max_rows=64, max_bytes=1048576) == ()
        assert len(values) == 2 and len(t.session.sent) == 1
        assert not t.repo._runtime.healthy  # Observation never reactivates dispatch.
        assert (await t.repo.balance(t.cfg.binding)).total_quote_usdt == D('10.98')
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('target', ['fill', 'bill', 'order', 'wallet'])
async def test_raw_money_must_remain_provider_decimal_strings(database, target):
    t, observer, p, requests, values = await observed_source(database)
    if target == 'fill':
        p['/api/v5/trade/fills-history'][0]['fillPx'] = 1.0
    elif target == 'bill':
        p['/api/v5/account/bills-archive'][0]['balChg'] = -1.02
    elif target == 'order':
        p['/api/v5/trade/order'][0]['sz'] = 2
    else:
        p['/api/v5/account/balance'][0]['details'][0]['cashBal'] = 6
    try:
        before = await t.repo.balance(t.cfg.binding)
        with pytest.raises(StorageError, match='PG_FINANCIAL_OBSERVER_WIRE_TYPES_INVALID'):
            await observer.observe()
        assert await t.repo.balance(t.cfg.binding) == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_financial_ingress') == 0
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_untrusted_pending_reference_is_requalified_but_never_replaced(database, monkeypatch):
    from dataclasses import replace
    t, observer, p, requests, values = await observed_source(database)
    original = observer.intake.capture

    async def capture(command, **kwargs):
        if hasattr(command, 'fills'):
            command = replace(command, fills=tuple(replace(fill, independent_price_usdt=D('1.1')) for fill in command.fills))
            await original(command, **kwargs)
            raise StorageError('PG_SYNTHETIC_UNTRUSTED_INTAKE')
        return await original(command, **kwargs)
    monkeypatch.setattr(observer.intake, 'capture', capture)
    try:
        with pytest.raises(StorageError, match='PG_SYNTHETIC_UNTRUSTED_INTAKE'):
            await observer.observe()
        monkeypatch.setattr(observer.intake, 'capture', original)
        with pytest.raises(StorageError, match='PG_FINANCIAL_OBSERVER_PENDING_VALUE_UNQUALIFIED'):
            await observer.observe()
        pending = await observer.intake.pending(t.cfg.binding, max_rows=64, max_bytes=1048576)
        assert pending[1].command.fills[0].independent_price_usdt == D('1.1')
        assert (await t.repo.balance(t.cfg.binding)).total_quote_usdt == 10
        assert len(t.session.sent) == 1 and not t.repo._runtime.healthy
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_state_change_during_collection_keeps_new_financial_input_unapplied(database, monkeypatch):
    t, observer, p, requests, values = await observed_source(database)
    original = observer.reader.get_spot_cash_balances

    async def changed():
        result = await original()
        await database[0].execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', t.cfg.account_id)
        return result
    monkeypatch.setattr(observer.reader, 'get_spot_cash_balances', changed)
    try:
        with pytest.raises(StorageError, match='PG_FINANCIAL_OBSERVER_STATE_CHANGED'):
            await observer.observe()
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_financial_ingress') == 0
        assert (await t.repo.balance(t.cfg.binding)).total_quote_usdt == 10
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_cold_original_delivery_repair_uses_new_runtime_without_restoring_permission(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t, observer, p, requests, values = await observed_source(database)
    mark = observer.intake.mark_applied
    cold = None

    async def lost(*args, **kwargs):
        await mark(*args, **kwargs)
        raise CommitUnknown('PG_SYNTHETIC_DELIVERY_REPLY_LOST')
    monkeypatch.setattr(observer.intake, 'mark_applied', lost)
    try:
        with pytest.raises(CommitUnknown):
            await observer.observe()
        originals = await observer.intake.pending(t.cfg.binding, max_rows=64, max_bytes=1048576)
        assert len(originals) == 1
        cold = PgSpotRepository(await PgRuntime.open(t.cfg))
        journal = PgQuoteRunnerJournal(cold, owner=t.owner, controller_id=t.journal.controller_id,
                                       config_version=t.journal.config_version, runner_facts=t.journal.runner_facts,
                                       local_ready=lambda: False, clock=t.journal.clock, max_actions=64)
        account = await PgQuoteAccountSource.create(journal, connector=t.connector, admission_source=t.facts,
                                                    max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=100,
                                                    max_pending=2, collection_timeout_ms=1000)
        reader = PgSpotAccountReader(account, max_reads=64, max_bytes=1048576, timeout_ms=1000)
        restored = PgSpotFinancialObserver(reader, independent_fill_value=observer.fill_value,
                                           independent_cashflow_value=observer.cashflow_value,
                                           max_pages=2, max_rows=64, max_bytes=1048576)
        await restored.observe()
        assert await restored.intake.pending(t.cfg.binding, max_rows=64, max_bytes=1048576) == ()
        assert (await cold.balance(t.cfg.binding)).total_quote_usdt == D('10.98')
        assert (await cold.balance(t.cfg.binding)).reserved_quote_usdt > 0
        assert len(t.session.sent) == 1 and journal._ready() is False
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.fills') == 1
    finally:
        if cold is not None:
            await cold.close()
        await t.close()


@pytest.mark.asyncio
async def test_late_earlier_fill_keeps_complete_native_snapshot_and_original_values(database):
    t, observer, p, requests, values = await observed_source(database)
    try:
        await observer.observe()
        old = p['/api/v5/trade/fills-history'][0]
        late = dict(old, billId='107', tradeId='trade0', ts=str(int(old['ts']) - 1))
        p['/api/v5/trade/fills-history'] = [late, old]
        p['/api/v5/trade/order'][0].update(accFillSz='2', state='filled')
        p['/api/v5/trade/orders-pending'] = []
        p['/api/v5/account/bills-archive'][:0] = [
            dict(p['/api/v5/account/bills-archive'][0], billId='107', tradeId='trade0', ts=late['ts']),
            dict(p['/api/v5/account/bills-archive'][1], billId='106', tradeId='trade0', ts=late['ts'])]
        p['/api/v5/account/balance'][0]['details'][0]['cashBal'] = '7'
        p['/api/v5/account/balance'][0]['details'][1]['cashBal'] = '9.96'
        receipts = await observer.observe()
        assert len(receipts) == 1 and receipts[0].cumulative_base_life == 2
        assert (await t.repo.balance(t.cfg.binding)).total_quote_usdt == D('9.96')
        assert await observer.observe() == ()
        assert len(values) == 2 and len(t.session.sent) == 1
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'ACKED'
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_new_complete_authenticated_snapshot_covers_older_pending_intake_without_repricing(database, monkeypatch):
    t, observer, p, requests, values = await observed_source(database)
    apply = observer.intake.capture_and_apply

    async def unavailable(*args, **kwargs):
        raise StorageError('PG_SYNTHETIC_APPLICATION_UNAVAILABLE')
    monkeypatch.setattr(observer.intake, 'capture_and_apply', unavailable)
    try:
        with pytest.raises(StorageError, match='PG_SYNTHETIC_APPLICATION_UNAVAILABLE'):
            await observer.observe()
        old_pending = await observer.intake.pending(t.cfg.binding, max_rows=64, max_bytes=1048576)
        original_fill = old_pending[1]
        old = p['/api/v5/trade/fills-history'][0]
        late = dict(old, billId='107', tradeId='trade0', ts=str(int(old['ts']) - 1))
        p['/api/v5/trade/fills-history'] = [late, old]
        p['/api/v5/trade/order'][0].update(accFillSz='2', state='filled')
        p['/api/v5/trade/orders-pending'] = []
        p['/api/v5/account/bills-archive'][:0] = [
            dict(p['/api/v5/account/bills-archive'][0], billId='107', tradeId='trade0', ts=late['ts']),
            dict(p['/api/v5/account/bills-archive'][1], billId='106', tradeId='trade0', ts=late['ts'])]
        p['/api/v5/account/balance'][0]['details'][0]['cashBal'] = '7'
        p['/api/v5/account/balance'][0]['details'][1]['cashBal'] = '9.96'
        monkeypatch.setattr(observer.intake, 'capture_and_apply', apply)
        receipts = await observer.observe()
        assert len(receipts) == 3 and receipts[-1].cumulative_base_life == 2
        original = await observer.intake.lookup(original_fill.ingress_id)
        assert original.command == original_fill.command and original.applied_operation_id == receipts[-1].operation_id
        assert await observer.intake.pending(t.cfg.binding, max_rows=64, max_bytes=1048576) == ()
        assert (await t.repo.balance(t.cfg.binding)).total_quote_usdt == D('9.96')
        assert not t.repo._runtime.healthy and len(t.session.sent) == 1
    finally:
        await t.close()
