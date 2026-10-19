"""Native logical successor reconciliation is independent of signed activation."""
import asyncio
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import evidence, setup, signed
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_session import tick
from uuid import NAMESPACE_URL, uuid4, uuid5

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, OwnerToken, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition


@pytest.mark.asyncio
async def test_successor_uses_original_deadline_and_duration_preserves_money_and_requires_separate_signed_activation(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import ReconcileSessionSuccessor
    financial, cfg, owner, keys, control = await setup(database, session_duration=timedelta(seconds=1), on_expiry='switch_to_market_reference')
    try:
        before = await financial.snapshot(cfg.binding)
        now = await database[0].fetchval('SELECT clock_timestamp()')
        await asyncio.sleep(max(0, (before.session.expires_at - now).total_seconds()) + .01)
        await financial.tick_session(tick(owner, operation_id='expire'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        cut, facts = await evidence(database, financial, cfg)
        command = ReconcileSessionSuccessor('successor', owner, D('1.2'), facts.observed_at, uuid4())
        result = await control.reconcile_successor(command, **await signed(database, financial, cfg, keys, command))
        assert result.session_id == str(uuid5(NAMESPACE_URL, str(owner.session_id) + ':market-successor'))
        assert result.epoch == owner.epoch + 1
        assert result.started_at == before.session.expires_at
        assert result.expires_at == before.session.expires_at + timedelta(milliseconds=60000)
        assert not result.trading_permission and not result.replayed
        after = await financial.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
        assert await database[0].fetchval('SELECT mode FROM life_financial.accounts') == 'RECOVERY_ONLY'
        assert (await control.lookup('successor')).replayed
        now = await database[0].fetchval('SELECT clock_timestamp()')
        target = OwnerToken(uuid4(), uuid4(), result.epoch, uuid5(NAMESPACE_URL, str(owner.session_id) + ':market-successor'),
                            now, min(result.expires_at, now + timedelta(seconds=10)))
        activate = OwnerTransition('activate-successor', OwnerMode.ACTIVE, target)
        activation = await control.transition_owner(activate, **await signed(database, financial, cfg, keys, activate))
        assert activation.token == target and not activation.trading_permission
        final = await financial.snapshot(cfg.binding)
        assert final.session.session_id == result.session_id and final.session.expires_at == result.expires_at
        assert final.balance == before.balance and final.budget == before.budget
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 1
    finally:
        await financial.close()


async def expired_parent(database, **options):
    values = await setup(database, session_duration=timedelta(seconds=1), on_expiry='switch_to_market_reference', **options)
    financial, cfg, owner = values[:3]
    view = await financial.snapshot(cfg.binding)
    now = await database[0].fetchval('SELECT clock_timestamp()')
    await asyncio.sleep(max(0, (view.session.expires_at - now).total_seconds()) + .01)
    await financial.tick_session(tick(owner, operation_id='expire'), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
    return values


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['early', 'clock-rollback', 'incomplete', 'foreign-order', 'liability', 'wallet', 'future-reference', 'stale-reference', 'owner'])
async def test_successor_rejects_unqualified_source_without_new_session_money_or_activation(database, fault):
    from dataclasses import replace

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import ReconcileSessionSuccessor
    if fault in ('early', 'clock-rollback'):
        options = {} if fault == 'early' else dict(session_clock_offset=timedelta(hours=1))
        values = await setup(database, session_duration=timedelta(seconds=10), on_expiry='switch_to_market_reference', **options)
        if fault == 'clock-rollback':
            financial, cfg, owner = values[:3]
            await financial.tick_session(tick(owner), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
            assert (await financial.session(cfg.binding)).rollback_latched
    else:
        values = await expired_parent(database)
    financial, cfg, owner, keys, control = values
    try:
        cut, facts = await evidence(database, financial, cfg)
        command = ReconcileSessionSuccessor('successor', owner, D('1.2'), facts.observed_at, uuid4())
        if fault == 'incomplete':
            facts = replace(facts, runner_scope_complete=False)
        elif fault == 'foreign-order':
            facts = replace(facts, accepted_wire_ids=('foreign',))
        elif fault == 'liability':
            facts = replace(facts, liability_quote_usdt=D('.1'))
        elif fault == 'wallet':
            facts = replace(facts, account_base_life=D('6'))
        elif fault in ('future-reference', 'stale-reference'):
            command = replace(command, reference_observed_at=facts.observed_at + timedelta(seconds=5 if fault == 'future-reference' else -5))
        elif fault == 'owner':
            command = replace(command, owner=replace(owner, generation=uuid4()))
        approval = keys.sign('operator', cfg.binding, command.role, command.json(), cut, facts,
                             issued_at=facts.observed_at - timedelta(seconds=1), expires_at=facts.observed_at + timedelta(seconds=5))
        before = await financial.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_SUCCESSOR_SOURCE_UNQUALIFIED'):
            await control.reconcile_successor(command, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.sessions') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_successor_clock_observation_and_cold_readback_preserve_manual_activation_and_fixed_expiry(database):
    from dataclasses import replace

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import (
        PgControlRepository,
        ReconcileSessionSuccessor,
    )
    financial, cfg, owner, keys, control = await expired_parent(database)
    try:
        cut, facts = await evidence(database, financial, cfg)
        command = ReconcileSessionSuccessor('successor', owner, D('1.2'), facts.observed_at, uuid4())
        args = await signed(database, financial, cfg, keys, command)
        receipt = await control.reconcile_successor(command, **args)
        assert (await control.reconcile_successor(command, **args)).replayed
        view = await control.successor()
        assert view.state == 'PAUSED' and view.reason_code == 'MANUAL_ACTIVATION_REQUIRED' and not view.trading_permission
        await control.observe_successor('observe-clock', owner=owner)
        later = await control.successor()
        assert later.receipt.expires_at == receipt.expires_at and later.reason_code == view.reason_code
        cold = PgControlRepository(database[0], financial, verifier=keys.verifier())
        assert (await cold.lookup('successor')).replayed
        assert (await cold.successor()).reason_code == 'MANUAL_ACTIVATION_REQUIRED'
        new = replace(command, operation_id='another-successor', reference_observed_at=later.observed_at)
        with pytest.raises(StorageError, match='PG_SUCCESSOR_ALREADY_RECONCILED'):
            await cold.reconcile_successor(new, **await signed(database, financial, cfg, keys, new))
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.sessions') == 2
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
        assert not financial._runtime.healthy
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_late_reconciliation_never_extends_already_expired_successor_or_activates_it(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import ReconcileSessionSuccessor
    financial, cfg, owner, keys, control = await setup(database, successor_duration_ms=1, session_duration=timedelta(seconds=1), on_expiry='switch_to_market_reference')
    try:
        before = await financial.snapshot(cfg.binding)
        now = await database[0].fetchval('SELECT clock_timestamp()')
        await asyncio.sleep(max(0, (before.session.expires_at - now).total_seconds()) + .02)
        await financial.tick_session(tick(owner), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        cut, facts = await evidence(database, financial, cfg)
        command = ReconcileSessionSuccessor('successor', owner, D('1.2'), facts.observed_at, uuid4())
        receipt = await control.reconcile_successor(command, **await signed(database, financial, cfg, keys, command))
        view = await control.successor()
        assert view.state == 'EXPIRED' and receipt.expires_at == before.session.expires_at + timedelta(milliseconds=1)
        token = OwnerToken(uuid4(), uuid4(), receipt.epoch, uuid5(NAMESPACE_URL, str(owner.session_id) + ':market-successor'),
                           facts.observed_at, facts.observed_at + timedelta(seconds=1))
        activation = OwnerTransition('activate', OwnerMode.ACTIVE, token)
        with pytest.raises(StorageError, match='PG_SUCCESSOR_ACTIVATION_UNQUALIFIED'):
            await control.transition_owner(activation, **await signed(database, financial, cfg, keys, activation))
        assert (await control.successor()).receipt.expires_at == receipt.expires_at
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('successor', [False, True])
async def test_activation_keeps_real_fill_fee_subsidy_and_campaign_loss_history_without_reopening_old_ids(database, successor):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_inventory_settlement import (
        command as settle_command,
        cycle,
        facts as settle_facts,
    )

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import ReconcileSessionSuccessor
    financial, cfg, owner, keys, control = await setup(database, session_duration=timedelta(seconds=6 if successor else 60),
                                                       on_expiry='switch_to_market_reference' if successor else 'pause_quotes')
    try:
        _, _, _, settlement = await cycle(database, financial_values=(financial, cfg, owner))
        await settlement.settle_inventory(settle_command(), facts=await settle_facts(financial), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        before = await financial.snapshot(cfg.binding)
        assert before.budget.campaign_execution_loss_usdt == D('.12')
        assert before.budget.campaign_subsidy_committed_usdt == D('.12')
        budget_rows = await database[0].fetch('SELECT to_jsonb(b) AS body FROM life_financial.budget_buckets b ORDER BY family,scope,window_key')
        fills = await database[0].fetch('SELECT to_jsonb(f) AS body FROM life_financial.fills f ORDER BY trade_id')
        if successor:
            now = await database[0].fetchval('SELECT clock_timestamp()')
            await asyncio.sleep(max(0, (before.session.expires_at - now).total_seconds()) + .01)
            await financial.tick_session(tick(owner), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
            cut, facts = await evidence(database, financial, cfg)
            command = ReconcileSessionSuccessor('successor', owner, D('1.2'), facts.observed_at, uuid4())
            child = await control.reconcile_successor(command, **await signed(database, financial, cfg, keys, command))
            session_id, epoch = uuid5(NAMESPACE_URL, str(owner.session_id) + ':market-successor'), child.epoch
        else:
            command = OwnerTransition('drain', OwnerMode.DRAINING, owner)
            await control.transition_owner(command, **await signed(database, financial, cfg, keys, command))
            session_id, epoch = owner.session_id, owner.epoch
        now = await database[0].fetchval('SELECT clock_timestamp()')
        target = OwnerToken(uuid4(), uuid4(), epoch, session_id, now, now + timedelta(seconds=10))
        command = OwnerTransition('activate', OwnerMode.ACTIVE, target)
        await control.transition_owner(command, **await signed(database, financial, cfg, keys, command))
        after = await financial.snapshot(cfg.binding)
        assert not after.recovery_required and after.balance == before.balance
        assert after.budget.campaign_execution_loss_usdt == before.budget.campaign_execution_loss_usdt
        assert after.budget.campaign_subsidy_committed_usdt == before.budget.campaign_subsidy_committed_usdt
        assert await database[0].fetch('SELECT to_jsonb(b) AS body FROM life_financial.budget_buckets b ORDER BY family,scope,window_key') == budget_rows
        assert await database[0].fetch('SELECT to_jsonb(f) AS body FROM life_financial.fills f ORDER BY trade_id') == fills
        assert (await financial.lookup_prepare('operation1', cfg.binding)).replayed
        assert not financial._runtime.healthy
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_successor_activation_rechecks_parent_clock_latch_at_physical_commit(database, monkeypatch):
    from uuid import UUID

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import ReconcileSessionSuccessor
    financial, cfg, owner, keys, control = await expired_parent(database)
    try:
        _, facts = await evidence(database, financial, cfg)
        reconcile = ReconcileSessionSuccessor('successor', owner, D('1.2'), facts.observed_at, uuid4())
        child = await control.reconcile_successor(reconcile, **await signed(database, financial, cfg, keys, reconcile))
        now = await database[0].fetchval('SELECT clock_timestamp()')
        target = OwnerToken(uuid4(), uuid4(), child.epoch, UUID(child.session_id), now, now + timedelta(seconds=10))
        command = OwnerTransition('activate-child', OwnerMode.ACTIVE, target)
        args = await signed(database, financial, cfg, keys, command)
        original = control._commit
        reached = False

        async def changed(tx):
            nonlocal reached
            reached = True
            await tx._connection.execute("""UPDATE life_financial.spot_session_progress SET state='PAUSED',
                reason_code='CLOCK_ROLLBACK',rollback_latched=true,version=version+1 WHERE account_id=$1 AND session_id=$2""",
                                         cfg.account_id, owner.session_id)
            await original(tx)
        monkeypatch.setattr(control, '_commit', changed)
        with pytest.raises(StorageError, match='PG_SUCCESSOR_ACTIVATION_UNQUALIFIED_AT_COMMIT'):
            await control.transition_owner(command, **args)
        assert reached
        assert await database[0].fetchval('SELECT mode FROM life_financial.accounts') == 'RECOVERY_ONLY'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.control_records') == 1
        assert (await control.successor()).reason_code == 'MANUAL_ACTIVATION_REQUIRED'
    finally:
        await financial.close()
