"""Supported paused actual sender: provider exclusion, native recovery and outage cancel."""
import asyncio
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from test.hummingbot.core.web_assistant.connections.test_request_guard import Response
from test.hummingbot.strategy_v2.life_liquidity.storage.offline_generation_provider import OfflineGenerationProvider
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import evidence, signed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_generation_broker import (
    broker_service,
    identity,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_sdk import sdk
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.generation_access import GenerationClient, GenerationCredentials
from hummingbot.strategy_v2.life_liquidity.storage.generation_exclusion import CredentialExclusionVerifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.cancellation_manifest import (
    PgCancellationManifestRepository,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.generation_broker import (
    PgGenerationBrokerControl,
    bind_private_broker,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recovery_actor import PgRecoveryActor, PgRecoveryGrantIssuer
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@pytest.mark.asyncio
@pytest.mark.parametrize('barrier', ['before_accept', 'after_accept'])
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
async def test_paused_original_sdk_replacement_exclusion_and_recovery_outage_cancel(database, tmp_path, barrier, product, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.flow_trace import QuoteFlowTrace
    trace = QuoteFlowTrace(monkeypatch).install() if os.environ.get('LIFE_PG_TEST_FLOW_TRACE') == '1' else None
    provider = OfflineGenerationProvider()
    t, service, native, state = await broker_service(database, provider=provider, hedge=product == 'SWAP')
    if trace is not None:
        trace.http(t.session)
    broker, source = state.broker, state.client.identity.registration
    entered, release = asyncio.Event(), asyncio.Event()
    task = actor = control_financial = None
    state2 = SimpleNamespace()

    async def pause(resource, request):
        if resource == source.identity.credential_resource:
            entered.set()
            await release.wait()
    setattr(provider, barrier, pause)
    t.session.request = provider.wrap(t.session.request)
    try:
        await service.arm_process()
        await state.control.sync(source, enable_active=True)
        task = asyncio.create_task(service.quote('supported-paused', TradeType.BUY, 0) if product == 'SPOT' else service.execute_hedge('supported-paused'))
        waiting = asyncio.create_task(entered.wait())
        done, _ = await asyncio.wait((task, waiting), timeout=service._trading_model.quote_schedule_timeout_ms / 1000, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            await task
        assert waiting in done and entered.is_set()
        await waiting
        if product == 'SPOT':
            assert service.quote_application._guards['supported-paused']._consumed
        attempt = await database[0].fetchrow('SELECT * FROM life_financial.send_attempts WHERE product=$1', product)
        intent_id = attempt['intent_id']
        assert attempt['state'] == 'SEND_UNKNOWN'
        before = await service.financial.snapshot(t.cfg.binding)
        if product == 'SPOT':
            assert before.balance.reserved_quote_usdt > 0
        else:
            assert await service.source.capital.claims(t.cfg.binding)
        service.revoke()
        control_financial = PgSpotRepository(await PgRuntime.open(t.cfg))
        control = PgControlRepository(database[0], control_financial, verifier=native.keys.verifier())
        recover = OwnerTransition('supported-recover', OwnerMode.RECOVERY_ONLY, t.owner)
        await control.transition_owner(recover, **await signed(database, control_financial, t.cfg, native.keys, recover))
        await broker.begin_exclusion(source)
        now = await database[0].fetchval('SELECT clock_timestamp()')
        target_owner = OwnerToken(uuid4(), uuid4(), t.owner.epoch, t.owner.session_id, now, now + timedelta(seconds=20))
        verifier = CredentialExclusionVerifier('generation-provider', broker.public_key)
        issuer = PgRecoveryGrantIssuer(control, credential_verifier=verifier)
        grant = uuid4()

        async def register(registration):
            worker = identity(t.cfg, registration, resource='secret:recovery:1', service='service:recovery')
            credentials = GenerationCredentials('recovery-key', 'recovery-secret', 'recovery-pass')
            broker.stage(worker.registration, credentials)
            provider.register(worker.registration.identity.credential_resource, credentials)
            client = GenerationClient(worker, broker.dispatch, public_key=broker.public_key, local_ready=lambda: False, timeout_ms=1000)
            with pytest.raises(StorageError):
                await client.credentials()
            with pytest.raises(StorageError):
                await broker.certify(source, worker.registration, expires_at=now + timedelta(seconds=5))
            provider.effect(source.identity.credential_resource, ws=False, caa=False)
            with pytest.raises(StorageError):
                await broker.certify(source, worker.registration, expires_at=now + timedelta(seconds=5))
            provider.effect(source.identity.credential_resource)
            if barrier == 'after_accept':
                with pytest.raises(StorageError):
                    await broker.certify(source, worker.registration, expires_at=now + timedelta(seconds=5))
                # Accepted source requests finish before replacement credential delivery.
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                assert len(t.service_orders) == 1
            certificate = await broker.certify(source, worker.registration, expires_at=now + timedelta(seconds=5))
            cut, facts = await evidence(database, control_financial, t.cfg)
            facts = replace(facts, accepted_wire_ids=(attempt['wire_id'],), order_scope_complete=False, runner_scope_complete=False)
            request = issuer.request('supported-delegation', grant, registration, certificate, t.owner)
            approval = native.keys.sign('operator', t.cfg.binding, 'HANDOFF', request, cut, facts, issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
            await issuer.register('supported-delegation', grant, registration, source_owner=t.owner, certificate=certificate, approval=approval, facts=facts, cut=cut)
            broker.admit(worker.registration, certificate, recovery=True)
            # Even after exclusion, no delivery until original private native claim/bind.
            with pytest.raises(StorageError):
                await client.credentials()
            state2.client, state2.worker, state2.credentials = client, worker, credentials
        actor = await PgRecoveryActor.open(t.cfg, owner=target_owner, claim_operation_id='supported-recovery-process', heartbeat_interval_ms=250, register=register)
        await bind_private_broker(actor.runtime, state2.client, 'supported-recovery-bind')
        await PgGenerationBrokerControl(control, broker).sync(state2.worker.registration)
        assert await state2.client.credentials() == state2.credentials
        with pytest.raises(StorageError):
            await state.client.credentials()
        with pytest.raises(StorageError):
            await PgGenerationBrokerControl(control, broker).sync(state2.worker.registration, enable_active=True)
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        assert len(t.service_orders) == (1 if barrier == 'after_accept' else 0)
        assert not provider.pending
        after = await actor.financial.snapshot(t.cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert await database[0].fetchval('SELECT state FROM life_financial.send_attempts WHERE intent_id=$1', intent_id) == ('ACKED' if barrier == 'after_accept' else 'SEND_UNKNOWN')
        exporter = PgCancellationManifestRepository(actor.financial, max_rows=8, max_bytes=65536)
        manifest = await exporter.capture(source_owner=t.owner)
        assert len(manifest.identities) == (1 if barrier == 'after_accept' else 0)
        manifest.save(tmp_path / 'emergency.json')
        broker.pin_manifest(state2.worker.registration, manifest)
        holds = await actor.financial.snapshot(t.cfg.binding)
        calls = []

        async def http(**kw):
            import json
            calls.append(kw)
            response = Response()

            async def body():
                data = json.loads(kw['data']) if kw.get('data') else {}
                return dict(code='0', data=[dict(uid='12345', sCode='0', ordId=data.get('ordId'), clOrdId=data.get('clOrdId'))])
            response.json = body
            return response
        connector = sdk(state2.credentials, product, SimpleNamespace(request=provider.wrap(http)))
        await state2.client.install({connector.name: connector})
        # Close actual physical DB pools; emergency SDK path must need no borrow.
        await actor.runtime.close()
        assert not actor.ready
        await connector._api_get('/api/v5/account/config', is_auth_required=True)
        if manifest.identities:
            assert await state2.client.cancel_exact(connector, manifest.identities[0])
            for identity_change in (dict(wire_id='successorwire'), dict(exchange_order_id='999'), dict(instrument='LIFE-USDT-SWAP' if product == 'SPOT' else 'LIFE-USDT', product='SWAP' if product == 'SPOT' else 'SPOT')):
                with pytest.raises(StorageError):
                    await state2.client.cancel_exact(connector, replace(manifest.identities[0], **identity_change))
        (tmp_path / 'emergency.json').unlink()
        broker.pin_manifest(state2.worker.registration, replace(manifest, identities=()))
        if manifest.identities:
            with pytest.raises(StorageError):
                await state2.client.cancel_exact(connector, manifest.identities[0])
        # An old cached SDK cannot use cancellation as a revocation bypass.
        with pytest.raises(StorageError):
            await service.connectors['okx']._api_post('/api/v5/trade/cancel-order', data=dict(instId='LIFE-USDT', clOrdId='old', ordId='1'), is_auth_required=True)
        final = await control_financial.snapshot(t.cfg.binding)
        assert (final.balance, final.budget, final.cut) == (holds.balance, holds.budget, holds.cut)
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if actor is not None:
            await actor.close()
        try:
            await service.close()
        except StorageError:
            assert service.runtime._closing and service._close_task.done()
        if control_financial is not None:
            await control_financial.close()
        if trace is not None:
            import json
            from dataclasses import asdict
            print('bounded_native_generation_flow=' + json.dumps(dict(
                summaries=trace.summaries(), rows=trace.rows(),
                background_leases=[asdict(v) for v in trace.background_leases],
                background_leases_dropped=trace.background_leases_dropped)))


@pytest.mark.asyncio
async def test_clean_successor_installs_new_sdk_credentials_and_two_explicit_arms_before_order(database):
    from decimal import Decimal as D
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_shared_sdk_quota import fake_http

    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.data_feed.market_data_provider import MarketDataProvider
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.managed_process import PgManagedProcessConfig
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.process_handoff import PgProcessHandoffIssuer
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.service import PgLiquidityService
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota
    provider = OfflineGenerationProvider()
    t, original, native, state = await broker_service(database, provider=provider)
    target = control_financial = None
    broker = state.broker
    source = state.client.identity.registration
    next_state = SimpleNamespace()
    try:
        original.drain_process()
        await original.close()
        control_financial = PgSpotRepository(await PgRuntime.open(t.cfg))
        control = PgControlRepository(database[0], control_financial, verifier=native.keys.verifier())
        command = OwnerTransition('broker-clean-drain', OwnerMode.DRAINING, t.owner)
        await control.transition_owner(command, **await signed(database, control_financial, t.cfg, native.keys, command))
        now = await database[0].fetchval('SELECT clock_timestamp()')
        owner = OwnerToken(uuid4(), uuid4(), t.owner.epoch, t.owner.session_id, now, now + timedelta(seconds=30))
        command = OwnerTransition('broker-clean-activate', OwnerMode.ACTIVE, owner)
        await control.transition_owner(command, **await signed(database, control_financial, t.cfg, native.keys, command))
        grant = await database[0].fetchval("SELECT grant_id FROM life_financial.grant_consumptions WHERE operation_id='broker-clean-activate'")
        verifier = CredentialExclusionVerifier('generation-provider', broker.public_key)
        issuer = PgProcessHandoffIssuer(control, credential_verifier=verifier)
        credentials = GenerationCredentials('clean-key', 'clean-secret', 'clean-pass')
        spot = OkxExchange(credentials.api_key, credentials.secret, credentials.passphrase, trading_pairs=['LIFE-USDT'], trading_required=False)
        swap = OkxPerpetualDerivative(okx_perpetual_api_key=credentials.api_key, okx_perpetual_secret_key=credentials.secret,
                                      okx_perpetual_passphrase=credentials.passphrase, trading_pairs=['LIFE-USDT'], trading_required=False)
        # New SDKs and factories, only public market/balance/rule state is copied.
        for connector, old in ((spot, t.connector), (swap, t.swap_connector)):
            fake_http(connector, t.session)
            connector.enable_protected_trading_pair('LIFE-USDT')
            connector._set_trading_pair_symbol_map((await old.trading_pair_symbol_map()).copy())
            connector._trading_rules.update(old._trading_rules)
            connector._account_balances.update(LIFE=D('5'), USDT=D('10'))
            connector._account_available_balances.update(LIFE=D('5'), USDT=D('10'))
            connector.order_book_tracker._order_books.update(old.order_book_tracker._order_books)
        swap._contract_sizes.update(t.swap_connector._contract_sizes)
        swap._perpetual_trading.set_position_mode(t.swap_connector.position_mode)
        swap._perpetual_trading.set_leverage('LIFE-USDT', 2)
        PgSharedSdkQuota.install(spot=spot, swap=swap)
        original_http = t.session.request

        async def fresh_http(**kwargs):
            for connector in (spot, swap):
                connector._set_current_timestamp(datetime.now(timezone.utc).timestamp())
            return await original_http(**kwargs)
        t.session.request = provider.wrap(fresh_http)

        async def register(public):
            worker = identity(t.cfg, public, resource='secret:clean:1', service='service:clean')
            broker.stage(worker.registration, credentials)
            provider.register(worker.registration.identity.credential_resource, credentials)
            await broker.begin_exclusion(source)
            provider.effect(source.identity.credential_resource)
            certificate = await broker.certify(source, worker.registration, expires_at=now + timedelta(seconds=5))
            cut, facts = await evidence(database, control_financial, t.cfg)
            request = issuer.request('broker-clean-handoff', grant, public, certificate)
            approval = native.keys.sign('operator', t.cfg.binding, 'HANDOFF', request, cut, facts, issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
            await issuer.register_successor('broker-clean-handoff', grant, public, certificate=certificate, approval=approval, facts=facts, cut=cut)
            broker.admit(worker.registration, certificate, recovery=False)
            next_state.client = GenerationClient(worker, broker.dispatch, public_key=broker.public_key, local_ready=lambda: next_state.service._trading_ready(), timeout_ms=1000)

        async def factory(service):
            next_state.service = service
            await bind_private_broker(service.runtime, next_state.client, 'broker-clean-bind')
            next_state.control = PgGenerationBrokerControl(control, broker)
            await next_state.control.sync(next_state.client.identity.registration)
            return next_state.client
        connectors = {'okx': spot, 'okx_perpetual': swap}
        target = await PgLiquidityService.open(original.config, runtime_config=t.cfg, owner=owner, connectors=connectors,
                                               market_data_provider=MarketDataProvider(connectors), sources=tuple(e.source for e in original.reporting.events),
                                               limits=original.limits, clock=original.clock, independent_fill_value=lambda *args: None, independent_cashflow_value=lambda *args: None,
                                               native_trading=True, managed_process=PgManagedProcessConfig(claim_operation_id='broker-clean-claim', heartbeat_interval_ms=250),
                                               register_process=register, generation_access_factory=factory)
        assert target.process_mode == 'UNARMED' and target.process_lease.ready
        assert (await target.process_lease.lookup('broker-clean-claim')).lease_epoch == 2
        with pytest.raises(StorageError):
            await target.quote('before-both-arms', TradeType.BUY, 0)
        await target.arm_process()
        await next_state.control.sync(next_state.client.identity.registration, enable_active=True)
        await target.quote('broker-successor-order', TradeType.BUY, 0)
        assert len(t.service_orders) == 1
        with pytest.raises(StorageError):
            await state.client.credentials()
        assert provider.observed[-1][2] == 'secret:clean:1'
    finally:
        if target is not None:
            await target.close()
        await original.close()
        if control_financial is not None:
            await control_financial.close()
