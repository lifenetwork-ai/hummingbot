"""Both-product SDK history requires original native and signed private facts."""
from dataclasses import asdict, replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_events import event
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_hedge_source import sources
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_observer import observer
from types import SimpleNamespace

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig
from hummingbot.connector.utils import TradeFillOrderDetails
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource, json_text


async def prepare_spot(database, values, prepared, *, verify_epochs=False):
    # Genuine native captured-action preparation/ARM, with synthetic admission
    # inputs. No direct INSERT of descriptors or historical monetary facts.
    import json
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_actions import action, transition

    from hummingbot.core.data_type.common import TradeType
    from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
    from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import PrepareQuoteAction, QuoteAdmissionFacts
    from hummingbot.strategy_v2.life_liquidity.storage.actions import ClaimQuoteActions
    from hummingbot.strategy_v2.life_liquidity.storage.quote_send import ArmQuoteAction
    financial, cfg, owner = values[:3]
    prepared = replace(prepared, subsidy_quote_usdt=D('0'))
    await database[0].execute("INSERT INTO life_financial.spot_action_policies VALUES($1,$2,'main',8,65536,1000)", cfg.account_id, cfg.binding.policy_hash)
    await database[0].execute("INSERT INTO life_financial.spot_quote_admission_policies VALUES($1,$2,'profit_mm',0,0,.01,.01,.01,1000,1000,1000,1000)", cfg.account_id, cfg.binding.policy_hash)
    # Preflight precedes creation of a new capture. Existing captures and
    # persisted IDs never get a renewed timestamp after an awaited read.
    initial_cut = (await financial.snapshot(cfg.binding)).cut
    at = await database[0].fetchval('SELECT clock_timestamp()')
    executor = OrderExecutorConfig(id='intent1', controller_id='main', timestamp=at.timestamp(), connector_name='okx',
                                   trading_pair='LIFE-USDT', side=TradeType.BUY, amount=D('2'), price=D('1'),
                                   level_id='0', execution_strategy=ExecutionStrategy.LIMIT_MAKER)
    row = action(owner, controller_id='main', payload_json=executor.model_dump_json())
    await financial.claim_quote_actions(ClaimQuoteActions('captured-spot', (row,)), cut=initial_cut, owner=owner)
    accepted = await transition(database, cfg, row, 'DISPATCHED', operation='accepted-spot')
    if any(m.version == 97 for m in financial._runtime._bundle):
        # Use the existing current-schema dispatch boundary, as the runner
        # does. Its locked full audit consumes the exact CLAIM successor;
        # a second Python snapshot only ages the already-issued capture.
        await financial.transition_quote_action(accepted, cut=None, owner=owner,
                                                _claim_version=initial_cut.checkpoint.revision + 1)
    else:
        await financial.transition_quote_action(accepted, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)

    async def admission():
        at = await database[0].fetchval('SELECT clock_timestamp()')
        return QuoteAdmissionFacts(cfg.binding, at, at, at, at, D('1'), D('1'), D('.99'), D('1.01'),
                                   D('0'), D('0'), D('0'), D('0'), D('0'), False, 'independent_market')
    if verify_epochs:
        for bad_epoch in (None, 2):
            with pytest.raises(StorageError):
                bad = PrepareQuoteAction(row, replace(prepared, risk_epoch=bad_epoch), await admission())
                await financial.prepare_quote_action(bad, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
            assert await financial.attempt('intent1', cfg.binding) is None
    cut = (await financial.snapshot(cfg.binding)).cut
    await financial.prepare_quote_action(PrepareQuoteAction(row, prepared, await admission()), cut=cut, owner=owner)
    wire = json.dumps(dict(clOrdId='wire1', instId='LIFE-USDT', side='buy', ordType='post_only', tdMode='cash', px='1', sz='2'), separators=(',', ':'))
    cut = (await financial.snapshot(cfg.binding)).cut
    await financial.arm_quote_action(ArmQuoteAction(prepared.operation_id, row, await admission(), wire), cut=cut, owner=owner)


async def setup(database, *, apply_finance=True, verify_epochs=False):
    template = LifeLiquidityConfig(id='main', storage_backend='postgres')
    template.strategy = type(template.strategy).model_validate(dict(
        template.strategy.model_dump(mode='json'),
        perpetual=dict(enabled=True, position_mode='HEDGE', margin_mode='cross', leverage=2)))
    registered = tuple(EventSource('main', 'life.yml', 'strategy_v2', market) for market in ('okx', 'okx_perpetual'))
    t = await sources(database, apply_batch=False, spot_fee='USDT', spot_prepare=lambda values, prepared: prepare_spot(database, values, prepared, verify_epochs=verify_epochs), spot_options=dict(
        safety_size=2, config_hash=fingerprint(template.strategy.model_dump(mode='json')),
        configuration_body=lambda binding: dict(
            controller=template.model_copy(update={'storage_binding': binding, 'recovery_account_uid': binding.account_uid}).model_dump(mode='json'),
            recorder_sources=[asdict(v) for v in registered])))
    intake = observer(t)
    controller = SimpleNamespace(config=template.model_copy(update={'storage_binding': t.cfg.binding, 'recovery_account_uid': t.cfg.binding.account_uid}))
    revoked = []
    reporting = await PgRecorderApplication.install(
        t.financial, controller=controller, owner=t.owner, sources=registered, revoke=lambda: revoked.append(True),
        local_ready=lambda: True, max_pending=4, max_callbacks=16, shutdown_timeout_s=2,
        max_rows=64, max_read_bytes=1048576)
    if apply_finance:
        await intake.observe('original-intake')
    return SimpleNamespace(**locals())


def swap_reports(s, **changes):
    at = int(s.t.journal.clock().timestamp() * 1000)
    source = s.registered[1]
    common = dict(wire='SwapWire1', exchange_id='987654321', side='SELL', leverage=2, position='OPEN')
    created = event(s.t.cfg.binding, source, at, 'SellOrderCreated', amount='1', price='1', **common)
    fee = json_text(dict(percent='0', percent_token='USDT', flat_fees=[dict(token='USDT', amount='.01')]))
    fill = event(s.t.cfg.binding, source, at + 1, 'OrderFilled', **(dict(
        common, amount='1', price='1.01', trade_id='trade1', fee_json=fee, fee_quote='.01') | changes))
    return created, fill


def spot_reports(s):
    at = int(s.t.journal.clock().timestamp() * 1000)
    source = s.registered[0]
    created = event(s.t.cfg.binding, source, at, exchange_id='123456789')
    fee = json_text(dict(percent='0', percent_token='USDT', flat_fees=[dict(token='USDT', amount='.001')]))
    fill = event(s.t.cfg.binding, source, at + 1, 'OrderFilled', exchange_id='123456789',
                 fee_json=fee, fee_quote='.001')
    return created, fill


async def close(s):
    try:
        try:
            await s.reporting.close()
        except StorageError:
            assert s.revoked
    finally:
        await s.t.financial.close()


@pytest.mark.asyncio
async def test_joint_native_history_qualifies_swap_sdk_ids_without_releasing_claims_or_restoring_tracking(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_dedup import PgJointDedupRecovery
    s = await setup(database)
    try:
        for report in swap_reports(s):
            await s.reporting.events[1].project(report, owner=s.t.owner)
        before = await s.t.financial.snapshot(s.t.cfg.binding)
        claims = await s.t.values[5].claims(s.t.cfg.binding)
        recovery = PgJointDedupRecovery(s.intake, s.reporting.events, local_idle=lambda: True)
        assert await recovery.restore() == ((0, 0), (1, 1))
        assert s.t.swap_connector._current_trade_fills == {TradeFillOrderDetails('okx_perpetual', 'trade1', 'LIFE-USDT')}
        assert s.t.swap_connector._exchange_order_ids == {'987654321': 'SwapWire1'}
        assert not s.t.connector._current_trade_fills and not s.t.connector._exchange_order_ids
        assert s.t.swap_connector.in_flight_orders == {} and s.t.connector.in_flight_orders == {}
        assert await s.t.values[5].claims(s.t.cfg.binding) == claims
        assert (await s.t.financial.snapshot(s.t.cfg.binding)).balance == before.balance
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_same_trade_id_on_spot_and_swap_retains_product_namespace_and_original_money(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_dedup import PgJointDedupRecovery
    s = await setup(database)
    try:
        for repository, reports in zip(s.reporting.events, (spot_reports(s), swap_reports(s))):
            for command in reports:
                await repository.project(command, owner=s.t.owner)
        before = await s.t.financial.snapshot(s.t.cfg.binding)
        recovery = PgJointDedupRecovery(s.intake, s.reporting.events, local_idle=lambda: True)
        assert await recovery.restore() == ((1, 1), (1, 1))
        for connector, market in zip((s.t.connector, s.t.swap_connector), ('okx', 'okx_perpetual')):
            assert connector._current_trade_fills == {TradeFillOrderDetails(market, 'trade1', 'LIFE-USDT')}
            assert connector.in_flight_orders == {}
        assert await recovery.restore() == ((1, 1), (1, 1))
        assert (await s.t.financial.snapshot(s.t.cfg.binding)).balance == before.balance
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [
    'amount', 'price', 'trade', 'fee', 'leverage', 'position', 'exchange_id',
    'credentials', 'auth', 'uid', 'foreign_cache', 'idle', 'ingress', 'expired', 'cut'])
async def test_joint_reporting_fault_cannot_publish_either_sdk_cache(database, monkeypatch, fault):
    from datetime import timedelta

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_dedup import PgJointDedupRecovery
    s = await setup(database)
    try:
        changes = ({'amount': '.5'} if fault == 'amount' else {'price': '1.02'} if fault == 'price'
                   else {'trade_id': 'foreign'} if fault == 'trade' else {'fee_quote': '.02'} if fault == 'fee'
                   else {'leverage': 3} if fault == 'leverage' else {'position': 'CLOSE'} if fault == 'position'
                   else {'exchange_id': '111111'} if fault == 'exchange_id' else {})
        created, fill = swap_reports(s, **changes)
        if fault in ('leverage', 'position', 'exchange_id'):
            with pytest.raises(StorageError):
                await s.reporting.events[1].project(fill, owner=s.t.owner)
            assert not s.t.swap_connector._current_trade_fills and not s.t.connector._current_trade_fills
            return
        for command in (created, fill):
            await s.reporting.events[1].project(command, owner=s.t.owner)
        recovery = PgJointDedupRecovery(s.intake, s.reporting.events, local_idle=lambda: fault != 'idle')
        before = await s.t.financial.snapshot(s.t.cfg.binding)
        unknown = TradeFillOrderDetails('okx', 'foreign', 'LIFE-USDT')
        if fault == 'credentials':
            s.t.swap_connector.okx_perpetual_api_key = 'foreign'
        elif fault == 'auth':
            s.t.swap_connector._auth._api_secret = 'foreign'
        elif fault == 'uid':
            s.t.config['uid'] = '999'
        elif fault == 'foreign_cache':
            s.t.connector._current_trade_fills.add(unknown)
        elif fault in ('ingress', 'expired', 'cut'):
            original = s.reporting.events[1]._read

            async def changed(*args, **kwargs):
                result = await original(*args, **kwargs)
                if fault == 'ingress':
                    s.t.financial._runtime._ingress_epoch += 1
                elif fault == 'expired':
                    clock = s.t.journal.clock
                    s.t.journal.clock = lambda: clock() + timedelta(seconds=2)
                else:
                    command = event(s.t.cfg.binding, s.registered[1], int(s.t.journal.clock().timestamp() * 1000), 'OrderCancelled',
                                    wire='SwapWire1', exchange_id='987654321')
                    await s.reporting.events[1].project(command, owner=s.t.owner)
                return result
            monkeypatch.setattr(s.reporting.events[1], '_read', changed)
        with pytest.raises(StorageError):
            await recovery.restore()
        assert not s.t.swap_connector._current_trade_fills and not s.t.swap_connector._exchange_order_ids
        assert s.t.connector._current_trade_fills == ({unknown} if fault == 'foreign_cache' else set())
        assert not s.t.connector._exchange_order_ids
        assert (await s.t.financial.snapshot(s.t.cfg.binding)).balance == before.balance
        assert not s.t.session.sent and not s.t.financial._runtime.healthy
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_second_sdk_publication_failure_rolls_back_both_original_caches(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_dedup import PgJointDedupRecovery
    s = await setup(database)
    try:
        for repository, reports in zip(s.reporting.events, (spot_reports(s), swap_reports(s))):
            for command in reports:
                await repository.project(command, owner=s.t.owner)
        original = s.t.swap_connector.add_exchange_order_ids_from_market_recorder

        def failed(value):
            original(value)
            raise StorageError('SYNTHETIC_SECOND_PUBLICATION_FAILURE')
        monkeypatch.setattr(s.t.swap_connector, 'add_exchange_order_ids_from_market_recorder', failed)
        with pytest.raises(StorageError, match='SYNTHETIC_SECOND_PUBLICATION_FAILURE'):
            await PgJointDedupRecovery(s.intake, s.reporting.events, local_idle=lambda: True).restore()
        for connector in (s.t.connector, s.t.swap_connector):
            assert not connector._current_trade_fills and not connector._exchange_order_ids
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_joint_history_refuses_unbooked_signed_facts_and_never_books_money_itself(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_dedup import PgJointDedupRecovery
    s = await setup(database, apply_finance=False)
    try:
        for command in swap_reports(s):
            await s.reporting.events[1].project(command, owner=s.t.owner)
        with pytest.raises(StorageError):
            await PgJointDedupRecovery(s.intake, s.reporting.events, local_idle=lambda: True).restore()
        assert (await s.t.financial.balance(s.t.cfg.binding)).total_quote_usdt == 10
        assert not s.t.connector._current_trade_fills and not s.t.swap_connector._current_trade_fills
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
async def test_shared_captured_quote_requires_original_claim_risk_epoch_and_never_defaults_it(database):
    s = await setup(database, verify_epochs=True)
    try:
        request = await database[0].fetchval("SELECT request->>'risk_epoch' FROM life_financial.spot_preparations WHERE intent_id='intent1'")
        assert request == '1'
        assert (await s.t.financial.balance(s.t.cfg.binding)).total_quote_usdt == D('10.889')
        assert not s.t.session.sent
    finally:
        await close(s)


@pytest.mark.asyncio
@pytest.mark.parametrize('market,wire', [('okx', 'SwapWire1'), ('okx_perpetual', 'wire1')])
async def test_native_event_origin_cannot_cross_product_scope(database, market, wire):
    s = await setup(database)
    try:
        source = next(v for v in s.registered if v.market == market)
        repository = next(v for v in s.reporting.events if v.source == source)
        command = event(s.t.cfg.binding, source, int(s.t.journal.clock().timestamp() * 1000), 'OrderCancelled',
                        wire=wire, exchange_id=None)
        with pytest.raises(StorageError, match='PG_EVENT_WIRE_INVALID'):
            await repository.project(command, owner=s.t.owner)
        assert not s.t.session.sent
    finally:
        await close(s)
