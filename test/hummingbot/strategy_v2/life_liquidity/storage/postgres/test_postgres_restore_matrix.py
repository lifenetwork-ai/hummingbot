"""Restored native bills, pending recorder, derivatives and expired sessions."""
import asyncio
from dataclasses import asdict, replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_fixture import recovered
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import flow, nav
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import evidence, signed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_events import event
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_lifecycle import terminal
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_ownership import managed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_session import tick
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_exclusion import proof, sign
from uuid import UUID, uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hummingbot.strategy_v2.life_liquidity.carry_monitor import FundingPayment
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.generation_exclusion import CredentialExclusionVerifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import ReconcileSessionSuccessor
from hummingbot.strategy_v2.life_liquidity.storage.postgres.events import PgEventRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import ApplyFunding, PgFundingRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import PgProcessLease
from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.process_handoff import PgProcessHandoffIssuer
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import PgSwapFillRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventSource


async def source(database, *, managed_options=None, **extra):
    private = Ed25519PrivateKey.generate()
    verifier = CredentialExclusionVerifier('synthetic-provider', private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    values = await managed(database, handoff_role=True, **(managed_options or {}), configuration_extra={'credential_authority': verifier.policy(), **extra})
    await values[4].claim('original-source')
    return values, private, verifier


@pytest.mark.asyncio
@pytest.mark.parametrize('currency,amount', [('USDT', D('2')), ('LIFE', D('1'))])
async def test_restored_pending_recorder_and_late_bill_fill_fee_apply_once_without_budget_reset(database, tmp_path, monkeypatch, currency, amount):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import test_postgres_actions as action_fixture
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import (
        bridge,
        command as quote_command,
    )
    report_source = EventSource('controller1', 'life.yml', 'strategy_v2', 'okx')
    values, private, verifier = await source(database, recorder_sources=[asdict(report_source)])
    f, cfg, owner, *_, keys = values
    c = database[0]
    at = await c.fetchval('SELECT clock_timestamp()')
    ms = int(at.timestamp() * 1000)
    await c.execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)", cfg.account_id, cfg.binding.policy_hash, owner.valid_from)
    await c.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                    cfg.account_id, '101', currency, amount, ms, b'synthetic-preverified-import')

    async def original_spot(*args, **kwargs):
        return f, cfg, owner
    with monkeypatch.context() as patch:
        patch.setattr(action_fixture, 'spot', original_spot)
        _, _, _, row = await bridge(database)
    await f.prepare_quote_action(await quote_command(database, cfg, owner, row), cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
    await f.arm('operation1', cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
    reporting = PgEventRepository(f, source=report_source, max_rows=64, max_read_bytes=1048576)
    created = event(cfg.binding, report_source, ms, exchange_id='123456789')
    await reporting.stage(created, cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
    before = await f.snapshot(cfg.binding)
    async with recovered(database, tmp_path, f, cfg, owner, keys, private, verifier) as r:
        f = r.actor.financial
        after = await f.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        recorder = PgEventRepository(f, source=report_source, max_rows=64, max_read_bytes=1048576)
        pending, = await recorder.pending(cfg.binding)
        assert pending.command == created and pending.owner == owner
        await recorder.apply(created.operation_id, cut=(await f.observation_cut(cfg.binding)).cut, owner=owner)
        assert not await recorder.pending(cfg.binding)
        now = await r.c.fetchval('SELECT clock_timestamp()')
        await r.actor.apply_received(ack(owner, now))
        fill = filled(owner, now)
        await r.actor.apply_received(fill)
        money = await f.balance(cfg.binding)
        assert (money.total_base_life, money.total_quote_usdt) == (D('6'), D('8.98'))
        bill = flow(now, ms, currency=currency, signed_amount=amount,
                    source_kind='quote_unit' if currency == 'USDT' else 'independent_market')
        cut = (await f.observation_cut(cfg.binding)).cut
        assert (await f.apply_cashflow(bill, cut=cut, owner=owner)).state == 'APPLIED'
        assert (await f.apply_cashflow(bill, cut=cut, owner=owner)).replayed
        wallet = await f.balance(cfg.binding)
        assert wallet.total_quote_usdt == D('8.98') + (amount if currency == 'USDT' else 0)
        assert wallet.total_base_life == D('6') + (amount if currency == 'LIFE' else 0)
        recorded_fill = event(cfg.binding, report_source, int(now.timestamp() * 1000), 'OrderFilled', exchange_id='123456789',
                              fee_json='{"percent":"0","percent_token":null,"flat_fees":[{"token":"USDT","amount":"0.02"}]}', fee_quote='0.02')
        await recorder.project(recorded_fill, owner=owner)
        assert (await recorder.apply(recorded_fill.operation_id, cut=(await f.observation_cut(cfg.binding)).cut, owner=owner)).replayed
        # Reporting is separate from financial application: it cannot book a
        # second fee, fill or bill. The native original identities remain unique.
        assert await r.c.fetchval('SELECT count(*) FROM life_financial.fills') == 1
        assert await r.c.fetchval('SELECT count(*) FROM life_financial.cashflows') == 1
        assert await r.c.fetchval('SELECT count(*) FROM life_recorder."TradeFill"') == 1
        command = await terminal(f, r.cfg, owner, fills=fill.fills, account_cashflow_ids=('101',))
        await f.confirm_terminal(command, cut=(await f.observation_cut(cfg.binding)).cut, owner=owner)
        now = await r.c.fetchval('SELECT clock_timestamp()')
        measured = await f.observe_nav(nav(now, int(now.timestamp() * 1000)), cut=(await f.observation_cut(cfg.binding)).cut, owner=owner)
        assert measured.capital.net_cashflows_usdt == amount
        assert measured.capital.adjusted_nav_usdt == D('14.98')
        budget = (await f.snapshot(cfg.binding)).budget
        assert budget.campaign_execution_loss_usdt == budget.campaign_subsidy_committed_usdt == D('.12')
        cold = PgEventRepository(r.observer, source=report_source, max_rows=64, max_read_bytes=1048576)
        history = await cold.connector_history(report_source, cfg.binding)
        assert history.trade_ids == (('okx', 'trade1', 'LIFE-USDT'),)
        # Cold process replacement uses a new signed exclusion/private lease;
        # durable import/readback cannot recreate the dead actor's capability.
        await r.restart_observation()
        assert (await r.actor.lease.lookup('matrix-cold-observer-claim')).lease_epoch == 3
        f = r.actor.financial
        before_retry = await f.snapshot(cfg.binding)
        assert (await f.apply_cashflow(bill, cut=(await f.observation_cut(cfg.binding)).cut, owner=owner)).replayed
        # The original terminal identity is final. Cold redelivery cannot
        # reopen its fill scope or regress a completed executor.
        with pytest.raises(StorageError, match='OBSERVATION_ORIGINAL_SCOPE_INVALID'):
            await r.actor.apply_received(fill)
        assert (await f.snapshot(cfg.binding)).budget == before_retry.budget
        assert (await f.balance(cfg.binding)) == before_retry.balance
        assert (await r.importer.lookup(r.request, manifest=r.bundle.image, account_id=cfg.account_id)).replayed
        assert await c.fetchval('SELECT count(*) FROM life_financial.fills') == 0
        assert await c.fetchval('SELECT count(*) FROM life_financial.cashflows') == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('retained_nav', [False, True])
async def test_restored_swap_unknown_ack_fill_fee_and_paid_funding_use_original_account_and_holds(database, tmp_path, monkeypatch, retained_nav):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import (
        test_postgres_shared_capital as shared_fixture,
    )
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_swap_fills import (
        setup as swap_source,
    )
    values, private, verifier = await source(database)
    f, cfg, owner, *_, keys = values

    async def original_spot(*args, **kwargs):
        return f, cfg, owner
    with monkeypatch.context() as patch:
        patch.setattr(shared_fixture, 'spot', original_spot)
        products, swap, fills, fill = await swap_source(database, acknowledge=retained_nav, carry_options={'refresh_initial_facts': True})
    await database[0].execute('INSERT INTO life_financial.funding_accounting_policies VALUES($1,$2,64,128,1048576)', cfg.account_id, cfg.binding.policy_hash)
    if retained_nav:
        await database[0].execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        original_nav = await PgPortfolioRepository(swap).measure(MeasurePortfolio('pre-cut-portfolio'), cut=(await f.observation_cut(cfg.binding)).cut, owner=owner)
        assert original_nav.measurement['highwater_quote'] == 15
    before = await f.snapshot(cfg.binding)
    async with recovered(database, tmp_path, f, cfg, owner, keys, private, verifier) as r:
        f = r.actor.financial
        capital = PgSharedCapitalRepository(f, owner=owner, observation=lambda: products[4][0], max_rows=64, max_bytes=1048576)
        swap = PgSwapRepository(capital)
        if retained_nav:
            portfolio = PgPortfolioRepository(swap)
            restored_nav = await portfolio.lookup('pre-cut-portfolio', cfg.binding)
            assert restored_nav.replayed and restored_nav.measurement == original_nav.measurement
            assert not restored_nav.trading_permission
        assert (await f.balance(cfg.binding)) == before.balance
        assert len(await capital.claims(cfg.binding)) == 1
        assert (await swap.acknowledge('prepare1', exchange_order_id='987654321', owner=owner)).state == 'ACKED'
        now = await r.c.fetchval('SELECT clock_timestamp()')
        at = int(now.timestamp() * 1000)
        trade = replace(fill.fills[0], fill_at_ms=at, independent_value_at_ms=at, independent_observed_at_ms=at)
        fill = replace(fill, fills=(trade,), observed_at=now)
        fills = PgSwapFillRepository(swap)
        cut = (await f.observation_cut(cfg.binding)).cut
        assert (await fills.apply_fills(fill, cut=cut, owner=owner)).state == 'APPLIED'
        assert (await fills.apply_fills(fill, cut=cut, owner=owner)).replayed
        assert (await f.balance(cfg.binding)).total_quote_usdt == D('9.99')
        b = products[7][0]
        now = await r.c.fetchval('SELECT clock_timestamp()')
        at = int(now.timestamp() * 1000)
        snapshot = replace(b.account.snapshot, sequence=2, observed_at_ms=at, spot_cash_quote=D('9.89'), collateral_quote=D('9.89'), short_base=D('1'))
        bundle = replace(b, account=replace(b.account, snapshot=snapshot, included_funding_bill_ids=('777',)),
                         mark=replace(b.mark, sequence=2, observed_at_ms=at), index=replace(b.index, sequence=2, observed_at_ms=at),
                         funding=replace(b.funding, sequence=2, observed_at_ms=at, coverage_through_ms=at, payments=(FundingPayment('777', at, D('-.1')),)),
                         terms=replace(b.terms, sequence=2, observed_at_ms=at))
        funding = PgFundingRepository(swap)
        command = ApplyFunding('post-cut-paid-funding', bundle)
        cut = (await f.observation_cut(cfg.binding)).cut
        result = await funding.apply_funding(command, cut=cut, owner=owner)
        assert result.state == 'APPLIED', await funding.frame(cfg.binding)
        assert (await funding.apply_funding(command, cut=cut, owner=owner)).replayed
        after = await f.snapshot(cfg.binding)
        assert after.balance.total_base_life == 5 and after.balance.total_quote_usdt == D('9.89')
        assert after.budget.campaign_execution_loss_usdt == D('.01')
        assert len(await capital.claims(cfg.binding)) == 1
        assert await r.c.fetchval('SELECT mode FROM life_financial.accounts') == 'RECOVERY_ONLY'
        assert await r.c.fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments') == 0
        assert not (await r.importer.lookup(r.request, manifest=r.bundle.image, account_id=cfg.account_id)).trading_permission


@pytest.mark.asyncio
async def test_restored_inflight_hedge_retains_original_cost_plan_and_claim_while_late_fill_is_booked(database, tmp_path, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import (
        test_postgres_shared_capital as shared_fixture,
    )
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_hedge import (
        child,
        setup as hedge_source,
    )

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge import PgHedgeRepository, PlanHedge
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import FillSwap, SwapFillObservation
    values, private, verifier = await source(database)
    f, cfg, owner, *_, keys = values

    async def original_spot(*args, **kwargs):
        return f, cfg, owner
    with monkeypatch.context() as patch:
        patch.setattr(shared_fixture, 'spot', original_spot)
        products, hedge, market, policy = await hedge_source(database)
    intent = await child(database, products)
    plan = PlanHedge.capture('pre-cut-hedge', market, child=intent)
    original = await hedge.plan(plan, cut=(await f.observation_cut(cfg.binding)).cut, owner=owner)
    await hedge.swap.arm(intent.prepare.operation_id, cut=await hedge.swap.cut(cfg.binding), owner=owner)
    before = await f.snapshot(cfg.binding)
    async with recovered(database, tmp_path, f, cfg, owner, keys, private, verifier) as r:
        f = r.actor.financial
        capital = PgSharedCapitalRepository(f, owner=owner, observation=lambda: products[4][0], max_rows=64, max_bytes=1048576)
        swap = PgSwapRepository(capital)
        hedge = PgHedgeRepository(swap)
        restored = await hedge.lookup(plan.operation_id, cfg.binding)
        assert restored.replayed and restored.decision == original.decision
        assert not restored.trading_permission and restored.child_operation_id == intent.prepare.operation_id
        claims = await capital.claims(cfg.binding)
        assert claims == (intent.claim,)
        assert (await f.balance(cfg.binding)) == before.balance
        await swap.acknowledge(intent.prepare.operation_id, exchange_order_id='987654321', owner=owner)
        now = await r.c.fetchval('SELECT clock_timestamp()')
        at = int(now.timestamp() * 1000)
        trade = SwapFillObservation('hedge-trade1', D('2'), D('1.01'), 'USDT', D('-.02'), at, D('1.01'), at, at, 'independent_market', D('0'), None)
        fill = FillSwap('post-cut-hedge-fill', intent.prepare.operation_id, intent.claim.intent_id, intent.claim.wire_id, 'LIFE-USDT',
                        'SELL', str(owner.session_id), owner.epoch, '987654321', D('2'), (trade,), now)
        result = await PgSwapFillRepository(swap).apply_fills(fill, cut=(await f.observation_cut(cfg.binding)).cut, owner=owner)
        assert result.state == 'APPLIED'
        after = await f.snapshot(cfg.binding)
        assert after.balance.total_base_life == 5 and after.balance.total_quote_usdt == D('9.98')
        assert after.budget.campaign_execution_loss_usdt == D('.02')
        assert await capital.claims(cfg.binding) == claims  # A fill cannot release an unresolved shared claim.
        denied = await hedge.plan(PlanHedge.capture('cannot-rearm-hedge', market), cut=(await f.observation_cut(cfg.binding)).cut, owner=owner)
        assert denied.child_operation_id is None and not denied.trading_permission
        assert not denied.decision['allow_spot_risk_increase']
        assert await r.c.fetchval('SELECT mode FROM life_financial.accounts') == 'RECOVERY_ONLY'
        assert await r.c.fetchval('SELECT count(*) FROM life_financial.send_attempts') == 1
        assert await capital.claims(cfg.binding) == claims


@pytest.mark.asyncio
@pytest.mark.parametrize('on_expiry', ['pause_quotes', 'switch_to_market_reference'])
async def test_expired_original_session_restores_for_observation_only_and_never_renews_its_deadline(database, tmp_path, on_expiry):
    values, private, verifier = await source(database, managed_options=dict(owner_duration_ms=5000, observe_session_role=True,
                                             spot_options=dict(session_duration=timedelta(seconds=6), on_expiry=on_expiry)))
    f, cfg, owner, *_, keys = values
    deadline = (await f.session(cfg.binding)).expires_at
    # Backup includes the original expiry policy/deadline. No test changes a
    # signed session to avoid waiting or turns a restore into a new campaign.
    now = await database[0].fetchval('SELECT clock_timestamp()')
    await asyncio.sleep(max(0, (deadline - now).total_seconds()) + .01)
    async with recovered(database, tmp_path, f, cfg, owner, keys, private, verifier) as r:
        f = r.actor.financial
        await f.tick_session(tick(owner, operation_id='restored-expiry-observation'), cut=(await f.observation_cut(cfg.binding)).cut, owner=owner)
        assert (await f.session(cfg.binding)).expires_at == deadline
        now = await r.c.fetchval('SELECT clock_timestamp()')
        successor = OwnerToken(uuid4(), uuid4(), owner.epoch, owner.session_id, now, now + timedelta(seconds=10))
        command = OwnerTransition('cannot-renew-by-restore', OwnerMode.ACTIVE, successor)
        with pytest.raises(StorageError, match='ACTIVATION_UNQUALIFIED'):
            await r.control.transition_owner(command, **await signed(r.db, r.observer, r.cfg, keys, command))
        assert await r.c.fetchval('SELECT mode FROM life_financial.accounts') == 'RECOVERY_ONLY'
        assert await r.c.fetchval('SELECT count(*) FROM life_financial.sessions') == 1
        assert (await f.session(cfg.binding)).expires_at == deadline
        if on_expiry == 'pause_quotes':
            return
        # The original configured transition may create one fixed successor;
        # restore itself cannot lengthen the old or successor deadline.
        original = await f.snapshot(cfg.binding)
        at = await r.c.fetchval('SELECT clock_timestamp()')
        reconcile = ReconcileSessionSuccessor('restored-fixed-successor', owner, D('1'), at, uuid4())
        next_session = await r.control.reconcile_successor(reconcile, **await signed(r.db, r.observer, r.cfg, keys, reconcile))
        assert next_session.started_at == deadline and next_session.expires_at == deadline + timedelta(seconds=60)
        assert (await r.control.lookup(reconcile.operation_id)).replayed
        assert await r.c.fetchval('SELECT mode FROM life_financial.accounts') == 'RECOVERY_ONLY'
        now = await r.c.fetchval('SELECT clock_timestamp()')
        new_owner = OwnerToken(uuid4(), uuid4(), next_session.epoch, UUID(next_session.session_id), now, now + timedelta(seconds=20))
        activate = OwnerTransition('restored-successor-activate', OwnerMode.ACTIVE, new_owner)
        await r.control.transition_owner(activate, **await signed(r.db, r.observer, r.cfg, keys, activate))
        grant = await r.c.fetchval('SELECT grant_id FROM life_financial.grant_consumptions WHERE operation_id=$1', activate.operation_id)
        previous = await r.actor.lease.lookup('matrix-observer-claim')
        _, _, cap, _, _, cert, _ = proof(cfg=r.cfg, source=r.actor_owner, target=new_owner, grant=previous.grant_id, epoch=previous.lease_epoch, private=private)
        cert = sign(replace(cert, source=r.certificate.target,
                            target=replace(cert.target, service_identity='service:successor', credential_resource='secret:successor:1')), private)
        issuer = PgProcessHandoffIssuer(r.control, credential_verifier=verifier)
        cut, facts = await evidence(r.db, r.observer, r.cfg)
        payload = issuer.request('restored-successor-private', grant, cap.registration, cert)
        approval = keys.sign('operator', cfg.binding, 'HANDOFF', payload, cut, facts,
                             issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
        await issuer.register_successor('restored-successor-private', grant, cap.registration, certificate=cert, approval=approval, facts=facts, cut=cut)
        cold = PgSpotRepository(await PgRuntime.open(r.cfg))
        try:
            lease = PgProcessLease(cold._runtime, cap)
            assert (await lease.claim('restored-successor-private-claim')).lease_epoch == 3
            current = await cold.snapshot(cfg.binding)
            assert (current.balance, current.budget) == (original.balance, original.budget)
            assert current.session.session_id == next_session.session_id and current.session.expires_at == next_session.expires_at
        finally:
            await cold.close()
