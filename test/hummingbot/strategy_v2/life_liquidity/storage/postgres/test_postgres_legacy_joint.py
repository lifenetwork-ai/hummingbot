"""Actual outer File SWAP interruption against an explicit original native seed."""
import asyncio
import json
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import KEYS
from uuid import UUID

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.legacy_applying import RetireLegacyApplying
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import ApplyJointBatch, PgJointBatchRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import (
    JointRepairFacts,
    PgJointRepairRepository,
    ResolveJointSources,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair_approval import PgJointRepairApprovalImporter
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import (
    ImportLegacyApplying,
    PgLegacyApplyingImporter,
)
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


async def setup(database, tmp_path, monkeypatch, stage, *, entry=D('1'), fee=D('-.001'), funding=None):
    from test.hummingbot.strategy_v2.life_liquidity import test_session
    from test.hummingbot.strategy_v2.life_liquidity.storage import test_legacy_joint_applying as file_fixture
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_carry import setup as carry_setup

    from hummingbot.core.data_type.common import PositionAction, TradeType
    from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
    from hummingbot.strategy_v2.life_liquidity.carry_monitor import FundingPayment
    from hummingbot.strategy_v2.life_liquidity.joint_recovery import RecoveryPayment
    from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalSnapshot
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import ApplyFunding
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import FillSwap, SwapFillObservation
    from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
    from hummingbot.strategy_v2.life_liquidity.storage.swap import PrepareSwap
    admin = database[0]
    now = await admin.fetchval('SELECT clock_timestamp()')
    original_report = file_fixture.report
    pnl = D('.25') * (D('1') - entry)
    final_cash = D('10') + pnl + fee + (funding or D('0'))
    final_collateral = final_cash + min(D('0'), D('.75') * (D('1') - entry))

    def clock_init(self):
        self.wall, self.mono = now, 100.0

    def numeric_report(values, box, claim):
        result = original_report(values, box, claim)
        result = replace(result, exchange_order_id='987654321', trades=(replace(result.trades[0], realized_pnl_quote=pnl, signed_fee=fee),))
        stamp = int(values[6].wall.timestamp() * 1000)
        box['value'] = replace(box['value'], orders=(result,), observed_at_ms=stamp,
                               payments=() if funding is None else (RecoveryPayment('777', funding, stamp),))
        values[8]['snapshot'] = replace(values[8]['snapshot'], spot_cash_quote=final_cash, collateral_quote=final_collateral)
        return result

    prepared_target = {}

    async def before_report(joint, store, recovery, values, claim, file_owner):
        binding = store.binding
        images = recovery.journal._read()
        anchor = CapitalSnapshot(**{**images['anchor'], **{key: D(images['anchor'][key]) for key in (
            'spot_life_base', 'spot_cash_quote', 'long_base', 'short_base', 'collateral_quote', 'initial_margin_quote',
            'maintenance_margin_quote', 'funding_liability_quote', 'mark_price_usdt', 'index_price_usdt')}, 'pending': ()})
        # An explicitly seeded ORIGINAL target basis/order, not the partial File cash.
        native = await carry_setup(database, max_age_ms=1000, refresh_initial_facts=True,
                                   capital_changes=dict(position_mode='ONEWAY', leverage=1), observed=anchor,
                                   spot_options=dict(original_session_id=file_owner.session_id, opening_base_life=D('10'), config_hash=binding.config_hash,
                                                     configuration_body=lambda _: {'operator_verifier': KEYS.verifier().policy(), 'legacy_source_verifier': KEYS.verifier().policy()}))
        financial, cfg, owner, _, feed, capital, _, carry_feed, carry = native
        policy = dict(controller_id='life', tick_size_usdt='0.0001', min_size_contracts='1', max_quote_levels=8, max_capture_age_ms=1000)
        await admin.execute('INSERT INTO life_financial.swap_policies VALUES($1,$2,$3::jsonb,$4)', cfg.account_id, cfg.binding.policy_hash, json.dumps(policy), fingerprint(policy))
        opening = store._accounting_policy
        await admin.execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,10,10,1,$3,'100',4096)", cfg.account_id, cfg.binding.policy_hash, opening.opened_at)
        await admin.execute('INSERT INTO life_financial.swap_fill_policies VALUES($1,$2,1,0,$3,0,64,128,500)', cfg.account_id, cfg.binding.policy_hash, entry)
        await admin.execute('INSERT INTO life_financial.funding_accounting_policies VALUES($1,$2,64,128,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await admin.execute('INSERT INTO life_financial.joint_batch_policies VALUES($1,$2,32,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
        await admin.execute('INSERT INTO life_financial.joint_repair_policies VALUES($1,$2,64,64,1048576,5000)', cfg.account_id, cfg.binding.policy_hash)
        await admin.execute('INSERT INTO life_financial.legacy_applying_policies VALUES($1,$2,4,4194304,5000)', cfg.account_id, cfg.binding.policy_hash)
        now = await admin.fetchval('SELECT clock_timestamp()')
        feed[0] = replace(anchor, sequence=2, observed_at_ms=int(now.timestamp() * 1000))
        carry_feed[0] = replace(carry_feed[0], account=replace(carry_feed[0].account, snapshot=feed[0]))
        assert (await capital.reserve(ReserveCapital('original-claim', claim), await capital.cut(cfg.binding), owner)).decision.allowed
        assert (await carry.check_carry(cfg.binding)).allowed

        async def refresh_original_source():
            # No fill has occurred yet. The fake venue still reports the actual
            # original cash/position and complete empty paid-bill scope. Refresh
            # each independent stream before the next real admission boundary;
            # never carry a 1000-ms observation through unrelated setup work.
            stamp = int((await admin.fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
            feed[0] = replace(feed[0], sequence=feed[0].sequence + 1, observed_at_ms=stamp)
            old = carry_feed[0]
            carry_feed[0] = replace(old, account=replace(old.account, snapshot=feed[0]),
                                    mark=replace(old.mark, sequence=old.mark.sequence + 1, observed_at_ms=stamp),
                                    index=replace(old.index, sequence=old.index.sequence + 1, observed_at_ms=stamp),
                                    funding=replace(old.funding, sequence=old.funding.sequence + 1, observed_at_ms=stamp, coverage_through_ms=stamp),
                                    terms=replace(old.terms, sequence=old.terms.sequence + 1, observed_at_ms=stamp))
            assert (await capital.check_capital(cfg.binding)).allowed
            assert (await carry.check_carry(cfg.binding)).allowed

        await refresh_original_source()
        now = await admin.fetchval('SELECT clock_timestamp()')
        config = OrderExecutorConfig(id=claim.intent_id, timestamp=now.timestamp(), controller_id='life', connector_name='okx_perpetual',
                                     trading_pair='LIFE-USDT', side=TradeType.SELL, position_action=PositionAction.CLOSE, amount=claim.quantity_base,
                                     price=claim.price_usdt, leverage=1, level_id='0', execution_strategy=ExecutionStrategy.LIMIT_MAKER)
        swap = PgSwapRepository(capital)
        prepared = PrepareSwap.from_config('original-prepare', claim.wire_id, config)
        await swap.prepare(prepared, cut=await swap.cut(cfg.binding), owner=owner)
        await refresh_original_source()
        await swap.arm(prepared.operation_id, cut=await swap.cut(cfg.binding), owner=owner)
        await swap.acknowledge(prepared.operation_id, exchange_order_id='987654321', owner=owner)
        prepared_target.update(native=native, swap=swap, prepared=prepared, claim=claim)
        captured_now = await admin.fetchval('SELECT clock_timestamp()')
        values[6].wall = captured_now
        values[8]['snapshot'] = replace(values[8]['snapshot'], observed_at_ms=int(captured_now.timestamp() * 1000))
        values[7]['value'] = replace(values[7]['value'], observed_at_ms=int(captured_now.timestamp() * 1000))

    with monkeypatch.context() as patches:
        patches.setattr(test_session.FakeClock, '__init__', clock_init)
        patches.setattr(file_fixture, 'KEYS', KEYS)
        # The real fixture calls its own module's imported KEYS at creation.
        from test.hummingbot.strategy_v2.life_liquidity.storage import test_joint_cash_repository
        original_fixture = test_joint_cash_repository.fixture

        async def fixture(path, **kwargs):
            from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital_runner import setup_shared
            values = setup_shared(path / 'native', collateral=str(D('10') + min(D('0'), D('1') - entry)), budget_capacity=50)
            return await original_fixture(path, **{**kwargs, 'operator_keys': KEYS, 'shared_values': values, 'opening_long_entry': entry})

        patches.setattr(file_fixture, 'fixture', fixture)
        patches.setattr(file_fixture, 'report', numeric_report)
        joint, store, recovery, values, file_owner = await file_fixture.interrupted(tmp_path, patches, stage, before_report=before_report)
    native, swap, prepared, claim = (prepared_target[k] for k in ('native', 'swap', 'prepared', 'claim'))
    financial, cfg, owner, _, feed, capital, _, carry_feed, carry = native
    binding = store.binding
    at = recovery.clock_ms()
    trade = SwapFillObservation('trade-1', D('.25'), D('1'), 'USDT', fee, at, D('1'), at, at, 'independent_market', pnl, None)
    fill = FillSwap('original-fill', prepared.operation_id, claim.intent_id, claim.wire_id, 'LIFE-USDT', claim.side,
                    claim.session_id, claim.epoch, '987654321', D('.25'), (trade,), values[6].wall)
    fresh_now = await admin.fetchval('SELECT clock_timestamp()')
    values[6].wall = fresh_now
    facts = SpotFinancialRecoveryFacts(binding, fresh_now, D('10'), final_cash, D('0'), D('0'), ('trade-1',), (), (claim.wire_id,), (), True, True, True)
    drain = OwnerTransition('outer-drain', OwnerMode.DRAINING, file_owner)
    cut = (await store.snapshot(binding)).cut
    signature = KEYS.sign('operator', binding, 'DRAIN', drain.json(), cut, facts, issued_at=fresh_now, expires_at=fresh_now + timedelta(seconds=10))
    await store.transition_owner(drain, approval=signature, cut=cut, facts=facts)
    cut = (await store.snapshot(binding)).cut
    source = await store.legacy_applying_source(binding, facts=facts, cut=cut, joint=joint)
    retirement = RetireLegacyApplying('outer-retire', source.content_digest, cfg.binding, cfg.database_lineage)
    signed = KEYS.sign('operator', binding, 'IMPORT', retirement.json(), cut, facts, issued_at=fresh_now, expires_at=fresh_now + timedelta(seconds=10))
    await store.retire_legacy_applying(retirement, source=source, approval=signed, joint=joint)
    b = carry_feed[0]
    stamp = int(fresh_now.timestamp() * 1000)
    snapshot = replace(b.account.snapshot, sequence=b.account.snapshot.sequence + 1, observed_at_ms=stamp, spot_cash_quote=final_cash, long_base=D('.75'), collateral_quote=final_collateral)
    b = replace(b, account=replace(b.account, snapshot=snapshot, included_funding_bill_ids=() if funding is None else ('777',)), mark=replace(b.mark, sequence=b.mark.sequence + 1, observed_at_ms=stamp),
                index=replace(b.index, sequence=b.index.sequence + 1, observed_at_ms=stamp), funding=replace(b.funding, sequence=b.funding.sequence + 1, observed_at_ms=stamp, coverage_through_ms=stamp),
                terms=replace(b.terms, sequence=b.terms.sequence + 1, observed_at_ms=stamp))
    if funding is not None:
        b = replace(b, funding=replace(b.funding, payments=(FundingPayment('777', at, funding),)))
    batch = PgJointBatchRepository(swap)
    cmd = ImportLegacyApplying(source.content_digest, ApplyJointBatch('outer-pending', (), (fill,), (), ApplyFunding('outer-funding', b)))
    await admin.execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', cfg.account_id)
    # This is a halted import, not source-readiness renewal. Capture its signed
    # diagnostic cut after the original pre-fill carry source has expired. Do
    # not sign a transient ready digest just as its 1000-ms source TTL crosses.
    now = await admin.fetchval('SELECT clock_timestamp()')
    deadline = max(feed[0].observed_at_ms, carry_feed[0].mark.observed_at_ms,
                   carry_feed[0].index.observed_at_ms, carry_feed[0].funding.observed_at_ms,
                   carry_feed[0].terms.observed_at_ms) + 1001
    await asyncio.sleep(max(0, deadline - now.timestamp() * 1000) / 1000)
    cut = (await financial.observation_cut(cfg.binding)).cut
    facts = replace(facts, binding=cfg.binding, observed_at=await admin.fetchval('SELECT clock_timestamp()'))
    approval = KEYS.sign('operator', cfg.binding, 'IMPORT', cmd.json(), cut, facts, issued_at=facts.observed_at, expires_at=facts.observed_at + timedelta(seconds=10))
    importer = PgLegacyApplyingImporter(admin, batch, verifier=KEYS.verifier(), source_verifier=KEYS.verifier(), max_fact_age_ms=5000)
    return native, batch, cmd, cut, facts, approval, importer, store, source


async def apply_recovery(database, batch, command, facts, store):
    financial, cfg, owner = batch.financial, batch.runtime.config, batch.owner
    b = command.batch.funding.bundle
    now = await database[0].fetchval('SELECT clock_timestamp()')
    at = int(now.timestamp() * 1000)
    b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=b.account.snapshot.sequence + 1, observed_at_ms=at)),
                mark=replace(b.mark, sequence=b.mark.sequence + 1, observed_at_ms=at), index=replace(b.index, sequence=b.index.sequence + 1, observed_at_ms=at),
                funding=replace(b.funding, sequence=b.funding.sequence + 1, observed_at_ms=at, coverage_through_ms=at), terms=replace(b.terms, sequence=b.terms.sequence + 1, observed_at_ms=at))
    repair = ResolveJointSources('outer-recover', command.operation_id,
                                 replace(command.batch, operation_id='outer-applied', funding=replace(command.batch.funding, operation_id='outer-funding-applied', bundle=b)))
    proof = JointRepairFacts(replace(facts, observed_at=now), (('SWAP', 'trade-1'),), tuple(p.bill_id for p in command.batch.funding.bundle.funding.payments), True,
                             int(store._accounting_policy.opened_at.timestamp() * 1000), at)
    repair_cut = (await financial.observation_cut(cfg.binding)).cut
    signed = KEYS.sign('operator', cfg.binding, 'RECOVER', repair.json(), repair_cut, proof, issued_at=now, expires_at=now + timedelta(seconds=10))
    await PgJointRepairApprovalImporter(database[0], batch, verifier=KEYS.verifier(), max_fact_age_ms=5000).approve(repair, approval=signed, cut=repair_cut, facts=proof)
    result = await PgJointRepairRepository(batch).resolve(repair, cut=repair_cut, owner=owner)
    return result, repair, repair_cut


@pytest.mark.asyncio
@pytest.mark.parametrize('stage,entry,fee,funding', [(s, D('1'), D('-.001'), None) for s in
                                                     ('outer_before_cash', 'wallet_after', 'cash_done', 'wal_after', 'shared_after')]
                         + [('wallet_after', D('1.02'), D('-.001'), D('-.1')), ('cash_done', D('.98'), D('-.001'), D('.04')),
                            ('shared_after', D('1.02'), D('.001'), None)])
async def test_outer_file_swap_import_then_recover_keeps_original_trade_session_basis_and_gross_fee(database, tmp_path, monkeypatch, stage, entry, fee, funding):
    values, batch, command, cut, facts, approval, importer, store, source = await setup(database, tmp_path, monkeypatch, stage, entry=entry, fee=fee, funding=funding)
    financial, cfg, owner = values[:3]
    try:
        held = await values[5].claims(cfg.binding)
        assert not (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).trading_permission
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        result, repair, repair_cut = await apply_recovery(database, batch, command, facts, store)
        assert result.state == 'REPAIRED' and not result.trading_permission
        assert (await financial.balance(cfg.binding)).total_quote_usdt == facts.account_quote_usdt
        assert (await financial.balance(cfg.binding)).total_base_life == 10
        assert await values[5].claims(cfg.binding) == held
        loss = max(D('0'), -D('.25') * (D('1') - entry)) + max(D('0'), -fee)
        assert await database[0].fetchval("SELECT incurred FROM life_financial.budget_buckets WHERE account_id=$1 AND family='LOSS' AND scope='SESSION'", cfg.account_id) == loss
        source_loss = json.loads(dict(source.journal_images)['loss'])['events']
        if stage in ('cash_done', 'wal_after', 'shared_after'):
            assert sum((D(e['loss_quote']) for e in source_loss.values()), D('0')) == loss
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == (0 if funding is None else 1)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows WHERE account_id=$1', cfg.account_id) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts WHERE account_id=$1', cfg.account_id) == 1
        assert (await importer.lookup(command.operation_id)).state == 'RECOVERY_REQUIRED'
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['fee', 'pnl', 'quantity', 'price', 'trade_time', 'trade_id', 'wire', 'exchange', 'session', 'side', 'prepare', 'basis'])
async def test_even_a_signed_target_import_cannot_change_original_swap_economics_or_order_basis(database, tmp_path, monkeypatch, fault):
    values, _, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch, 'wallet_after')
    financial, cfg, _ = values[:3]
    try:
        fill = command.batch.swap_fills[0]
        observed = fill.fills[0]
        edits = dict(fee=dict(signed_fee=D('-.002')), pnl=dict(realized_pnl_usdt=D('.001')),
                     quantity=dict(quantity_base_life=D('.2')), price=dict(price_usdt=D('1.01')),
                     trade_time=dict(fill_at_ms=observed.fill_at_ms + 1), trade_id=dict(trade_id='different-trade'))
        if fault in edits:
            fill = replace(fill, fills=(replace(observed, **edits[fault]),),
                           cumulative_base_life=D('.2') if fault == 'quantity' else fill.cumulative_base_life)
        elif fault in ('wire', 'exchange', 'session', 'side', 'prepare'):
            fill = replace(fill, **dict(wire=dict(client_order_id='DifferentWire'), exchange=dict(exchange_order_id='123456789'),
                                        session=dict(session_id=str(UUID(int=999))), side=dict(side='BUY'), prepare=dict(prepare_operation_id='different-prepare'))[fault])
        else:
            await database[0].execute('ALTER TABLE life_financial.swap_fill_policies DISABLE TRIGGER USER')
            await database[0].execute('UPDATE life_financial.swap_fill_policies SET opening_long_entry=2 WHERE account_id=$1', cfg.account_id)
            await database[0].execute('ALTER TABLE life_financial.swap_fill_policies ENABLE TRIGGER USER')
            cut = (await financial.observation_cut(cfg.binding)).cut
        command = replace(command, batch=replace(command.batch, swap_fills=(fill,)))
        now = await database[0].fetchval('SELECT clock_timestamp()')
        facts = replace(facts, observed_at=now)
        signed = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_'):
            await importer.import_pending(command, source_store=store, approval=signed, cut=cut, facts=facts)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        for table in ('legacy_applying_imports', 'joint_financial_batches', 'swap_fill_facts', 'funding_cash_payments'):
            assert await database[0].fetchval('SELECT count(*) FROM life_financial.' + table + ' WHERE account_id=$1', cfg.account_id) == 0
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_changed_retained_swap_basis_with_rehashed_body_blocks_common_history_and_cold_import_audit(database, tmp_path, monkeypatch):
    values, batch, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch, 'cash_done')
    financial, cfg, _ = values[:3]
    try:
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await database[0].execute('ALTER TABLE life_financial.legacy_applying_imports DISABLE TRIGGER USER')
        await database[0].execute("UPDATE life_financial.legacy_applying_imports SET body=jsonb_set(body,'{source_basis,swap_opening,opening_long_entry}',to_jsonb('2'::text)) WHERE account_id=$1", cfg.account_id)
        await database[0].execute("UPDATE life_financial.legacy_applying_imports SET body_hash=encode(sha256(convert_to(body::text,'UTF8')),'hex') WHERE account_id=$1", cfg.account_id)
        await database[0].execute('ALTER TABLE life_financial.legacy_applying_imports ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        with pytest.raises(StorageError, match='PG_JOINT_BATCH_UNVERIFIED'):
            await batch.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_outer_pending_process_exit_then_cold_recovery_books_swap_fee_once_without_releasing_claim(database, tmp_path, monkeypatch, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_pending_process import exit_pending

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, _, command, _, initial_facts, _, importer, store, source = await setup(database, tmp_path, monkeypatch, 'cash_done')
    financial, cfg, owner = values[:3]
    cold = None
    try:
        async def capture():
            cut = (await financial.observation_cut(cfg.binding)).cut
            now = await database[0].fetchval('SELECT clock_timestamp()')
            facts = replace(initial_facts, observed_at=now)
            approval = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
            await importer._approve(command, store, approval, cut, facts)
            return cut, facts, approval

        cut, facts, approval = await exit_pending(cfg, database[2], owner, command, KEYS.verifier(), committed, capture)
        await financial.close()
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        batch = PgJointBatchRepository(PgSwapRepository(capital))
        restored = PgLegacyApplyingImporter(database[0], batch, verifier=KEYS.verifier(), source_verifier=KEYS.verifier(), max_fact_age_ms=5000)
        assert ((await restored.lookup(command.operation_id)) is not None) is committed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
        assert (await restored.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        result, repair, repair_cut = await apply_recovery(database, batch, command, facts, store)
        assert result.state == 'REPAIRED'
        assert (await PgJointRepairRepository(batch).resolve(repair, cut=repair_cut, owner=owner)).replayed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == D('9.999')
        assert len(await capital.claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_fill_facts WHERE account_id=$1', cfg.account_id) == 1
        assert await store.frozen_legacy_applying_source(store.binding) == source
        assert not result.trading_permission
    finally:
        if cold is not None:
            await cold.close()
        await store.close()
        await financial.close()
