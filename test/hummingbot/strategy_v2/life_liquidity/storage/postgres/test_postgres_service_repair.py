"""Configured financial failure -> separately signed repair -> fresh SDK startup."""
import asyncio
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_process import exit_joint_repair
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.service_process import (
    cold_service_context,
    encoded_config,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_fills import partial_spot_fill
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import asyncpg
import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import PgJointBatchRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import (
    JointRepairFacts,
    PgJointRepairRepository,
    ResolveJointSources,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_approval import PgJointRepairApprovalImporter
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


@pytest.mark.asyncio
@pytest.mark.parametrize('commit_fault', [None, 'before', 'after', 'process_before', 'process_after'])
async def test_configured_pending_fill_requires_signed_repair_and_never_restores_sender(database, monkeypatch, commit_fault):
    # The private signing key belongs only to this synthetic operator. The
    # runtime configuration receives its public verifier before initial import.
    keys = OperatorKeys({'operator': b'r' * 32}, {'operator': frozenset(('RECOVER',))})
    values = {}
    t, service = await configured(
        database, independent_fill_value=lambda product, trade, at: values[(product, trade)],
        configuration_extra={'operator_verifier': keys.verifier().policy()},
        spot_overrides=dict(owner_duration=timedelta(seconds=300), session_duration=timedelta(seconds=360)))
    await database[0].execute('INSERT INTO life_financial.joint_repair_policies VALUES($1,$2,64,64,1048576,5000)',
                              t.cfg.account_id, t.cfg.binding.policy_hash)
    captured, stream, financial, cold, closed = [], None, None, None, False
    severed_runtime = None

    async def close_financial():
        try:
            await financial.close()
        except StorageUnavailable as error:
            if financial._runtime is not severed_runtime or str(error) != 'PG_SHUTDOWN_READBACK_REQUIRED':
                raise
            # A physically killed COMMIT may require bounded protocol teardown.
            # It must terminate every pool and explicitly require readback.
            assert severed_runtime._closing and not severed_runtime.healthy
            assert not severed_runtime._tasks and not any(severed_runtime._counts.values())
            assert severed_runtime._close_task.done()
            assert all(pool._closed for pool in severed_runtime._pools.values())
    apply = PgJointBatchRepository.apply

    async def wrong_source(batch, command, **kwargs):
        if batch is service.events.observer.batch and command.spot_fills and not captured:
            captured.append(command)
            bundle = command.funding.bundle
            command = replace(command, funding=replace(command.funding, bundle=replace(
                bundle, account=replace(bundle.account, snapshot=replace(
                    bundle.account.snapshot, spot_cash_quote=bundle.account.snapshot.spot_cash_quote + 1)))))
        return await apply(batch, command, **kwargs)
    monkeypatch.setattr(PgJointBatchRepository, 'apply', wrong_source)
    try:
        f = await partial_spot_fill(t, service, values, TradeType.BUY, 'USDT')
        stream = f.stream
        with pytest.raises(StorageError, match='PG_JOINT_OBSERVER_RECOVERY_REQUIRED'):
            await service.events.drain()
        original, = captured
        assert (await service.financial.balance(t.cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_fill_facts') == 0
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await service.source.capital.claims(t.cfg.binding)) == 1
        manifest = dict(config=encoded_config(t.cfg), owner=t.owner.json(), controller=service.config.model_dump_json(),
                        sources=[asdict(r.source) for r in service.reporting.events], account_config=t.config,
                        balance=t.balance, positions=t.positions, orders=t.service_orders,
                        bills=[*f.bills, dict(billId='100')], fills={'SPOT': [f.fill], 'SWAP': []})
        try:
            await service.close()
        except StorageError as error:
            assert str(error) in ('PG_JOINT_OBSERVER_RECOVERY_REQUIRED', 'RECORDER_APPLICATION_UNAVAILABLE')
        closed = True
        stream.cancel()
        await asyncio.gather(stream, return_exceptions=True)
        context = cold_service_context(manifest, independent_fill_value=lambda product, trade, at: values[(product, trade)])
        with pytest.raises(StorageError, match='PG_COLLATERAL_PENDING_RESOLUTION_REQUIRED'):
            await context.reopen(False)

        # A separate operator channel signs a fresh complete private comparison;
        # it never changes the original fill, bill, market or valuation stamps.
        # No failed service object or restored executor supplies these facts.
        rounds = []
        for _ in range(2):
            sdk = context.connectors['okx']
            rows = []
            for path, params in (
                ('/api/v5/account/config', None), ('/api/v5/account/balance', None),
                ('/api/v5/account/positions', None), ('/api/v5/trade/order', {'clOrdId': f.order['clOrdId'], 'instId': 'LIFE-USDT'}),
                ('/api/v5/trade/orders-pending', None), ('/api/v5/account/bills-archive', None),
                    ('/api/v5/trade/fills-history', {'instType': 'SPOT'}), ('/api/v5/trade/fills-history', {'instType': 'SWAP'})):
                rows.append(await sdk._api_get(path_url=path, params=params, is_auth_required=True))
            rounds.append(rows)
        assert rounds[0] == rounds[1]
        balance = {v['ccy']: D(v['cashBal']) for v in rounds[1][1]['data'][0]['details']}
        assert (balance['LIFE'], balance['USDT']) == (f.expected_base, f.expected_quote)
        assert rounds[1][2]['data'] == [] and rounds[1][7]['data'] == []
        assert rounds[1][6]['data'] == [f.fill]
        assert rounds[1][5]['data'] == [*f.bills, dict(billId='100')]
        assert rounds[1][0]['data'] == [t.config]
        assert all(D(v[k]) == 0 for v in rounds[1][1]['data'][0]['details']
                   for k in ('liab', 'crossLiab', 'isoLiab', 'uplLiab', 'interest'))
        financial = PgSpotRepository(await PgRuntime.open(t.cfg))
        capital = PgSharedCapitalRepository(financial, owner=t.owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        batch = PgJointBatchRepository(PgSwapRepository(capital))
        repo = PgJointRepairRepository(batch)
        command = ResolveJointSources('configured-source-repair', original.operation_id, replace(
            original, operation_id='configured-repaired-batch', funding=replace(original.funding, operation_id='configured-repaired-funding')))
        cut = (await financial.observation_cut(t.cfg.binding)).cut
        with pytest.raises(StorageError, match='PG_JOINT_REPAIR_APPROVAL_REQUIRED'):
            await repo.resolve(command, cut=cut, owner=t.owner)
        now = await database[0].fetchval('SELECT clock_timestamp()')
        account = SpotFinancialRecoveryFacts(
            t.cfg.binding, now, balance['LIFE'], balance['USDT'], D('0'), D('0'), (f.trade,), (),
            (f.order['clOrdId'],), (), True, True, True)
        opening = await database[0].fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies')
        facts = JointRepairFacts(account, (('SPOT', f.trade),), (), True, opening, int(now.timestamp() * 1000))
        approval = keys.sign('operator', t.cfg.binding, 'RECOVER', command.json(), cut, facts,
                             issued_at=now - timedelta(seconds=1), expires_at=now + timedelta(seconds=10))
        importer = PgJointRepairApprovalImporter(database[0], batch, verifier=keys.verifier(), max_fact_age_ms=5000)
        with pytest.raises(StorageError, match='OPERATOR_APPROVAL'):
            await importer.approve(command, approval=replace(approval, signature='00' * 64), cut=cut, facts=facts)
        runtime_connection = await asyncpg.connect(t.cfg.dsn)
        try:
            denied = PgJointRepairApprovalImporter(runtime_connection, batch, verifier=keys.verifier(), max_fact_age_ms=5000)
            with pytest.raises(StorageError, match='PG_JOINT_REPAIR_IMPORT_OWNER_REQUIRED'):
                await denied.approve(command, approval=approval, cut=cut, facts=facts)
        finally:
            await runtime_connection.close()
        await importer.approve(command, approval=approval, cut=cut, facts=facts)
        if commit_fault is None:
            assert not (await repo.resolve(command, cut=cut, owner=t.owner)).trading_permission
            assert (await repo.resolve(command, cut=cut, owner=t.owner)).replayed
        else:
            old = await financial.balance(t.cfg.binding)
            committed = commit_fault.endswith('after')
            commit = financial._runtime._commit
            killed = []
            admin = await asyncpg.connect(database[1])

            async def lost(tx):
                assert await tx._connection.fetchval("SELECT receipt->>'state' FROM life_financial.operations WHERE operation_id=$1",
                                                     command.operation_id) == 'REPAIRED'
                pid = tx._connection.get_server_pid()
                if committed:
                    await commit(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
                killed.append(pid)
                if not committed:
                    await commit(tx)
                raise ConnectionError('synthetic lost repair COMMIT result')
            monkeypatch.setattr(financial._runtime, '_commit', lost)
            try:
                if commit_fault.startswith('process_'):
                    await exit_joint_repair(t.cfg, command, cut, t.owner, committed)
                    assert not killed
                else:
                    severed_runtime = financial._runtime
                    with pytest.raises(CommitUnknown):
                        await repo.resolve(command, cut=cut, owner=t.owner)
                    assert len(killed) == 1
            finally:
                await admin.close()
            await close_financial()
            financial = PgSpotRepository(await PgRuntime.open(t.cfg))
            capital = PgSharedCapitalRepository(financial, owner=t.owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
            batch = PgJointBatchRepository(PgSwapRepository(capital))
            repo = PgJointRepairRepository(batch)
            found = await repo.lookup(command.operation_id, t.cfg.binding)
            assert (found is not None) == committed
            if not committed:
                assert await financial.balance(t.cfg.binding) == old
                assert (await repo.frame(t.cfg.binding))['pending']
                assert await database[0].fetchval('SELECT count(*) FROM life_financial.spot_fill_facts') == 0
                assert len(await capital.claims(t.cfg.binding)) == 1
                assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
                context = cold_service_context(manifest)
                with pytest.raises(StorageError, match='PG_COLLATERAL_PENDING_RESOLUTION_REQUIRED'):
                    await context.reopen(False)
                assert await financial.balance(t.cfg.binding) == old
                assert all(r['method'] == 'GET' for r in context.calls)
                return  # No automatic retry or use of an expired approval.
            assert found.replayed and not found.trading_permission
        assert (await batch.lookup(original.operation_id, t.cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts')
        assert len(await capital.claims(t.cfg.binding)) == 1
        assert not (await repo.frame(t.cfg.binding))['pending']
        expected = await financial.balance(t.cfg.binding)
        cost = (await financial.snapshot(t.cfg.binding)).budget.session_execution_loss_usdt
        assert (expected.total_base_life, expected.total_quote_usdt) == (f.expected_base, f.expected_quote)
        assert cost == f.fee
        await close_financial()
        financial = None
        # New SDKs again: the failed startup cannot be reused as recovery proof.
        context = cold_service_context(manifest, independent_fill_value=lambda product, trade, at: values[(product, trade)])
        cold = await context.reopen(False)
        assert await cold.financial.balance(t.cfg.binding) == expected
        assert (await cold.financial.snapshot(t.cfg.binding)).budget.session_execution_loss_usdt == cost
        assert not cold.runner.executor_orchestrator.active_executors['main']
        assert not cold.journal._entries and not cold._owned_executors
        assert all(r['method'] == 'GET' for r in context.calls)
        with pytest.raises(StorageError, match='PG_SERVICE_TRADING_NOT_QUALIFIED'):
            await cold.quote('repaired-does-not-activate', TradeType.BUY, 0)
    finally:
        if not closed:
            try:
                await service.close()
            except StorageError:
                pass
        if stream is not None:
            stream.cancel()
            await asyncio.gather(stream, return_exceptions=True)
        if cold is not None:
            await cold.close()
        if financial is not None:
            await close_financial()
