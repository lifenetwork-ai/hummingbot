"""Interrupted real File funding -> native pending -> signed native accounting.

The original native opening basis is explicitly seeded by the isolated fixture.
This is not a general wallet/bootstrap importer or a configured-service test.
"""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, OwnerMode, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys, OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillPolicy
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

KEYS = OperatorKeys({'operator': b'i' * 32}, {'operator': frozenset(('IMPORT', 'RECOVER', 'DRAIN'))})


async def setup(database, tmp_path, monkeypatch, stage='wallet_after', *, cashflow=None, spot_fill=None, file_result=None, retained_cashflows_only=False, spot_prefix=False, book_native_prefix=False, spot_open_only=False, book_native_cashflow=False, book_native_funding=False, prior_funding=(), prior_after_accounting=False, book_native_parent=False, book_native_accounting_parent=False):
    from test.hummingbot.strategy_v2.life_liquidity.storage import test_spot_financial_contracts as file_fixture
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_funding import setup as funding_setup

    from hummingbot.strategy_v2.life_liquidity.storage.capital import (
        ApprovedSpotCashflow,
        CashflowSpot,
        SpotAccountingPolicy,
    )
    from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashEvent, JointCashSpot
    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
    from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryPolicy
    initial = None
    if spot_fill is not None:
        with monkeypatch.context() as patches:
            patches.setattr(file_fixture, 'NOW', await database[0].fetchval('SELECT clock_timestamp()'))
            initial = file_fixture.create_fixture(tmp_path)
    values, funding, payment = await funding_setup(database, carry_options=dict(refresh_initial_facts=True),
                                                   spot_options=dict(configuration_body=lambda _: {'operator_verifier': KEYS.verifier().policy(),
                                                                                                   'legacy_source_verifier': KEYS.verifier().policy()},
                                                                     **({} if initial is None else {'original_session_id': initial[2].session_id})))
    financial, cfg, owner = values[:3]
    admin = database[0]
    if prior_funding:
        from hummingbot.strategy_v2.life_liquidity.carry_monitor import FundingPayment
        original = payment.bundle.funding.payments[0]
        earlier = tuple(FundingPayment(str(770 + i), original.settled_at_ms - len(prior_funding) + i, amount)
                        for i, amount in enumerate(prior_funding))
        b = payment.bundle
        amount = sum(prior_funding, D('0'))
        payment = replace(payment, bundle=replace(b,
                                                  account=replace(b.account, included_funding_bill_ids=tuple(p.bill_id for p in (*earlier, original)),
                                                                  snapshot=replace(b.account.snapshot, spot_cash_quote=b.account.snapshot.spot_cash_quote + amount,
                                                                                   collateral_quote=b.account.snapshot.collateral_quote + amount)),
                                                  funding=replace(b.funding, payments=(*earlier, original))))
    await admin.execute('INSERT INTO life_financial.joint_batch_policies VALUES($1,$2,32,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    await admin.execute('INSERT INTO life_financial.joint_repair_policies VALUES($1,$2,64,64,1048576,5000)', cfg.account_id, cfg.binding.policy_hash)
    await admin.execute('INSERT INTO life_financial.legacy_applying_policies VALUES($1,$2,4,4194304,5000)', cfg.account_id, cfg.binding.policy_hash)
    if book_native_funding:
        original_payment = replace(payment, operation_id='native-prior-funding')
        if book_native_parent:
            prior_batch = ApplyJointBatch('native-prior-parent', (), (), (), original_payment)
            assert (await PgJointBatchRepository(funding.swap).apply(prior_batch,
                                                                     cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
        else:
            assert (await funding.apply_funding(original_payment, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
    now = await admin.fetchval('SELECT clock_timestamp()')
    opened = await admin.fetchval('SELECT opened_at FROM life_financial.spot_capital_policies WHERE account_id=$1', cfg.account_id)
    with monkeypatch.context() as patches:
        patches.setattr(file_fixture, 'NOW', opened)
        files, _, file_owner = initial or file_fixture.create_fixture(tmp_path)
    binding = replace(cfg.binding, lineage=uuid4())
    clock = [now]
    flows, approvals = (), ()
    base, quote = D('5'), D('9.9') + sum(prior_funding, D('0'))
    if cashflow is not None:
        currency, amount, price = cashflow
        await admin.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
        at = int(now.timestamp() * 1000)
        approvals = (ApprovedSpotCashflow('101', currency, amount, at),)
        flows = (CashflowSpot('file-transfer', '101', currency, amount, at, price, at, at,
                              'quote_unit' if currency == 'USDT' else 'independent_market', now),)
        base += amount if currency == 'LIFE' else D('0')
        quote += amount if currency == 'USDT' else D('0')
        b = payment.bundle
        payment = replace(payment, bundle=replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot,
                          spot_life_base=base, spot_cash_quote=quote, collateral_quote=b.account.snapshot.collateral_quote + (amount if currency == 'USDT' else D('0'))))))
        await admin.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                            cfg.account_id, '101', currency, amount, at, b'synthetic-preverified-import')
    accounting = SpotAccountingPolicy(D('5'), D('10'), D('1'), opened, '100', approvals)

    async def open_file(*, create=False):
        return await open_legacy_spot(files, binding=binding, limits=file_fixture.CAPACITY,
                                      risk_limits=file_fixture.RISK, subsidy_policy=file_fixture.BUDGET, loss_policy=file_fixture.BUDGET,
                                      config_version=1, account_lock_root=tmp_path / 'locks', owner=file_owner if create else None,
                                      create_binding=create, clock=lambda: clock[0], fill_policy=SpotFillPolicy(5000),
                                      accounting_policy=accounting, recovery_policy=SpotFinancialRecoveryPolicy(5000), operator_keys=KEYS)

    store = await open_file(create=True)
    prior_events = ()

    async def complete_history():
        nonlocal prior_events
        if not prior_funding:
            return
        before = (await store.snapshot(binding)).balance
        running_cash = before.total_quote_usdt
        for i, previous in enumerate(payment.bundle.funding.payments[:-1]):
            prior_events += (JointCashEvent(f'JOINT:{previous.settled_at_ms}:FUNDING:{previous.bill_id}', previous.signed_payment_quote),)
            running_cash += previous.signed_payment_quote
            old = JointCashSpot(f'file-prior-funding-{i}', binding, format(i + 1, '064x'), clock[0],
                                prior_events, before.total_base_life, running_cash, True, True, 'okx_joint_reconciled')
            assert (await store.apply_joint_cash(old, cut=(await store.snapshot(binding)).cut, owner=file_owner)).state == 'APPLIED'
    if not prior_after_accounting:
        await complete_history()
    fills = ()
    if spot_fill is not None:
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import (
            native_claim,
        )
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import filled

        from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
        side, fee_currency = spot_fill
        if cashflow is None:
            await admin.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
        stamp = int((await admin.fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
        values[4][0] = replace(values[4][0], sequence=values[4][0].sequence + 1, observed_at_ms=stamp)
        b = values[7][0]
        values[7][0] = replace(b, account=replace(b.account, snapshot=values[4][0]),
                               mark=replace(b.mark, sequence=b.mark.sequence + 1, observed_at_ms=stamp), index=replace(b.index, sequence=b.index.sequence + 1, observed_at_ms=stamp),
                               funding=replace(b.funding, sequence=b.funding.sequence + 1, observed_at_ms=stamp, coverage_through_ms=stamp), terms=replace(b.terms, sequence=b.terms.sequence + 1, observed_at_ms=stamp))
        claim = replace(native_claim(owner, 'intent1', side=side, amount='2', price='1'), wire_id='wire1')
        assert (await values[5].reserve(ReserveCapital('spotclaim', claim), await values[5].cut(cfg.binding), owner)).decision.allowed
        assert (await values[8].check_carry(cfg.binding)).allowed
        changes = dict(side=side, risk_epoch=1, fee_buffer_base_life=D('0'), fee_buffer_quote_usdt=D('.002'))
        await financial.prepare(file_fixture.order(owner, **changes), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        await financial.arm('operation1', cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        await store.prepare(file_fixture.order(file_owner, **changes), cut=(await store.snapshot(binding)).cut, owner=file_owner)
        await store.arm('operation1', cut=(await store.snapshot(binding)).cut, owner=file_owner)
        now = await admin.fetchval('SELECT clock_timestamp()')
        clock[0] = now
        if not spot_open_only:
            f = filled(file_owner, now, side=side)
            f = replace(f, fills=(replace(f.fills[0], fee_currency=fee_currency),))
            if spot_prefix:
                prefix = replace(f.fills[0], trade_id='trade0', quantity_base_life=D('.5'), signed_fee=D('-.001'))
                first = replace(f, operation_id='file-prefix', cumulative_base_life=D('.5'), fills=(prefix,))
                await store.apply_fills(first, cut=(await store.snapshot(binding)).cut, owner=file_owner)
                if book_native_prefix and not book_native_accounting_parent:
                    assert (await financial.apply_fills(first, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
                f = replace(f, fills=(prefix, replace(f.fills[0], quantity_base_life=D('.5'), signed_fee=D('-.019'))))
            fills = (f,)
            base += (D('1') if side == 'BUY' else D('-1')) + (D('-.02') if fee_currency == 'LIFE' else D('0'))
            quote += (D('-1') if side == 'BUY' else D('1')) + (D('-.02') if fee_currency == 'USDT' else D('0'))
            b = payment.bundle
            payment = replace(payment, bundle=replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot,
                              spot_life_base=base, spot_cash_quote=quote, collateral_quote=quote))))
    if flows:
        # A core command has no outer recovery authority. Its transfer must
        # already have an APPLIED receipt before a new joint command starts.
        # Partially written transfer + joint recovery uses the outer profile.
        await store.apply_cashflow(flows[0], cut=(await store.snapshot(binding)).cut, owner=file_owner)
        if book_native_cashflow and not book_native_accounting_parent:
            prior = replace(flows[0], operation_id='native-prior-transfer')
            assert (await financial.apply_cashflow(prior, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
    if prior_after_accounting:
        await complete_history()
    if book_native_accounting_parent:
        # One independently declared final fake provider snapshot for the
        # ORIGINAL atomic prefix batch, from opening + its exact leaves.
        # Do not use the partial File wallet as a native opening balance.
        received = await admin.fetchval('SELECT clock_timestamp()')
        stamp = int(received.timestamp() * 1000)
        original_spot = replace(first, operation_id='native-prior-spot', observed_at=received)
        original_flows = tuple(replace(v, operation_id='native-prior-transfer') for v in flows)
        original_base, original_cash = D('5'), D('10')
        for item in original_spot.fills:
            original_base += item.quantity_base_life if original_spot.side == 'BUY' else -item.quantity_base_life
            original_cash += -item.quantity_base_life * item.price_usdt if original_spot.side == 'BUY' else item.quantity_base_life * item.price_usdt
            original_base += item.signed_fee if item.fee_currency == 'LIFE' else D('0')
            original_cash += item.signed_fee if item.fee_currency == 'USDT' else D('0')
        for item in original_flows:
            original_base += item.signed_amount if item.currency == 'LIFE' else D('0')
            original_cash += item.signed_amount if item.currency == 'USDT' else D('0')
        original_cash += sum((p.signed_payment_quote for p in payment.bundle.funding.payments), D('0'))
        b = payment.bundle
        observed = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot,
                                                                          sequence=b.account.snapshot.sequence + 1, observed_at_ms=stamp,
                                                                          spot_life_base=original_base, spot_cash_quote=original_cash, collateral_quote=original_cash)),
                           mark=replace(b.mark, sequence=b.mark.sequence + 1, observed_at_ms=stamp),
                           index=replace(b.index, sequence=b.index.sequence + 1, observed_at_ms=stamp),
                           funding=replace(b.funding, sequence=b.funding.sequence + 1, observed_at_ms=stamp, coverage_through_ms=stamp),
                           terms=replace(b.terms, sequence=b.terms.sequence + 1, observed_at_ms=stamp))
        parent = ApplyJointBatch('native-prior-parent', (original_spot,), (), original_flows,
                                 replace(payment, operation_id='native-prior-funding', bundle=observed))
        await admin.execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', cfg.account_id)
        assert (await PgJointBatchRepository(funding.swap).apply(parent,
                                                                 cut=(await financial.observation_cut(cfg.binding)).cut, owner=owner)).state == 'APPLIED'
    paid = payment.bundle.funding.payments[-1]
    joint = JointCashSpot('file-joint', binding, 'c' * 64, now,
                          (*prior_events, JointCashEvent(f'JOINT:{paid.settled_at_ms}:FUNDING:{paid.bill_id}', paid.signed_payment_quote)),
                          base, quote, True, True, 'okx_joint_reconciled',
                          spot_cashflows=() if retained_cashflows_only else flows, spot_fills=fills)
    target, method = ((store._ledger, 'apply_joint_snapshot') if stage.startswith('wallet')
                      else (store._loss, 'record') if stage == 'loss_after'
                      else (store._subsidy, 'record_fill_floor') if stage == 'floor_after' else (store, '_publish_joint_cash'))
    original = getattr(target, method)

    def crash(*args, **kwargs):
        if stage == 'receipt' and not args[0].startswith('joint-done:'):
            return original(*args, **kwargs)
        if stage in ('wallet_after', 'loss_after', 'floor_after'):
            original(*args, **kwargs)
        raise OSError('synthetic File saga interruption')

    joint_cut = (await store.snapshot(binding)).cut
    with monkeypatch.context() as patches:
        patches.setattr(target, method, crash)
        with pytest.raises(CommitUnknown):
            await store.apply_joint_cash(joint, cut=joint_cut, owner=file_owner)
    await store.close()
    store = await open_file()
    if file_result is not None:
        # Fork the disposable File history sequentially at its exact crash
        # bytes. Run the real File resume to obtain independent parity values,
        # then restore that crash branch for signed source retirement. No
        # configured path, native DB money or retirement fence is rewound.
        images = {p: p.read_bytes() for p in vars(files).values() if p is not None}
        await store.resume_joint_cash(joint, cut=joint_cut, owner=file_owner)
        expected = await store.snapshot(binding)
        file_result.update(balance=expected.balance, budget=expected.budget)
        await store.close()
        for p, raw in images.items():
            p.write_bytes(raw)
        store = await open_file()
    now = await admin.fetchval('SELECT clock_timestamp()')
    clock[0] = now
    facts = SpotFinancialRecoveryFacts(binding, now, base, quote, D('0'), D('0'), tuple(f.trade_id for v in fills for f in v.fills),
                                       tuple(v.bill_id for v in flows), ('wire1',) if spot_open_only else tuple(v.client_order_id for v in fills), (), True, True, True)
    drain = OwnerTransition('file-drain', OwnerMode.DRAINING, file_owner)
    cut = (await store.snapshot(binding)).cut
    signature = KEYS.sign('operator', binding, 'DRAIN', drain.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
    await store.transition_owner(drain, approval=signature, cut=cut, facts=facts)
    cut = (await store.snapshot(binding)).cut
    source = await store.legacy_applying_source(binding, facts=facts, cut=cut)
    retire = RetireLegacyApplying('file-retire', source.content_digest, cfg.binding, cfg.database_lineage)
    signature = KEYS.sign('operator', binding, 'IMPORT', retire.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
    await store.retire_legacy_applying(retire, source=source, approval=signature)
    batch = PgJointBatchRepository(funding.swap)
    command = ImportLegacyApplying(source.content_digest, ApplyJointBatch('legacy-pending', fills, (), flows, payment))
    await admin.execute('UPDATE life_financial.accounts SET halted=true WHERE account_id=$1', cfg.account_id)
    cut = (await financial.observation_cut(cfg.binding)).cut
    now = await admin.fetchval('SELECT clock_timestamp()')
    facts = replace(facts, binding=cfg.binding, observed_at=now)
    approval = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
    importer = PgLegacyApplyingImporter(admin, batch, verifier=KEYS.verifier(), source_verifier=KEYS.verifier(), max_fact_age_ms=5000)
    return values, batch, command, cut, facts, approval, importer, store, source


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'receipt'])
async def test_actual_interrupted_file_import_then_signed_recovery_books_original_bill_once(database, tmp_path, monkeypatch, stage):
    values, batch, command, cut, facts, approval, importer, store, source = await setup(database, tmp_path, monkeypatch, stage)
    financial, cfg, owner = values[:3]
    try:
        before_file = await store.snapshot(store.binding)
        assert ImportLegacyApplying.from_json(command.json()) == command
        holds = await values[5].claims(cfg.binding)
        assert not (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).trading_permission
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await batch.lookup(command.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert (await importer.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).replayed
        assert (await importer.lookup(command.operation_id)).replayed
        assert await store.frozen_legacy_applying_source(store.binding) == source
        assert (await store.snapshot(store.binding)).balance == before_file.balance
        b = command.batch.funding.bundle
        now = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(now.timestamp() * 1000)
        fresh = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=3, observed_at_ms=at)),
                        mark=replace(b.mark, sequence=3, observed_at_ms=at), index=replace(b.index, sequence=3, observed_at_ms=at),
                        funding=replace(b.funding, sequence=3, observed_at_ms=at, coverage_through_ms=at), terms=replace(b.terms, sequence=3, observed_at_ms=at))
        replacement = replace(command.batch, operation_id='legacy-applied', funding=replace(command.batch.funding, operation_id='funding-applied', bundle=fresh))
        repair = ResolveJointSources('legacy-recover', command.operation_id, replacement)
        opening = await database[0].fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies WHERE account_id=$1', cfg.account_id)
        account = replace(facts, observed_at=now, accepted_wire_ids=('SwapWire1',))
        recovery_facts = JointRepairFacts(account, (), ('777',), True, opening, at)
        repair_cut = (await financial.observation_cut(cfg.binding)).cut
        signed = KEYS.sign('operator', cfg.binding, 'RECOVER', repair.json(), repair_cut, recovery_facts,
                           issued_at=now, expires_at=now + timedelta(seconds=10))
        await PgJointRepairApprovalImporter(database[0], batch, verifier=KEYS.verifier(), max_fact_age_ms=5000).approve(
            repair, approval=signed, cut=repair_cut, facts=recovery_facts)
        repo = PgJointRepairRepository(batch)
        assert (await repo.resolve(repair, cut=repair_cut, owner=owner)).state == 'REPAIRED'
        assert (await repo.resolve(repair, cut=repair_cut, owner=owner)).replayed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.9')
        assert (await batch.lookup(command.operation_id, cfg.binding)).state == 'RECOVERY_REQUIRED'
        assert await values[5].claims(cfg.binding) == holds
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows WHERE account_id=$1', cfg.account_id) == 0
        assert (await importer.lookup(command.operation_id)).replayed
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'role', 'liability', 'cash', 'incomplete', 'source_digest', 'bill', 'basis', 'target_active', 'missing_source', 'stale_cut', 'source_verifier'])
async def test_import_denials_cannot_publish_pending_or_adopt_partial_file_cash(database, tmp_path, monkeypatch, fault):
    values, batch, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch)
    financial, cfg, _ = values[:3]
    try:
        if fault == 'signature':
            approval = replace(approval, signature='0' * 128)
        elif fault == 'role':
            approval = replace(approval, role='RECOVER')
        elif fault == 'liability':
            facts = replace(facts, liability_quote_usdt=D('1'))
        elif fault == 'cash':
            facts = replace(facts, account_quote_usdt=D('10'))
        elif fault == 'incomplete':
            facts = replace(facts, runner_scope_complete=False)
        elif fault == 'source_digest':
            command = replace(command, source_digest='d' * 64)
        elif fault == 'bill':
            b = command.batch.funding.bundle
            b = replace(b, funding=replace(b.funding, payments=(replace(b.funding.payments[0], bill_id='888'),)))
            command = replace(command, batch=replace(command.batch, funding=replace(command.batch.funding, bundle=b)))
        elif fault == 'basis':
            await database[0].execute('ALTER TABLE life_financial.spot_capital_policies DISABLE TRIGGER USER')
            await database[0].execute("UPDATE life_financial.spot_capital_policies SET opened_at=opened_at-interval '1 second' WHERE account_id=$1", cfg.account_id)
            await database[0].execute('ALTER TABLE life_financial.spot_capital_policies ENABLE TRIGGER USER')
            cut = (await financial.observation_cut(cfg.binding)).cut
        elif fault == 'target_active':
            await database[0].execute('UPDATE life_financial.accounts SET halted=false WHERE account_id=$1', cfg.account_id)
            cut = (await financial.observation_cut(cfg.binding)).cut
        elif fault == 'source_verifier':
            importer.source_verifier = OperatorKeys({'operator': b'x' * 32}, KEYS.roles).verifier()
        elif fault == 'stale_cut':
            cut = replace(cut, checkpoint=replace(cut.checkpoint, state_digest='d' * 64))
        if fault not in ('signature', 'role'):
            approval = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts,
                                 issued_at=approval.issued_at, expires_at=approval.expires_at)
        before = await financial.balance(cfg.binding)
        with pytest.raises(StorageError):
            await importer.import_pending(command, source_store=None if fault == 'missing_source' else store, approval=approval, cut=cut, facts=facts)
        assert await financial.balance(cfg.binding) == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.legacy_applying_imports WHERE account_id=$1', cfg.account_id) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_financial_batches WHERE account_id=$1', cfg.account_id) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 0
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['reply', 'backend'])
@pytest.mark.parametrize('boundary', ['source', 'pending'])
@pytest.mark.parametrize('committed', [False, True])
async def test_lost_import_commit_cold_original_id_never_duplicates_source_or_money(database, tmp_path, monkeypatch, boundary, committed, fault):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, batch, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch)
    financial, cfg, owner = values[:3]
    cold, connection = None, None
    admin = await asyncpg.connect(database[1])
    try:
        target = importer if boundary == 'source' else financial._runtime
        original = target._commit

        async def lost(tx):
            pid = tx._connection.get_server_pid()
            if committed:
                await original(tx)
            if fault == 'backend':
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
            raise ConnectionError('synthetic import COMMIT reply lost')

        with monkeypatch.context() as patches:
            patches.setattr(target, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await financial.close()
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        restored_batch = PgJointBatchRepository(PgSwapRepository(capital))
        connection = await asyncpg.connect(database[2])
        restored = PgLegacyApplyingImporter(connection, restored_batch, verifier=KEYS.verifier(), source_verifier=KEYS.verifier(), max_fact_age_ms=5000)
        found = await restored.lookup(command.operation_id)
        assert (found is not None) is (boundary == 'pending' and committed)
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
        receipt = await restored.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        assert receipt.state == 'RECOVERY_REQUIRED' and receipt.replayed is (boundary == 'pending' and committed)
        assert (await restored.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).replayed
        assert await connection.fetchval('SELECT count(*) FROM life_financial.legacy_applying_imports WHERE account_id=$1', cfg.account_id) == 1
        assert await connection.fetchval('SELECT count(*) FROM life_financial.joint_financial_batches WHERE account_id=$1', cfg.account_id) == 1
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await store.close()
        await admin.close()
        if connection is not None:
            await connection.close()
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
async def test_missing_or_changed_import_proof_blocks_common_financial_history_and_release_readiness(database, tmp_path, monkeypatch):
    values, batch, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch)
    financial, cfg, _ = values[:3]
    try:
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        await database[0].execute('ALTER TABLE life_financial.legacy_applying_imports DISABLE TRIGGER USER')
        await database[0].execute("UPDATE life_financial.legacy_applying_imports SET body=jsonb_set(body,'{payload,source_digest}',to_jsonb($2::text)) WHERE account_id=$1", cfg.account_id, 'd' * 64)
        await database[0].execute('ALTER TABLE life_financial.legacy_applying_imports ENABLE TRIGGER USER')
        with pytest.raises(StorageError, match='LEGACY_APPLYING_UNVERIFIED'):
            await importer.lookup(command.operation_id)
        with pytest.raises(StorageError, match='JOINT_BATCH_UNVERIFIED'):
            await batch.lookup(command.operation_id, cfg.binding)
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'key_policy', 'owner', 'expiry', 'bound', 'file_changed'])
async def test_physical_import_publication_rechecks_original_context_and_fails_closed(database, tmp_path, monkeypatch, fault):
    import asyncio
    values, batch, command, cut, facts, approval, importer, store, source = await setup(database, tmp_path, monkeypatch)
    financial, cfg, _ = values[:3]
    original = importer._commit
    committed_attempt = []
    try:
        if fault == 'signature':
            approval = replace(approval, signature='0' * 128)
        elif fault == 'key_policy':
            importer.verifier = OperatorKeys({'operator': b'x' * 32}, KEYS.roles).verifier()
        elif fault == 'bound':
            await database[0].execute('ALTER TABLE life_financial.legacy_applying_policies DISABLE TRIGGER USER')
            await database[0].execute('UPDATE life_financial.legacy_applying_policies SET max_total_bytes=1 WHERE account_id=$1', cfg.account_id)
            await database[0].execute('ALTER TABLE life_financial.legacy_applying_policies ENABLE TRIGGER USER')
        elif fault == 'file_changed':
            path = store._path.parent / 'legacy_applying_sources' / (source.content_digest + '.json')
            path.write_bytes(path.read_bytes() + b'\n')
        else:
            if fault == 'expiry':
                now = await database[0].fetchval('SELECT clock_timestamp()')
                facts = replace(facts, observed_at=now)
                approval = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), cut, facts,
                                     issued_at=now, expires_at=now + timedelta(milliseconds=300))

            async def changed(tx):
                committed_attempt.append(True)
                if fault == 'owner':
                    await tx._connection.execute('UPDATE life_financial.accounts SET owner_generation=gen_random_uuid() WHERE account_id=$1', cfg.account_id)
                else:
                    await asyncio.sleep(.35)  # Real expiry; no clock/settings change.
                await original(tx)
            monkeypatch.setattr(importer, '_commit', changed)
        with pytest.raises(StorageError):
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        if fault in ('owner', 'expiry'):
            assert committed_attempt == [True]
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.legacy_applying_imports WHERE account_id=$1', cfg.account_id) == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.joint_financial_batches WHERE account_id=$1', cfg.account_id) == 0
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_runtime_and_reporter_have_no_source_proof_or_import_approval_privileges(database, tmp_path, monkeypatch):
    values, _, _, _, _, _, _, store, _ = await setup(database, tmp_path, monkeypatch)
    financial = values[0]
    try:
        for role in ('life_pg2_runtime', 'life_pg2_reporter'):
            for table in ('legacy_applying_policies', 'legacy_applying_imports'):
                assert not await database[0].fetchval("SELECT has_table_privilege($1,$2,'SELECT,INSERT,UPDATE,DELETE')", role, 'life_financial.' + table)
            for function in ('legacy_applying_verified(uuid)', 'legacy_applying_fresh(uuid,text)', 'legacy_applying_commit_guard()'):
                assert not await database[0].fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, 'life_financial.' + function)
            assert await database[0].fetchval("SELECT has_function_privilege($1,'life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)','EXECUTE')", role) is (role == 'life_pg2_runtime')
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_process_exit_around_pending_commit_preserves_original_bill_and_source(database, tmp_path, monkeypatch, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_pending_process import exit_pending

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
    values, _, command, cut, facts, approval, importer, store, source = await setup(database, tmp_path, monkeypatch)
    financial, cfg, owner = values[:3]
    cold = None
    try:
        async def capture():
            fresh_cut = (await financial.observation_cut(cfg.binding)).cut
            now = await database[0].fetchval('SELECT clock_timestamp()')
            fresh_facts = replace(facts, observed_at=now)
            signed = KEYS.sign('operator', cfg.binding, 'IMPORT', command.json(), fresh_cut, fresh_facts,
                               issued_at=now, expires_at=now + timedelta(seconds=10))
            await importer._approve(command, store, signed, fresh_cut, fresh_facts)
            return fresh_cut, fresh_facts, signed

        cut, facts, approval = await exit_pending(cfg, database[2], owner, command, KEYS.verifier(), committed, capture)
        await financial.close()
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        batch = PgJointBatchRepository(PgSwapRepository(capital))
        restored = PgLegacyApplyingImporter(database[0], batch, verifier=KEYS.verifier(), source_verifier=KEYS.verifier(), max_fact_age_ms=5000)
        assert ((await restored.lookup(command.operation_id)) is not None) is committed
        assert (await cold.balance(cfg.binding)).total_quote_usdt == 10
        receipt = await restored.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)
        assert receipt.replayed is committed and receipt.state == 'RECOVERY_REQUIRED'
        assert await store.frozen_legacy_applying_source(store.binding) == source
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 0
        assert len(await capital.claims(cfg.binding)) == 1
    finally:
        if cold is not None:
            await cold.close()
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_unpublished_import_cannot_be_bypassed_by_an_ordinary_financial_writer(database, tmp_path, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import PgFundingRepository
    values, batch, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch)
    financial, cfg, _ = values[:3]
    try:
        await importer._approve(command, store, approval, cut, facts)
        with pytest.raises(StorageError, match='LEGACY_APPLYING_PENDING_REQUIRED'):
            await batch.apply(command.batch, cut=cut, owner=batch.owner)
        with pytest.raises(StorageError, match='LEGACY_APPLYING_PENDING_REQUIRED'):
            await batch.apply(replace(command.batch, operation_id='bypass-import'), cut=cut, owner=batch.owner)
        with pytest.raises(StorageError, match='LEGACY_APPLYING_PENDING_REQUIRED'):
            await PgFundingRepository(batch.swap).apply_funding(command.batch.funding, cut=cut, owner=batch.owner)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 0
        assert (await importer.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_a_second_import_identity_cannot_create_an_orphan_approval_and_deadlock_recovery(database, tmp_path, monkeypatch):
    values, batch, command, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch)
    financial, cfg, _ = values[:3]
    try:
        await importer._approve(command, store, approval, cut, facts)
        other = replace(command, batch=replace(command.batch, operation_id='different-source-import'))
        signed = KEYS.sign('operator', cfg.binding, 'IMPORT', other.json(), cut, facts,
                           issued_at=approval.issued_at, expires_at=approval.expires_at)
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_ORIGIN_ALREADY_IMPORTED'):
            await importer._approve(other, store, signed, cut, facts)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.legacy_applying_imports WHERE account_id=$1', cfg.account_id) == 1
        assert (await importer.import_pending(command, source_store=None, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
    finally:
        await store.close()
        await financial.close()


@pytest.mark.asyncio
async def test_expired_unpublished_source_needs_explicit_signed_successor_not_history_rewrite(database, tmp_path, monkeypatch):
    import asyncio
    values, batch, old, cut, facts, approval, importer, store, _ = await setup(database, tmp_path, monkeypatch)
    financial, cfg, owner = values[:3]
    try:
        await importer._approve(old, store, approval, cut, facts)
        original = await database[0].fetchval('SELECT body FROM life_financial.legacy_applying_imports WHERE account_id=$1', cfg.account_id)
        await asyncio.sleep(5.05)  # Existing max fact age is 5000 ms.
        with pytest.raises(StorageError):
            await importer.import_pending(old, source_store=None, approval=approval, cut=cut, facts=facts)
        now = await database[0].fetchval('SELECT clock_timestamp()')
        facts = replace(facts, observed_at=now)
        cut = (await financial.observation_cut(cfg.binding)).cut
        renewed = replace(old, batch=replace(old.batch, operation_id='renewed-pending'), supersedes_operation_id=old.operation_id)
        assert ImportLegacyApplying.from_json(renewed.json()) == renewed
        signed = KEYS.sign('operator', cfg.binding, 'IMPORT', renewed.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
        assert (await importer.import_pending(renewed, source_store=None, approval=signed, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert await database[0].fetchval('SELECT body FROM life_financial.legacy_applying_imports WHERE account_id=$1 AND operation_id=$2', cfg.account_id, old.operation_id) == original
        assert (await importer.lookup(old.operation_id)) is None
        assert (await importer.lookup(renewed.operation_id)).state == 'RECOVERY_REQUIRED'
        # The signed successor cannot itself branch or replace a published root.
        branch = replace(renewed, batch=replace(renewed.batch, operation_id='branched-pending'))
        sign_branch = KEYS.sign('operator', cfg.binding, 'IMPORT', branch.json(), cut, facts, issued_at=now, expires_at=now + timedelta(seconds=10))
        with pytest.raises(StorageError):
            await importer._approve(branch, None, sign_branch, cut, facts)
        published = replace(branch, supersedes_operation_id=renewed.operation_id)
        current = (await financial.observation_cut(cfg.binding)).cut
        sign_published = KEYS.sign('operator', cfg.binding, 'IMPORT', published.json(), current, facts,
                                   issued_at=now, expires_at=now + timedelta(seconds=10))
        with pytest.raises(StorageError, match='PG_LEGACY_APPLYING_SUCCESSOR_INVALID'):
            await importer._approve(published, None, sign_published, current, facts)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.legacy_applying_imports WHERE account_id=$1', cfg.account_id) == 2
        assert len(await values[5].claims(cfg.binding)) == 1
        assert not (await importer.import_pending(renewed, source_store=None, approval=signed, cut=cut, facts=facts)).trading_permission
        now = await database[0].fetchval('SELECT clock_timestamp()')
        at = int(now.timestamp() * 1000)
        b = renewed.batch.funding.bundle
        b = replace(b, account=replace(b.account, snapshot=replace(b.account.snapshot, sequence=3, observed_at_ms=at)),
                    mark=replace(b.mark, sequence=3, observed_at_ms=at), index=replace(b.index, sequence=3, observed_at_ms=at),
                    funding=replace(b.funding, sequence=3, observed_at_ms=at, coverage_through_ms=at,
                                    settlement_ms=at + 10000, next_settlement_ms=at + 10000 + 4 * 3600000), terms=replace(b.terms, sequence=3, observed_at_ms=at))
        repair = ResolveJointSources('renewal-recover', renewed.operation_id,
                                     replace(renewed.batch, operation_id='renewal-applied', funding=replace(renewed.batch.funding, operation_id='renewal-funding', bundle=b)))
        opening = await database[0].fetchval('SELECT floor(extract(epoch FROM opened_at)*1000)::bigint FROM life_financial.spot_capital_policies WHERE account_id=$1', cfg.account_id)
        proof = JointRepairFacts(replace(facts, observed_at=now, accepted_wire_ids=('SwapWire1',)), (), ('777',), True, opening, at)
        repair_cut = (await financial.observation_cut(cfg.binding)).cut
        recover_approval = KEYS.sign('operator', cfg.binding, 'RECOVER', repair.json(), repair_cut, proof, issued_at=now, expires_at=now + timedelta(seconds=10))
        await PgJointRepairApprovalImporter(database[0], batch, verifier=KEYS.verifier(), max_fact_age_ms=5000).approve(
            repair, approval=recover_approval, cut=repair_cut, facts=proof)
        repo = PgJointRepairRepository(batch)
        assert (await repo.resolve(repair, cut=repair_cut, owner=owner)).state == 'REPAIRED'
        assert (await repo.resolve(repair, cut=repair_cut, owner=owner)).replayed
        assert (await financial.balance(cfg.binding)).total_quote_usdt == D('9.9')
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.funding_cash_payments WHERE account_id=$1', cfg.account_id) == 1
        assert (await importer.lookup(old.operation_id)) is None
        assert (await importer.lookup(renewed.operation_id)).state == 'RECOVERY_REQUIRED'
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.operations WHERE account_id=$1 AND operation_id=$2', cfg.account_id, old.operation_id) == 0
    finally:
        await store.close()
        await financial.close()
