"""Actual whole-service death and cold native runner, fake HTTP/disposable PG."""
import os
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal as D
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.service_process import crash_service, decoded_config
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service import limits
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_shared_sdk_quota import sdk_pair

import pytest

from controllers.generic.life_liquidity import LifeLiquidityConfig
from hummingbot.core.data_type.common import PositionMode
from hummingbot.data_feed.market_data_provider import MarketDataProvider
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.service import PgLiquidityService
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


async def monetary_rows(connection, account_id):
    # Fixed schema identifiers only. Independently compare every retained hold,
    # money bucket and original durable action; reporting recovery may append
    # diagnostic operations, but cannot refund or replace this financial state.
    tables = ('balances', 'budget_buckets', 'budget_events', 'reservations', 'send_attempts',
              'shared_capital_claims', 'quote_actions', 'spot_action_descriptors', 'spot_action_events')
    return {table: await connection.fetchval(
        f"SELECT coalesce(jsonb_agg(to_jsonb(v) ORDER BY to_jsonb(v)::text),'[]'::jsonb) FROM life_financial.{table} v WHERE account_id=$1",
        account_id) for table in tables}


@pytest.mark.asyncio
@pytest.mark.parametrize('phase,expected', [('capture', None), ('prepare', 'PREPARED'), ('send', 'SEND_UNKNOWN'), ('ack', 'ACKED')])
async def test_configured_service_process_death_retains_original_holds_without_cold_replay(database, phase, expected):
    manifest = await crash_service(database, phase)
    assert manifest['process_id'] != os.getpid() and manifest['phase'] == phase
    cfg = decoded_config(manifest['config'])
    before = await monetary_rows(database[0], cfg.account_id)
    spot, swap, http = sdk_pair()
    spot.enable_protected_trading_pair('LIFE-USDT')
    PgSharedSdkQuota.install(spot=spot, swap=swap)
    connectors = dict(okx=spot, okx_perpetual=swap)
    calls = []

    swap._contract_sizes['LIFE-USDT'] = D('.25')
    swap._perpetual_trading.set_position_mode(PositionMode.HEDGE)
    swap._perpetual_trading.set_leverage('LIFE-USDT', 2)

    async def original_exchange(**kwargs):
        calls.append(kwargs)
        assert kwargs['method'] == 'GET', 'Cold recovery attempted a new wire'
        assert kwargs['headers'].get('OK-ACCESS-SIGN')
        tail, params = kwargs['url'].rsplit('/', 1)[1], kwargs.get('params') or {}
        rows = {'config': [manifest['account_config']], 'balance': [manifest['balance']],
                'positions': manifest['positions'], 'bills-archive': [dict(billId='100')],
                'fills-history': [], 'orders-algo-pending': []}
        if tail in ('order', 'orders-pending'):
            data = manifest['orders']
            for key in ('clOrdId', 'instId', 'instType'):
                if params.get(key):
                    data = [row for row in data if row[key] == params[key]]
        else:
            data = rows[tail]
        response = Response()

        async def body():
            return dict(code='0', data=deepcopy(data))
        response.json = body
        return response
    http.request = original_exchange
    service = None

    async def reopen(native_trading):
        return await PgLiquidityService.open(
            LifeLiquidityConfig.model_validate_json(manifest['controller']), runtime_config=cfg,
            owner=OwnerToken.from_json(manifest['owner']), connectors=connectors,
            market_data_provider=MarketDataProvider(connectors),
            sources=tuple(EventSource(**s) for s in manifest['sources']), limits=limits(),
            clock=lambda: datetime.now(timezone.utc), independent_fill_value=lambda *args: None,
            independent_cashflow_value=lambda *args: None, native_trading=native_trading)

    try:
        if phase == 'send':
            # A physical send with no received ACK is unresolved native risk.
            # Cold readback cannot initialize another quote-capable source.
            with pytest.raises(StorageError, match='PG_QUOTE_POLICY_UNVERIFIED'):
                await reopen(True)
            assert await monetary_rows(database[0], cfg.account_id) == before
        service = await reopen(phase != 'send')
        state = await service.financial.snapshot(cfg.binding)
        assert state.balance.total_base_life == 5 and state.balance.total_quote_usdt == 10
        stored, = await service.financial.quote_actions(cfg.binding)
        assert stored.action.intent_id == 'whole-service-original'
        assert stored.state == ('PROPOSED' if phase == 'capture' else 'PREPARED')
        attempt = await service.financial.attempt(stored.action.intent_id, cfg.binding)
        if expected is None:
            assert attempt is None
            assert state.balance.reserved_base_life == state.balance.reserved_quote_usdt == 0
        else:
            assert attempt.state.value == expected
            assert state.balance.reserved_quote_usdt > 0
            assert (attempt.exchange_order_id is not None) == (phase == 'ack')
        assert len(manifest['orders']) == (1 if phase in ('send', 'ack') else 0)
        if manifest['orders']:
            assert manifest['orders'][0]['clOrdId'] == attempt.client_order_id
        assert len(await service.source.capital.claims(cfg.binding)) == 1
        assert service.events._registered
        assert (await service.controller.runner_facts()).executor_ids == ()
        assert not service.runner.executor_orchestrator.active_executors['main']
        assert service.journal._entries == {} and service._owned_executors == {}
        action = CreateExecutorAction(controller_id='main', executor_config=OrderExecutorConfig.model_validate_json(stored.action.payload_json))
        assert not await service.reporting.before_dispatch(action)
        assert not await service.journal.async_authorize_runner_create_action(action)
        assert not service.controller.local_runner_create_fence(action)
        if phase == 'send':
            with pytest.raises(ValueError, match='PG_RUNNER_TRADING_ASSEMBLY_REQUIRED'):
                service.runner.executor_orchestrator.execute_action(action)
            from hummingbot.core.data_type.common import TradeType
            with pytest.raises(StorageError, match='PG_SERVICE_TRADING_NOT_QUALIFIED'):
                await service.quote(stored.action.intent_id, TradeType.BUY, 0)
        else:
            with pytest.raises(StorageError, match='DISPATCH_ORIGIN'):
                service.runner.executor_orchestrator.execute_action(action)
        with pytest.raises(StorageError, match='CLAIM_REPLAY_REQUIRES_RECOVERY'):
            await service.journal.preflight_publication('service-publish:' + fingerprint(['whole-service-original']))
        assert calls and all(call['method'] == 'GET' for call in calls)
        assert await monetary_rows(database[0], cfg.account_id) == before
    finally:
        if service is not None:
            await service.close()
    assert all(call['method'] == 'GET' for call in calls)
    assert await monetary_rows(database[0], cfg.account_id) == before
