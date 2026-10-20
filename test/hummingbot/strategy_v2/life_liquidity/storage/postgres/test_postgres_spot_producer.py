"""Configured native quote/cost production; only disposable DB and fake HTTP."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_market import market

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_producer import PgSpotQuoteProducer


def policy():
    # Explicit synthetic calibration. None of these values is a live default.
    return dict(controller_id='main', config_version=1, levels=[dict(spread_bps='300', quantity_base='1')],
                carry_cost_bps='1', inventory_risk_bps='2', max_subsidy_per_quote_usdt='0.1')


async def producer(database, body=None, objective='profit_mm'):
    config = policy() if body is None else body
    t = await market(database, clean=True, objective=objective, configuration_body=lambda _: dict(spot_quote_producer=config))
    source = await PgQuoteAccountSource.create(
        t.reader.source.journal, connector=t.connector, market_source=t.market_source,
        max_uid_age_ms=1000, max_auth_age_ms=1000, max_clock_skew_ms=100,
        max_pending=1, collection_timeout_ms=1000)
    return t, source


@pytest.mark.asyncio
@pytest.mark.parametrize('side,price,inventory', [('BUY', '.97', '.0002'), ('SELL', '1.03', '0')])
async def test_original_signed_market_and_imported_levels_produce_price_fees_and_costs(database, side, price, inventory):
    t, source = await producer(database)
    try:
        p = await PgSpotQuoteProducer.create(source)
        before = await t.financial.balance(t.cfg.binding)
        intent = await p.propose('new-native', TradeType[side], 0)
        assert intent.price_usdt == D(price) and intent.quantity_base == 1
        costs = p.assess(t.market_source._capture, intent)
        assert costs.carry_cost_usdt == D('.0001')
        assert costs.inventory_risk_usdt == D(inventory)
        assert costs.fee_buffer_quote_usdt == intent.price_usdt * D('.001')
        assert costs.fee_buffer_base_life == D('.001') and costs.subsidy_quote_usdt == 0
        assert (await t.financial.balance(t.cfg.binding)) == before
        assert not await t.source.capital.claims(t.cfg.binding) and not source._intents
        assert not [r for r in t.requests if r['method'] == 'POST']
        with pytest.raises(StorageError, match='INTENT_USED'):
            await p.propose('new-native', TradeType[side], 0)
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing', 'extra', 'float', 'negative', 'version', 'controller', 'too_many'])
async def test_missing_or_unbounded_imported_producer_configuration_cannot_grant_a_quote(database, fault):
    body = policy()
    if fault == 'missing':
        del body['carry_cost_bps']
    elif fault == 'extra':
        body['unknown'] = '0'
    elif fault == 'float':
        body['inventory_risk_bps'] = .1
    elif fault == 'negative':
        body['max_subsidy_per_quote_usdt'] = '-1'
    elif fault == 'version':
        body['config_version'] = 2
    elif fault == 'controller':
        body['controller_id'] = 'foreign'
    else:
        body['levels'] *= 65
    t, source = await producer(database, body)
    try:
        with pytest.raises(StorageError, match='PRODUCER'):
            await PgSpotQuoteProducer.create(source)
        assert not t.requests and not source._intents
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_changed_primary_producer_configuration_blocks_proposals_without_new_source_reads(database):
    t, source = await producer(database)
    try:
        p = await PgSpotQuoteProducer.create(source)
        # Explicit migrator corruption injection; ordinary UPDATE is refused.
        await database[0].execute('ALTER TABLE life_financial.configurations DISABLE TRIGGER USER')
        await database[0].execute("UPDATE life_financial.configurations SET body=jsonb_set(body,'{spot_quote_producer,carry_cost_bps}','\"5\"')")
        await database[0].execute('ALTER TABLE life_financial.configurations ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='CONFIGURATION_CHANGED'):
            await p.propose('changed', TradeType.BUY, 0)
        assert not t.requests and not source._intents
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_copied_market_capture_cannot_become_cost_or_proposal_authority(database):
    t, source = await producer(database)
    try:
        p = await PgSpotQuoteProducer.create(source)
        intent = await p.propose('native', TradeType.BUY, 0)
        with pytest.raises(StorageError, match='CAPTURE_CHANGED'):
            p.assess(deepcopy(t.market_source._capture), intent)
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['copy_intent', 'stale_capture', 'changed_model', 'stopped'])
async def test_producer_refuses_substituted_stale_or_revoked_inputs_without_allocating(database, fault):
    import asyncio
    t, source = await producer(database)
    try:
        p = await PgSpotQuoteProducer.create(source)
        intent = await p.propose('native', TradeType.BUY, 0)
        capture = t.market_source._capture
        if fault == 'copy_intent':
            intent = replace(intent)
        elif fault == 'stale_capture':
            await asyncio.sleep(1.01)
        elif fault == 'changed_model':
            p._carry = D('0')
        else:
            p.stop()
        with pytest.raises(StorageError):
            p.assess(capture, intent)
        assert not source._intents and not await t.source.capital.claims(t.cfg.binding)
        assert not [r for r in t.requests if r['method'] == 'POST']
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('objective', ['profit_mm', 'liquidity_service'])
async def test_economic_denial_consumes_local_proposal_id_without_money_or_capture(database, objective):
    body = policy()
    body['levels'][0]['spread_bps'] = '100'
    body['max_subsidy_per_quote_usdt'] = '0'
    t, source = await producer(database, body, objective)
    try:
        p = await PgSpotQuoteProducer.create(source)
        with pytest.raises(StorageError, match='ECONOMICS_DENIED'):
            await p.propose('denied', TradeType.BUY, 0)
        assert 'denied' not in p._proposals
        with pytest.raises(StorageError, match='INTENT_USED'):
            await p.propose('denied', TradeType.BUY, 0)
        assert not source._intents and not await t.source.capital.claims(t.cfg.binding)
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_potential_rebate_is_not_precredited_in_produced_fee_holds(database):
    t, source = await producer(database)
    t.spot['trade-fee'][0]['feeGroup'][0]['maker'] = '.001'
    try:
        p = await PgSpotQuoteProducer.create(source)
        intent = await p.propose('rebate', TradeType.BUY, 0)
        costs = p.assess(t.market_source._capture, intent)
        assert costs.fee_buffer_base_life == costs.fee_buffer_quote_usdt == 0
        assert not source._intents and not await t.source.capital.claims(t.cfg.binding)
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_post_proposal_source_capture_must_keep_the_original_imported_model_hash(database):
    t, source = await producer(database)
    try:
        p = await PgSpotQuoteProducer.create(source)
        intent = await p.propose('original', TradeType.BUY, 0)
        await database[0].execute('ALTER TABLE life_financial.configurations DISABLE TRIGGER USER')
        await database[0].execute("UPDATE life_financial.configurations SET body=jsonb_set(body,'{spot_quote_producer,carry_cost_bps}','\"5\"')")
        await database[0].execute('ALTER TABLE life_financial.configurations ENABLE TRIGGER USER')
        capture = await t.market_source.collect()
        with pytest.raises(StorageError, match='CONFIGURATION_CHANGED'):
            p.assess(capture, intent)
        assert not source._intents and not await t.source.capital.claims(t.cfg.binding)
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_unbound_producer_cannot_enter_post_dispatch_preparation(database):
    t, source = await producer(database)
    try:
        p = await PgSpotQuoteProducer.create(source)
        with pytest.raises(StorageError, match='PRODUCER_REQUIRED'):
            await t.market_source.prepare_produced(None, 'foreign', p)
        assert not t.requests and not source._intents
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_native_scale_holds_round_up_and_are_accepted_by_actual_money_domains(database):
    from decimal import localcontext
    body = policy()
    body['levels'][0]['quantity_base'] = '1.12'
    body['carry_cost_bps'] = '0.1234567890123456789'
    body['inventory_risk_bps'] = '0.000000000000000001'
    t, source = await producer(database, body)
    t.spot['trade-fee'][0]['feeGroup'][0]['maker'] = '-0.000123456789012345678'
    try:
        p = await PgSpotQuoteProducer.create(source)
        intent = await p.propose('fractional', TradeType.BUY, 0)
        costs = p.assess(t.market_source._capture, intent)
        with localcontext() as ctx:
            ctx.prec = 160
            raw = intent.quantity_base * D(body['carry_cost_bps']) / 10000
            assert raw <= costs.carry_cost_usdt < raw + D('1e-18')
        assert costs.inventory_risk_usdt == D('1e-18')
        for value in costs.__dict__.values():
            assert await database[0].fetchval('SELECT $1::text::life_financial.amount', str(value)) == value
        assert not source._intents and not await t.source.capital.claims(t.cfg.binding)
    finally:
        await t.financial.close()


@pytest.mark.asyncio
async def test_native_scale_roundup_cannot_exceed_the_imported_per_quote_subsidy_ceiling(database):
    body = policy()
    body['levels'][0]['spread_bps'] = '100'
    body.update(carry_cost_bps='0', inventory_risk_bps='0', max_subsidy_per_quote_usdt='0.000000000000000000995')
    t, source = await producer(database, body, 'liquidity_service')
    t.spot['trade-fee'][0]['feeGroup'][0].update(maker='-0.000000000000000001', taker='0')
    try:
        p = await PgSpotQuoteProducer.create(source)
        with pytest.raises(StorageError, match='ECONOMICS_DENIED'):
            await p.propose('subquantum', TradeType.BUY, 0)
        assert 'subquantum' not in p._proposals
        assert not source._intents and not await t.source.capital.claims(t.cfg.binding)
    finally:
        await t.financial.close()
