"""Signed account-wide collateral reconciliation; synthetic HTTP and money only."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_account_source import source
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import transport
from unittest.mock import AsyncMock

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.collateral_source import parse_account
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader


def bodies(*, mode='net_mode', long='0', short='1', mark='1', cash='9.89', entry='1.01'):
    config = {'uid': '12345', 'acctLv': '2', 'posMode': mode, 'autoLoan': False}
    rows = []
    upl = D('0')
    for side, amount in [('long', long), ('short', short)]:
        if D(amount) == 0:
            continue
        signed = D(amount) if side == 'long' else -D(amount)
        profit = signed * (D(mark) - D(entry))
        upl += profit
        rows.append(dict(instType='SWAP', instId='LIFE-USDT-SWAP', mgnMode='cross', ccy='USDT',
                         posSide='net' if mode == 'net_mode' else side, pos=str(signed if mode == 'net_mode' else D(amount)),
                         lever='2', avgPx=entry, markPx=mark, upl=str(profit), imr='.1', mmr='.01', uTime='100'))
    details = []
    for ccy, amount, profit in [('LIFE', '5', D('0')), ('USDT', cash, upl)]:
        details.append(dict(ccy=ccy, cashBal=amount, eq=str(D(amount) + profit), upl=str(profit),
                            liab='0', crossLiab='0', isoLiab='0', uplLiab='0', interest='0',
                            isoEq='0', isoUpl='0', rewardBal='0', fixedBal='0', uTime='100',
                            imr=str(D('.1') * len(rows) if ccy == 'USDT' else 0),
                            mmr=str(D('.01') * len(rows) if ccy == 'USDT' else 0)))
    return config, {'uTime': '100', 'details': details}, rows


def parse(config, balance, positions, *, mode='ONEWAY'):
    return parse_account(config, balance, positions, account_uid='12345', position_mode=mode,
                         leverage=2, contract_value_life=D('.1'), lot_contracts=D('1'), observed_at_ms=1000)


@pytest.mark.parametrize('mode', ['net_mode', 'long_short_mode'])
@pytest.mark.parametrize('mark', ['.99', '1.02'])
def test_cash_collateral_counts_original_usdt_and_signed_upl_once(mode, mark):
    config, balance, positions = bodies(mode=mode, mark=mark)
    # Contract units on the wire differ from the native LIFE position.
    positions[0]['pos'] = '-10' if mode == 'net_mode' else '10'
    value = parse(config, balance, positions, mode='ONEWAY' if mode == 'net_mode' else 'HEDGE')
    assert value.base_life == 5 and value.cash_usdt == D('9.89')
    assert value.short_base == 1 and value.short_entry == D('1.01')
    assert value.collateral_usdt == D('9.89') + D('1.01') - D(mark)
    assert value.initial_margin_usdt == D('.1') and value.maintenance_margin_usdt == D('.01')


@pytest.mark.parametrize(
    'fault,reason',
    [
        ('borrow', 'LIABILITY'),
        ('blank_liability', 'AMOUNT'),
        ('missing_liability', 'AMOUNT'),
        ('foreign_balance', 'ASSET_SCOPE'),
        ('foreign_position', 'POSITION_SCOPE'),
        ('isolated', 'POSITION_SCOPE'),
        ('duplicate_asset', 'ASSET_SCOPE'),
        ('duplicate_side', 'POSITION_SCOPE'),
        ('mode', 'MODE'),
        ('uid', 'UID'),
        ('auto_loan', 'MODE'),
        ('cash_equity', 'EQUITY'),
        ('position_upl', 'EQUITY'),
        ('balance_upl', 'EQUITY'),
        ('margin', 'MARGIN'),
        ('nan', 'AMOUNT'),
        ('future_update', 'TIMESTAMP'),
        ('fractional_contract', 'CONTRACTS'),
        ('leverage', 'MODE'),
        ('missing_asset', 'ASSET_SCOPE'),
        ('reward', 'UNSUPPORTED_BALANCE'),
    ],
)
def test_incomplete_liabilities_scope_and_inconsistent_equity_are_never_qualified(fault, reason):
    c, b, p = bodies()
    p[0]['pos'] = '-10'
    if fault == 'borrow':
        b['details'][1]['liab'] = '.001'
    elif fault == 'blank_liability':
        b['details'][1]['crossLiab'] = ''
    elif fault == 'missing_liability':
        b['details'][1].pop('liab')
    elif fault == 'foreign_balance':
        b['details'].append({**b['details'][0], 'ccy': 'BTC'})
    elif fault == 'foreign_position':
        p.append({**p[0], 'instId': 'BTC-USDT-SWAP'})
    elif fault == 'isolated':
        p[0]['mgnMode'] = 'isolated'
    elif fault == 'duplicate_asset':
        b['details'].append(b['details'][0])
    elif fault == 'duplicate_side':
        p.append(p[0])
    elif fault == 'mode':
        c['acctLv'] = '3'
    elif fault == 'uid':
        c['uid'] = 'foreign'
    elif fault == 'auto_loan':
        c['autoLoan'] = True
    elif fault == 'cash_equity':
        b['details'][1]['eq'] = '100'
    elif fault == 'position_upl':
        p[0]['upl'] = '100'
    elif fault == 'balance_upl':
        b['details'][1]['upl'] = '100'
    elif fault == 'margin':
        b['details'][1]['imr'] = '0'
    elif fault == 'nan':
        b['details'][1]['cashBal'] = 'NaN'
    elif fault == 'future_update':
        p[0]['uTime'] = '1001'
    elif fault == 'fractional_contract':
        p[0]['pos'] = '-10.1'
    elif fault == 'leverage':
        p[0]['lever'] = '3'
    elif fault == 'missing_asset':
        b['details'].pop(0)
    else:
        b['details'][1]['rewardBal'] = '1'
    with pytest.raises(StorageError, match='PG_COLLATERAL_' + reason):
        parse(c, b, p)


@pytest.mark.asyncio
async def test_account_wide_source_routes_are_signed_unfiltered_and_durable_before_parsing(database):
    t = await transport(database)
    requests = []
    config, balance, positions = bodies(short='0', cash='10')
    t.connector._api_request_url = AsyncMock(side_effect=lambda path_url, **_: 'https://www.okx.com' + path_url)

    async def http(**kwargs):
        requests.append(kwargs)
        response = Response()

        async def body():
            tail = kwargs['url'].rsplit('/', 1)[1]
            return {'code': '0', 'data': deepcopy(positions if tail == 'positions' else [config if tail == 'config' else balance])}
        response.json = body
        return response
    t.session.request = http
    try:
        reader = PgSpotAccountReader(await source(t), max_reads=8, max_bytes=1048576, timeout_ms=1000)
        async with reader.collection():
            assert (await reader.get_account_configuration())['data'] == [config]
            assert (await reader.get_all_account_positions())['data'] == positions
        assert len(requests) == 4
        assert all(r['params'] in ({}, None) and r['data'] is None and r['allow_redirects'] is False for r in requests)
        assert all(r['headers']['OK-ACCESS-KEY'] == 'synthetic-key' for r in requests)
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
        pages = await PgProviderJournal(t.repo).read_collection(reader.collection_id, max_rows=8, max_bytes=1048576)
        assert len(pages) == 2 and all(not p.trading_permission for p in pages)
        assert not t.session.sent
    finally:
        await t.close()


async def reconciler(database, *, before_apply=None):
    from datetime import datetime, timezone
    from test.hummingbot.core.web_assistant.connections.test_request_guard import Session, Throttler
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_batch import setup
    from types import SimpleNamespace

    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
    from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.collateral_source import PgCollateralReconciler
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_account_source import (
        PgObservationAccountSource,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal

    values, batch, command = await setup(database, carry_options=dict(refresh_initial_facts=True))
    financial, cfg, owner = values[:3]
    if before_apply is not None:
        command = await before_apply(values, command)
    applied = await batch.apply(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    assert applied.state == 'APPLIED', await database[0].fetchval('SELECT reason FROM life_financial.joint_financial_batches')
    await database[0].execute("UPDATE life_financial.accounts SET halted=true,mode='RECOVERY_ONLY' WHERE account_id=$1", cfg.account_id)
    connector = OkxExchange('synthetic-key', 'synthetic-secret', 'synthetic-pass', trading_pairs=[], trading_required=False)
    session, throttler = Session(), Throttler()
    throttler.release.set()
    assistant = RESTAssistant(RESTConnection(session), throttler, auth=connector.authenticator)
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=assistant))
    connector._api_request_url = AsyncMock(side_effect=lambda path_url, **_: 'https://www.okx.com' + path_url)
    journal = PgQuoteRunnerJournal(financial, owner=owner, controller_id='main', config_version=1,
                                   runner_facts=lambda: None, local_ready=lambda: False,
                                   clock=lambda: datetime.now(timezone.utc), max_actions=64)
    account = await PgObservationAccountSource.create(journal, connector=connector, max_uid_age_ms=1000,
                                                      max_auth_age_ms=1000, max_clock_skew_ms=100,
                                                      max_pending=2, collection_timeout_ms=1000)
    reader = PgSpotAccountReader(account, max_reads=8, max_bytes=1048576, timeout_ms=1000)
    config, balance, positions = bodies(mode='long_short_mode')
    positions[0]['pos'] = '4'  # Native policy .25 LIFE per contract.
    # Original economic update times, never re-dated to collection time. LIFE
    # has not changed; the USDT balance and derivative position have changed.
    trade_at = command.swap_fills[0].fills[0].fill_at_ms
    money_at = max(trade_at, command.funding.bundle.funding.payments[0].settled_at_ms)
    positions[0]['uTime'] = str(trade_at)
    balance['uTime'] = balance['details'][1]['uTime'] = str(money_at)
    requests = []

    async def http(**kwargs):
        requests.append(kwargs)
        response = Response()

        async def body():
            tail = kwargs['url'].rsplit('/', 1)[1]
            return {'code': '0', 'data': deepcopy(positions if tail == 'positions' else [config if tail == 'config' else balance])}
        response.json = body
        return response
    session.request = http
    return SimpleNamespace(**locals(), collect=PgCollateralReconciler(reader, batch.swap).collect)


@pytest.mark.asyncio
async def test_settled_fill_and_paid_funding_reconcile_after_halt_without_resetting_claims_budgets_or_permission(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.provider_journal import PgProviderJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t = await reconciler(database)
    cold = None
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        claims = await t.values[5].claims(t.cfg.binding)
        healthy = t.financial._runtime.healthy
        report = await t.collect()
        assert report.cut == before.cut and report.account.cash_usdt == D('9.89')
        assert report.account.collateral_usdt == D('9.90') and report.account.short_base == 1
        assert not report.trading_permission and t.financial._runtime.healthy == healthy
        after = await t.financial.snapshot(t.cfg.binding)
        assert after == before and await t.values[5].claims(t.cfg.binding) == claims
        assert after.budget.session_execution_loss_usdt == D('.01')
        assert await database[0].fetchval('SELECT gross_debits_usdt FROM life_financial.funding_observations') == D('.1')
        assert len(t.requests) == 8 and all(row['method'] == 'GET' for row in t.requests)
        assert not t.session.sent
        await t.financial.close()
        cold = await PgRuntime.open(t.cfg)
        financial = PgSpotRepository(cold)
        pages = await PgProviderJournal(financial).read_collection(report.collection_id, max_rows=8, max_bytes=1048576)
        assert len(pages) == 6 and all(not page.trading_permission for page in pages)
        assert (await financial.snapshot(t.cfg.binding)).balance == before.balance
        assert (await financial.snapshot(t.cfg.binding)).budget == before.budget
        # Retained pages are explicitly untrusted diagnostics, not a cold
        # authentication token. A fresh signed collection is still required.
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
    finally:
        if cold is not None:
            await cold.close()
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'fault,reason',
    [
        ('cash', 'NATIVE_MISMATCH'),
        ('entry', 'NATIVE_MISMATCH'),
        ('liability', 'LIABILITY_NONZERO'),
        ('stable', 'SOURCE_CHANGED'),
        ('native_cut', 'NATIVE_CUT_CHANGED'),
        ('pending', 'PENDING_RESOLUTION_REQUIRED'),
        ('native_liability', 'LIABILITY_NONZERO'),
        ('pre_native_balance', 'TIMESTAMP_PREDATES_NATIVE'),
        ('pre_native_position', 'TIMESTAMP_PREDATES_NATIVE'),
    ],
)
async def test_provider_mismatch_or_changed_native_cut_never_publishes_reconciliation_or_releases_money(database, fault, reason):
    t = await reconciler(database)
    original = t.session.request
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        claims = await t.values[5].claims(t.cfg.binding)
        if fault == 'cash':
            t.balance['details'][1]['cashBal'] = '10'
            t.balance['details'][1]['eq'] = '10.01'
        elif fault == 'entry':
            t.positions[0].update(avgPx='1.02', upl='.02')
            t.balance['details'][1].update(upl='.02', eq='9.91')
        elif fault == 'liability':
            t.balance['details'][1]['isoLiab'] = '.001'
        elif fault == 'pre_native_balance':
            t.balance['details'][1]['uTime'] = '100'
        elif fault == 'pre_native_position':
            t.positions[0]['uTime'] = '100'
        elif fault == 'pending':
            # Capture an actual conflicting observation, not a hand-made flag.
            bad = replace(t.command, operation_id='unresolved', funding=replace(t.command.funding, operation_id='unresolved-funding',
                          bundle=replace(t.command.funding.bundle, account=replace(t.command.funding.bundle.account,
                                         snapshot=replace(t.command.funding.bundle.account.snapshot, spot_cash_quote=D('100'))))))
            assert (await t.batch.apply(bad, cut=before.cut, owner=t.owner)).state == 'RECOVERY_REQUIRED'
        elif fault == 'native_liability':
            await database[0].execute("UPDATE life_financial.balances SET liability=.001,version=version+1 WHERE asset='USDT'")
            before = await t.financial.snapshot(t.cfg.binding)

        async def changed(**kwargs):
            response = await original(**kwargs)
            if len(t.requests) == 4:
                if fault == 'stable':
                    t.balance['details'][1].update(cashBal='10', eq='10.01')
                elif fault == 'native_cut':
                    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_session import tick
                    await t.financial.tick_session(tick(t.owner), cut=(await t.financial.snapshot(t.cfg.binding)).cut, owner=t.owner)
            return response
        t.session.request = changed
        with pytest.raises(StorageError, match='PG_COLLATERAL_' + reason):
            await t.collect()
        after = await t.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert await t.values[5].claims(t.cfg.binding) == claims
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert not t.financial._runtime.healthy and not t.session.sent
        if fault in ('pending', 'native_liability'):
            assert not t.requests
        else:
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_provider_pages') == (3 if fault == 'liability' else 6)
    finally:
        await t.financial.close()


def test_hedge_legs_are_not_netted_for_exposure_or_counted_as_two_cash_wallets():
    c, b, p = bodies(mode='long_short_mode', long='2', short='1', mark='1.02')
    p[0]['pos'], p[1]['pos'] = '20', '10'
    value = parse(c, b, p, mode='HEDGE')
    assert (value.long_base, value.short_base) == (2, 1)
    assert value.collateral_usdt == D('9.90') and value.cash_usdt == D('9.89')
    # A mark change can preserve net UPL on a neutral hedge. It still changes
    # the account source and must be detected by the double-read collector.
    c, b, p = bodies(mode='long_short_mode', long='1', short='1')
    for row in p:
        row['pos'] = '10'
    before = parse(c, b, p, mode='HEDGE')
    for row in p:
        row['markPx'] = '2'
        row['upl'] = '.99' if row['posSide'] == 'long' else '-.99'
    assert before != parse(c, b, p, mode='HEDGE')


@pytest.mark.asyncio
async def test_reconciliation_context_is_scoped_bounded_read_only_and_pending_aware_for_reporter(database):
    from uuid import uuid4

    import asyncpg
    t = await reconciler(database)
    reporter = await asyncpg.connect(t.cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        query = 'SELECT life_financial.collateral_reconciliation_context($1,$2,$3)'
        assert await reporter.fetchval(query, t.cfg.account_id, 64, 1048576)
        with pytest.raises(asyncpg.RaiseError, match='LIFE_ACCOUNT_SCOPE_INVALID'):
            await reporter.fetchval(query, uuid4(), 64, 1048576)
        for rows, size, reason in [(None, 1048576, 'READ_BOUND_INVALID'), (0, 1048576, 'READ_BOUND_INVALID'),
                                   (64, 1, 'READ_BOUND'), (1, 1048576, 'READ_BOUND')]:
            with pytest.raises(asyncpg.RaiseError, match='LIFE_COLLATERAL_' + reason):
                await reporter.fetchval(query, t.cfg.account_id, rows, size)
        assert await t.financial.snapshot(t.cfg.binding) == before
    finally:
        await reporter.close()
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['unsigned', 'filtered', 'uid_changed', 'credential_changed', 'runtime_changed', 'cancelled'])
async def test_collateral_collection_uses_original_signed_transport_and_revokes_on_unavailable_identity(database, fault):
    import asyncio
    t = await reconciler(database)
    original_auth, original_http = t.assistant._authenticate, t.session.request
    try:
        before = await t.financial.snapshot(t.cfg.binding)

        async def authenticate(request):
            if request.url.endswith('/positions'):
                if fault == 'unsigned':
                    return request
                if fault == 'filtered':
                    request.params['instId'] = 'LIFE-USDT-SWAP'
            return await original_auth(request)
        t.assistant._authenticate = authenticate

        async def http(**kwargs):
            result = await original_http(**kwargs)
            if kwargs['url'].endswith('/positions') and len(t.requests) == 4:
                if fault == 'uid_changed':
                    t.config['uid'] = 'foreign'
                elif fault == 'credential_changed':
                    t.connector.okx_api_key = 'replacement'
                elif fault == 'runtime_changed':
                    from types import SimpleNamespace
                    t.reader.runtime = SimpleNamespace(_healthy=True, _observation_inflight=0)
                elif fault == 'cancelled':
                    raise asyncio.CancelledError()
            return result
        t.session.request = http
        with pytest.raises((StorageError, PermissionError, asyncio.CancelledError)):
            await t.collect()
        assert not t.financial._runtime.healthy
        assert t.financial._runtime._observation_inflight == 0
        after = await t.financial.snapshot(t.cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert not t.session.sent and all(r['data'] is None for r in t.requests)
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['fee', 'trade_identity'])
async def test_original_financial_corruption_cannot_be_hidden_by_matching_provider_cash(database, fault):
    t = await reconciler(database)
    try:
        await database[0].execute('ALTER TABLE life_financial.swap_fill_facts DISABLE TRIGGER USER')
        if fault == 'fee':
            await database[0].execute("UPDATE life_financial.swap_fill_facts SET observation=jsonb_set(observation,'{signed_fee}','\"0\"')")
        else:
            await database[0].execute("UPDATE life_financial.swap_fill_facts SET trade_id='forged-trade'")
        await database[0].execute('ALTER TABLE life_financial.swap_fill_facts ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_COLLATERAL_NATIVE_UNVERIFIED'):
            await t.collect()
        assert not t.requests and not t.financial._runtime.healthy
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await t.values[5].claims(t.cfg.binding)) == 1
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'fault,reason',
    [
        ('reconciler_reader', 'PG_COLLATERAL_CONTEXT_CHANGED'),
        ('reader_source', 'PG_FINANCIAL_SOURCE_CONTEXT_CHANGED'),
        ('reader_owner', 'PG_COLLATERAL_CONTEXT_CHANGED'),
        ('reconciler_runtime', 'PG_COLLATERAL_CONTEXT_CHANGED'),
    ],
)
async def test_replacing_qualified_source_objects_cannot_forge_a_signed_collection(database, fault, reason):
    from types import SimpleNamespace
    from uuid import uuid4
    t = await reconciler(database)
    selected = t.collect.__self__
    try:
        before = await t.financial.snapshot(t.cfg.binding)
        if fault == 'reconciler_reader':
            selected.reader = SimpleNamespace(**vars(t.reader))
        elif fault == 'reconciler_runtime':
            selected.runtime = SimpleNamespace(_healthy=True)
        elif fault == 'reader_source':
            t.reader.source = SimpleNamespace(**vars(t.account), _credentials=lambda: None)
        else:
            t.reader.owner = replace(t.owner, process_id=uuid4())
            t.journal.owner = t.reader.owner
        with pytest.raises(StorageError, match=reason):
            await t.collect()
        after = await t.financial.snapshot(t.cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert not t.requests and not t.financial._runtime.healthy
    finally:
        await t.financial.close()
