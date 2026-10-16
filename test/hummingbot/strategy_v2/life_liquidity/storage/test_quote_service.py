"""Configured quotes keep native reference/fee/subsidy and final-send proofs."""
import asyncio
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.test_service_startup import settings
from test.hummingbot.strategy_v2.life_liquidity.test_own_depth import book
from test.hummingbot.strategy_v2.life_liquidity.test_quote_reference_binding import market_engine
from unittest.mock import MagicMock

import pytest


async def quote_service(tmp_path, *, objective='profit_mm', fee_buffers=(D('0'), D('0')), risk_config=None, risk_sources=None, control_config=None, control_seed=None, inventory_config=None, settlement_policy=None, session_config=None, resume_policy=None, exit_config=None, recovery_policy=None):
    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.strategy_v2.life_liquidity.config import EconomicsConfig, QuotesConfig
    from hummingbot.strategy_v2.life_liquidity.economics import EconomicPolicy
    from hummingbot.strategy_v2.life_liquidity.fees import FeeRateSnapshot
    from hummingbot.strategy_v2.life_liquidity.market_data import InstrumentRules
    from hummingbot.strategy_v2.life_liquidity.quote_actions import QuotePlanningSnapshot
    from hummingbot.strategy_v2.life_liquidity.spot_quotes import QuoteCosts
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService, ServiceSources
    from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig, QuoteServiceConfig
    from hummingbot.strategy_v2.life_liquidity.storage.spot_application import SpotAdmission
    cfg = await settings(tmp_path)
    if session_config is not None:
        from pathlib import Path

        from hummingbot.strategy_v2.life_liquidity.session import SessionStore
        strategy = cfg.controller.strategy.model_copy(update={'session': session_config})
        cfg = cfg.model_copy(update={'controller': cfg.controller.model_copy(update={'strategy': strategy})})
        sessions = SessionStore(Path(cfg.state_dir) / 'session.json')
        journal = sessions.load()
        sessions.save(replace(journal, on_expiry=session_config.on_expiry,
                              successor_duration_seconds=session_config.successor_duration_seconds))
    economics = EconomicsConfig(objective=objective, min_net_edge_bps='0', uncertainty_buffer_bps='0', fee_policy='pause', fee_max_age='1s',
                                subsidy_budget_quote={'campaign': '10', 'day': '5', 'session': '2'} if objective == 'liquidity_service' else None)
    strategy = cfg.controller.strategy.model_copy(update={'economics': economics,
                                                          'quotes': QuotesConfig(spreads_bps=('30',), sizes_base=('1',))})
    if resume_policy is not None:
        strategy = strategy.model_copy(update={'risk': strategy.risk.model_copy(update={'resume_policy': resume_policy})})
    binding = replace(cfg.controller.storage_binding, config_hash=fingerprint(strategy.model_dump(mode='json')))
    controller = cfg.controller.model_copy(update={'strategy': strategy, 'storage_binding': binding,
                                                   'own_depth_max_observation_skew_ms': 100, 'own_depth_max_book_age_ms': 500,
                                                   'own_depth_max_distance_bps': D('200')})
    values = {key: getattr(cfg, key) for key in FileServiceConfig.model_fields}
    values.update(controller=controller, quotes=QuoteServiceConfig(reference_policy=market_engine().policy,
                                                                   life_anchor_usdt=D('1'), model_version='test-v1', fee_group_id='1', max_actions_per_tick=1,
                                                                   fee_buffer_quote_usdt=fee_buffers[0], fee_buffer_base_life=fee_buffers[1]))
    values['risk'] = risk_config
    values['control'] = control_config
    values['exit'] = exit_config
    values['inventory'] = inventory_config
    values['settlement_policy'] = settlement_policy
    if recovery_policy is not None:
        values['recovery_policy'] = recovery_policy
    if inventory_config is not None:
        from hummingbot.strategy_v2.life_liquidity.storage.settlement import SpotSettlementPolicy
        values['settlement_policy'] = SpotSettlementPolicy(1000)
    cfg = FileServiceConfig.model_validate(values)
    connector = OkxExchange('', '', '', trading_pairs=['LIFE-USDT'], trading_required=False)

    async def uid():
        return binding.account_uid
    connector.get_account_uid = uid
    market = book(bids=[['0.98', '20']], asks=[['1.02', '20']])
    state = {'snapshot': QuotePlanningSnapshot(str(cfg.owner.session_id), cfg.owner.epoch, 1, 2.0, 2.5,
                                               True, True, True, D('1'), D('1'), market.bid, market.ask,
                                               InstrumentRules(D('.01'), D('.1'), D('.1')), QuoteCosts(*([D('0')] * 6)),
                                               EconomicPolicy(objective, D('0')), subsidy_remaining_quote=D('2') if objective == 'liquidity_service' else None, book_sequence_id=42, reference_model_version='test-v1')}
    if objective == 'liquidity_service':
        state['snapshot'] = replace(state['snapshot'], qualified_exit_value_usdt=D('.98'))
    state['fee'] = FeeRateSnapshot(binding.account_uid, 'okx', 'SPOT', 'LIFE-USDT', '1', 'USDT', None, 1000, D('0'), D('0'))
    source = ServiceSources(clock=lambda: cfg.owner.valid_from, risk_epoch=lambda: 1, authorize_permit=lambda _: True,
                            spot_admission=lambda _: SpotAdmission(D('1'), D('99'), D('0'), D('0')),
                            quote_snapshot=lambda: state['snapshot'], fee_snapshot=lambda: state['fee'], monotonic_clock=lambda: 2.05)
    source = replace(source, control_seed=control_seed)
    if risk_sources is not None:
        source = replace(source, **risk_sources)
    provider = MagicMock()
    provider.get_connector_with_fallback.return_value = connector
    service = await FileLiquidityService.open(cfg, connectors={'okx': connector}, sources=source,
                                              market_data_provider=provider, create_binding=True)
    c = service.controller
    # Explicit synthetic market observations; native reference/fee/journal checks stay active.
    c.listing_gate.instrument_found, c.listing_gate.instrument_state = True, 'live'
    c.listing_gate.instrument_rules = state['snapshot'].rules
    c.continuous_gate.ready, c.continuous_gate.exchange_time_ms = True, 1100
    c.snapshot_gate.clock, c.snapshot_gate.ready, c.snapshot_gate.snapshot = lambda: 2.05, True, market
    c.trading_permissions_ready = lambda: True
    c.allow_create_executor_actions = lambda: True
    service.runner.listen_to_executor_actions_task.cancel()
    await asyncio.gather(service.runner.listen_to_executor_actions_task, return_exceptions=True)
    return service, state


@pytest.mark.asyncio
async def test_configured_planner_issues_timestamped_actions_and_reject_releases_slot_on_worker(tmp_path):
    service, state = await quote_service(tmp_path)
    try:
        actions = await service.plan()
        assert len(actions) == 1, service.controller._quote_action_planner.reason_code
        assert actions[0].executor_config.timestamp == service.sources.clock().timestamp()
        assert service.store._wal.all_records() == ()
        service.rejected(actions[0])
        await service.drain_action_events()
        planner = service.controller._quote_action_planner
        record = await service.store._call(planner.action_journal.verified_get, actions[0].executor_config.id)
        assert record.state == 'REJECTED'
        assert len(await service.plan()) == 1
        assert state['snapshot'].policy.objective == 'profit_mm'
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['fee', 'book', 'rules', 'edge', 'uncertainty', 'stale', 'depth'])
async def test_configured_quote_sources_cannot_bypass_native_proofs(tmp_path, fault):
    service, state = await quote_service(tmp_path)
    try:
        if fault == 'fee':
            state['fee'] = replace(state['fee'], account_id='other')
        elif fault == 'book':
            state['snapshot'] = replace(state['snapshot'], book_sequence_id=43)
        elif fault == 'rules':
            state['snapshot'] = replace(state['snapshot'], rules=replace(state['snapshot'].rules, min_size=D('2')))
        elif fault == 'edge':
            state['snapshot'] = replace(state['snapshot'], policy=replace(state['snapshot'].policy, min_net_edge_bps=D('10')))
        elif fault == 'uncertainty':
            state['snapshot'] = replace(state['snapshot'], costs=replace(state['snapshot'].costs, uncertainty_bps=D('1')))
        elif fault == 'stale':
            state['snapshot'] = replace(state['snapshot'], expires_monotonic=2.01)
        else:
            service.controller.snapshot_gate.snapshot = replace(service.controller.snapshot_gate.snapshot,
                                                                bid_depth=((D('.98'), D('1')),), ask_depth=((D('1.02'), D('1')),))
        assert await service.plan() == []
        assert service.store._wal.all_records() == ()
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['none', 'book', 'fee', 'stop', 'risk', 'config_invalid', 'config_kill', 'config_sql'])
@pytest.mark.parametrize('objective', ['profit_mm', 'liquidity_service'])
async def test_configured_quote_actual_runner_rest_reprices_after_throttle(tmp_path, change, objective, inventory_side=None, recovery_probe=False, markout_bootstrap=False, exit_route=False):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hummingbot.connector.exchange.okx import okx_constants
    from hummingbot.connector.trading_rule import TradingRule
    from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
    from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
    risk_state, extra = None, {}
    if exit_route:
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_exit_service import exit_service
        service, risk_state, state = await exit_service(tmp_path, objective=objective)
        await service.request_exit('exit-rest-request')
    elif markout_bootstrap:
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_markout_probe_service import markout_service
        service, risk_state, state = await markout_service(tmp_path, objective=objective)
        assert not await service.store._call(service.risk.ready)
        assert await service.store._call(service.risk.ready)
    elif recovery_probe:
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_probe_service import degraded, probe_service
        service, risk_state, state = await probe_service(tmp_path, objective=objective)
        await degraded(service, risk_state)
    elif inventory_side is not None:
        from test.hummingbot.strategy_v2.life_liquidity.storage.test_inventory_service import (
            activate_inventory,
            inventory_service,
        )
        service, risk_state, state, signer = await inventory_service(tmp_path, side=inventory_side)
        await activate_inventory(service, signer)
    else:
        if change == 'risk':
            from test.hummingbot.strategy_v2.life_liquidity.storage.test_risk_service import policy, sources
            risk_state, callbacks = sources()
            extra = dict(risk_config=policy(), risk_sources=callbacks)
        service, state = await quote_service(tmp_path, objective=objective, **extra)
        if change == 'risk':
            assert not await service.store._call(service.risk.ready)
            assert await service.store._call(service.risk.ready)
    connector = service.connectors['okx']
    entered, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    sent, failures = [], []

    class Throttle:
        @asynccontextmanager
        async def execute_task(self, limit_id):
            if limit_id == okx_constants.OKX_PLACE_ORDER_PATH:
                entered.set()
                await release.wait()
            yield

    class Response:
        status, content_type = 200, 'application/json'

        async def json(self):
            return {'data': [{'sCode': '0', 'ordId': '101'}]}

    class Session:
        async def request(self, **kwargs):
            sent.append(kwargs)
            return Response()
    assistant = RESTAssistant(connection=RESTConnection(Session()), throttler=Throttle())
    connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=AsyncMock(return_value=assistant))
    connector._api_request_url = AsyncMock(return_value='https://www.okx.com/api/v5/trade/order')
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value='LIFE-USDT')
    connector.get_price_by_type = lambda *args, **kwargs: D('1')
    connector._account_balances = connector._account_available_balances = {'USDT': D('10'), 'LIFE': D('5')}
    connector._trading_rules['LIFE-USDT'] = TradingRule('LIFE-USDT', min_order_size=D('.1'),
                                                        min_price_increment=D('.01'), min_base_amount_increment=D('.1'))
    connector._set_current_timestamp(service.sources.clock().timestamp())
    service.runner._set_current_timestamp(service.sources.clock().timestamp())
    create = connector._create_order

    async def capture(**kwargs):
        try:
            await create(**kwargs)
        except Exception as exc:
            failures.append(exc)
        finally:
            completed.set()
    connector._create_order = capture
    try:
        actions = await service.plan()
        assert len(actions) == 1
        config = actions[0].executor_config
        if recovery_probe:
            assert config.amount == D('.1') and service.risk.gate.state == 'DEGRADED'
        service.runner._async_action_dispatcher.submit(actions)
        await service.runner._async_action_dispatcher.drain()
        await asyncio.wait_for(entered.wait(), 3)
        assert (await service.store.attempt(config.id, service.store.binding)).state.value == 'SEND_UNKNOWN'
        if change == 'book':
            service.controller.snapshot_gate.snapshot = replace(service.controller.snapshot_gate.snapshot, sequence_id=43)
        elif change == 'fee':
            state['fee'] = replace(state['fee'], maker_cost_rate=D('.01'))
        elif change == 'stop':
            service.spot.cancel_local(config.id)
        elif change == 'risk':
            risk_state['risk'] = replace(risk_state['risk'], latency_ok=False)
        elif change == 'config_invalid':
            await service.reload_yaml(tmp_path / 'missing.yml', operation_id='throttle-invalid')
        elif change == 'config_kill':
            config_update = service.config.model_copy(update={'controller': service.controller.config.model_copy(update={'manual_kill_switch': True})})
            await service.reload_config(config_update, operation_id='throttle-kill')
        elif change == 'config_sql':
            from hummingbot.model.controllers import Controllers

            def corrupt():
                repo = service.reporting.repository
                with repo._recorder.sql_manager.get_new_session() as sql:
                    row = sql.query(Controllers).filter(Controllers.id == repo._configuration_sql_id()).one()
                    row.config = {'unsigned': True}
                    sql.commit()
            await service.store._call(corrupt)
        elif change == 'halt':
            await service.store._call(service.risk.gate.halt, 'OPERATOR_STOP')
        elif change == 'journal':
            path = service.risk.markout_probe.path if markout_bootstrap else service.risk.probe.journal.path
            await service.store._call(path.unlink)
        elif change == 'exit_price':
            risk_state['exit'] = replace(risk_state['exit'], limit_price_usdt=D('1.01'))
        elif change == 'normal':
            from datetime import timedelta
            risk_state['now'] += timedelta(milliseconds=100)
            risk_state['mono'] += .1
            risk_state['risk'] = replace(risk_state['risk'], observed_monotonic_ms=2250)
        release.set()
        await asyncio.wait_for(completed.wait(), 3)
        await service.reporting.drain()
        attempt = await service.store.attempt(config.id, service.store.binding)
        view = await service.store.snapshot(service.store.binding)
        assert view.balance.reserved_quote_usdt == (config.price * config.amount if config.side.name == 'BUY' else 0)
        assert view.balance.total_quote_usdt == D('10')
        # Actual economic cost, not the source callback's arbitrary 99 USDT.
        assert view.budget.session_subsidy_committed_usdt == (D('.01') * config.amount if objective == 'liquidity_service' and not exit_route else 0)
        if exit_route:
            assert await service.store._call(lambda: service.exit.budget.reserved_for(config.id, session_id=str(service.spot.owner.session_id))) == D('.01')
        if change == 'none':
            assert not failures and len(sent) == 1
            assert attempt.state.value == 'ACKED'
            record = await service.store._call(service.quotes.native.action_journal.verified_get, config.id)
            assert record.state == 'DISPATCHED'
        else:
            # The actual connector reports a failed create through its event
            # path; it may consume PermissionError internally. It must issue no HTTP.
            assert sent == [] and attempt.state.value == 'SEND_UNKNOWN'
    finally:
        release.set()
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['missing_journal', 'anchor', 'group'])
async def test_configured_quote_cold_start_rejects_deleted_history_or_changed_policy(tmp_path, change):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    from hummingbot.strategy_v2.life_liquidity.storage.service import FileLiquidityService
    from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig
    service, _ = await quote_service(tmp_path)
    config, sources, connectors, provider = service.config, service.sources, service.connectors, service.controller.market_data_provider
    await service.close()
    values = {key: getattr(config, key) for key in FileServiceConfig.model_fields}
    if change == 'missing_journal':
        (tmp_path / 'state' / 'quote_actions.json').unlink()
    else:
        values['quotes'] = config.quotes.model_copy(update={'life_anchor_usdt': D('1.1')} if change == 'anchor' else {'fee_group_id': '2'})
    config = FileServiceConfig.model_validate(values)
    with pytest.raises((StorageError, ValueError)):
        cold = await FileLiquidityService.open(config, connectors=connectors, sources=sources,
                                               market_data_provider=provider, create_binding=False)
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('adequate', [False, True])
async def test_configured_positive_maker_fee_requires_buffers_in_both_possible_fee_currencies(tmp_path, adequate):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    buffers = (D('.01'), D('.01')) if adequate else (D('0'), D('0'))
    service, state = await quote_service(tmp_path, fee_buffers=buffers)
    try:
        state['fee'] = replace(state['fee'], maker_cost_rate=D('.001'))
        state['snapshot'] = replace(state['snapshot'], costs=replace(state['snapshot'].costs, maker_fee_rate=D('.001')))
        config = (await service.plan())[0].executor_config
        if adequate:
            terms = await service.store._call(service.quotes.admission, config)
            assert terms.fee_buffer_quote_usdt == terms.fee_buffer_base_life == D('.01')
        else:
            with pytest.raises(StorageError, match='SERVICE_QUOTE_FEE_BUFFER_INSUFFICIENT'):
                await service.store._call(service.quotes.admission, config)
        assert service.store._wal.all_records() == ()
        assert (await service.store.snapshot(service.store.binding)).balance.reserved_quote_usdt == 0
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_quote_service_stop_cannot_release_physical_account_lock_before_worker_drains(tmp_path):
    import threading

    from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable, AccountRiskPoolLock
    service, _ = await quote_service(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def blocked():
        entered.set()
        assert release.wait(5)
    running = asyncio.create_task(service.store._call(blocked))
    await asyncio.to_thread(entered.wait, 2)
    closing = asyncio.create_task(service.close())
    try:
        await asyncio.sleep(.05)
        assert not closing.done()
        assert service.store._account_lock._fd is not None
        competing = AccountRiskPoolLock(service.store.binding.account_uid, service.config.account_lock_root)
        with pytest.raises(AccountLockUnavailable):
            await asyncio.to_thread(competing.acquire)
    finally:
        release.set()
        await running
        await closing
    await asyncio.to_thread(competing.acquire)
    competing.release()
