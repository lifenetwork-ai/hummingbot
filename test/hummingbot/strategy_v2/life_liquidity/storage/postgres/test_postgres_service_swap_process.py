"""Whole configured SWAP process death preserves native money and one-shot IDs."""
import os
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.service_process import (
    cold_service_context,
    crash_service,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_process import monetary_rows

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint


async def joint_money(connection, account_id):
    result = await monetary_rows(connection, account_id)
    for table in ('swap_preparations', 'swap_intent_events', 'swap_boundaries', 'swap_ack_ingress',
                  'swap_ack_deliveries', 'swap_terminal_facts', 'shared_claim_releases',
                  'hedge_plans', 'hedge_plan_facts', 'hedge_settlements'):
        result[table] = await connection.fetchval(
            f"SELECT coalesce(jsonb_agg(to_jsonb(v) ORDER BY to_jsonb(v)::text),'[]'::jsonb) FROM life_financial.{table} v WHERE account_id=$1", account_id)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize('phase,expected', [
    ('capture', None), ('prepare', 'PREPARED'), ('send', 'SEND_UNKNOWN'),
    ('ack', 'ACKED'), ('terminal', 'TERMINAL'), ('settlement', 'TERMINAL'), ('release', 'TERMINAL')])
async def test_configured_swap_process_death_and_signed_cold_readback_never_restore_sender(database, monkeypatch, phase, expected):
    manifest = await crash_service(database, phase, product='SWAP')
    assert manifest['process_id'] != os.getpid() and manifest['product'] == 'SWAP'
    context = cold_service_context(manifest)
    cfg, calls = context.config, context.calls
    before = await joint_money(database[0], cfg.account_id)
    service = None
    try:
        if phase == 'send':
            with pytest.raises(StorageError, match='PG_QUOTE_POLICY_UNVERIFIED'):
                await context.reopen(True)
            assert await joint_money(database[0], cfg.account_id) == before
        service = await context.reopen(phase != 'send')
        state = await service.financial.snapshot(cfg.binding)
        assert state.balance.total_base_life == 5 and state.balance.total_quote_usdt == 10
        assert not service.journal._entries and not service._owned_executors
        assert not service.runner.executor_orchestrator.active_executors['main']
        assert (await service.controller.runner_facts()).executor_ids == ()
        assert service.hedge._sdk_child is None
        assert all(not sdk._order_tracker.active_orders for sdk in context.connectors.values())
        root = await service.hedge.repository.lookup('whole-service-hedge', cfg.binding)
        attempts = await database[0].fetch("SELECT * FROM life_financial.send_attempts WHERE account_id=$1 AND product='SWAP'", cfg.account_id)
        claims = await service.source.capital.claims(cfg.binding)
        if phase == 'capture':
            assert root is None and not attempts and not claims and not manifest['orders']
            advisory = await service.hedge.repository.lookup('hedge-source-plan:' + fingerprint(['whole-service-hedge']), cfg.binding)
            assert advisory.replayed and not advisory.trading_permission and advisory.child_operation_id is None
            count = len(calls)

            async def forbidden_cancel(*args, **kwargs):
                raise AssertionError('Interrupted original preflight entered cancellation again')
            monkeypatch.setattr(service.hedge._maker_cancellation, 'cancel_makers', forbidden_cancel)
            with pytest.raises(StorageError, match='PG_HEDGE_SOURCE_ORIGINAL_OPERATION_REQUIRES_RECONCILIATION'):
                await service.execute_hedge('whole-service-hedge')
            assert len(calls) == count
        else:
            assert root.replayed and not root.trading_permission and root.child_operation_id is not None
            attempt, = attempts
            assert attempt['state'] == expected
            assert (attempt['exchange_order_id'] is not None) == (phase not in ('prepare', 'send'))
            assert len(claims) == (0 if phase == 'release' else 1)
            assert len(manifest['orders']) == (0 if phase == 'prepare' else 1)
            if manifest['orders']:
                assert manifest['orders'][0]['clOrdId'] == attempt['wire_id']
            if phase == 'send':
                with pytest.raises(StorageError, match='PG_SERVICE_HEDGE_NOT_ENABLED'):
                    await service.execute_hedge('whole-service-hedge')
            else:
                assert (await service.execute_hedge('whole-service-hedge')).replayed
        for table, expected_count in (
                ('swap_terminal_facts', int(phase in ('terminal', 'settlement', 'release'))),
                ('hedge_settlements', int(phase in ('settlement', 'release'))),
                ('shared_claim_releases', int(phase == 'release'))):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table + ' WHERE account_id=$1', cfg.account_id) == expected_count
        if phase in ('settlement', 'release'):
            settled = await service.hedge.repository.lookup('whole-service-close', cfg.binding)
            assert settled.replayed and settled.state == 'TERMINAL' and not settled.trading_permission
        if phase == 'release':
            assert (await service.settle_hedge('whole-service-close', 'whole-service-hedge')).replayed
        assert calls and all(call['method'] == 'GET' for call in calls)
        assert await joint_money(database[0], cfg.account_id) == before
    finally:
        if service is not None:
            await service.close()
    assert all(call['method'] == 'GET' for call in calls)
    assert await joint_money(database[0], cfg.account_id) == before
