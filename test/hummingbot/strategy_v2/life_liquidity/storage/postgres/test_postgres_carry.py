"""Synthetic independent carry streams on disposable PostgreSQL, never live feeds."""
import json
from dataclasses import asdict, replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import (
    native_claim,
    setup as shared_setup,
)
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import policy as shared_policy

import pytest

from hummingbot.strategy_v2.life_liquidity.carry_monitor import (
    CarryAccount,
    CarryBundle,
    CarryFunding,
    CarryMonitor,
    CarryPolicy,
    CarryPrice,
    CarryTerms,
    FundingPayment,
)
from hummingbot.strategy_v2.life_liquidity.shared_capital import SharedCapitalAuthority, _json
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.carry import PgCarryRepository
from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital


async def setup(database, *, max_age_ms=1000, **changes):
    financial, cfg, owner, capital_policy, account_feed, capital = await shared_setup(database, configured=shared_policy(max_age_ms=max_age_ms))
    at = account_feed[0].observed_at_ms
    policy = replace(CarryPolicy('12345', 'LIFE-USDT-SWAP', at - 10000, max_age_ms, 500,
                                 4 * 3600000, 16 * 3600000, D('.002'), D('10'), D('100')), **changes)
    body = _json(asdict(policy))
    await database[0].execute('INSERT INTO life_financial.carry_policies VALUES($1,$2,$3::jsonb,$4,64)',
                              cfg.account_id, cfg.binding.policy_hash, json.dumps(body),
                              fingerprint({'policy': body, 'max_payments': 64}))
    await capital.check_capital(cfg.binding)
    feed = [CarryBundle(CarryAccount(account_feed[0], (), True, True),
                        CarryPrice(policy.instrument, 1, at, D('1'), 'okx_mark'),
                        CarryPrice(policy.instrument, 1, at, D('1'), 'okx_index'),
                        CarryFunding('12345', policy.instrument, 1, at, D('.001'), at + 10000,
                                     at + 10000 + 4 * 3600000, policy.anchor_ms, at, (), True, 'okx_funding'),
                        CarryTerms('12345', policy.instrument, 1, at, D('.001'), D('.002'),
                                   capital_policy.tiers, True, 'okx_account_terms'))]
    repo = PgCarryRepository(capital, observation=lambda: feed[0], max_rows=64, max_bytes=1048576)
    return financial, cfg, owner, capital_policy, account_feed, capital, policy, feed, repo


@pytest.mark.asyncio
@pytest.mark.parametrize('interval', [1, 2, 4, 8])
async def test_native_carry_matches_file_without_crediting_expected_funding(database, tmp_path, interval):
    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database)
    b = feed[0]
    feed[0] = replace(b, funding=replace(b.funding, next_settlement_ms=b.funding.settlement_ms + interval * 3600000))
    try:
        authority = SharedCapitalAuthority(tmp_path / 'capital.json', policy=cap_policy,
                                           observation=lambda: account_feed[0], clock_ms=lambda: account_feed[0].observed_at_ms, create=True)
        monitor = CarryMonitor(tmp_path / 'carry.json', policy=policy, capital=authority,
                               observation=lambda: feed[0], clock_ms=lambda: account_feed[0].observed_at_ms, create=True)
        before = await financial.balance(cfg.binding)
        actual = await repo.check_carry(cfg.binding)
        assert actual == monitor.check() and actual.allowed
        assert actual.funding_events == 1 + 4 // interval
        assert await financial.balance(cfg.binding) == before
        assert not (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('sign', ['.001', '-.001'])
async def test_prospective_swap_uses_worst_price_and_gross_funding_without_reservation(database, tmp_path, sign):
    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database)
    feed[0] = replace(feed[0], funding=replace(feed[0].funding, rate=D(sign)))
    candidate = native_claim(owner, 'future', product='SWAP', side='SELL', amount='1', price='2')
    try:
        authority = SharedCapitalAuthority(tmp_path / 'capital.json', policy=cap_policy,
                                           observation=lambda: account_feed[0], clock_ms=lambda: account_feed[0].observed_at_ms, create=True)
        monitor = CarryMonitor(tmp_path / 'carry.json', policy=policy, capital=authority,
                               observation=lambda: feed[0], clock_ms=lambda: account_feed[0].observed_at_ms, create=True)
        actual = await repo.check_carry(cfg.binding, candidate)
        assert actual == monitor.check(candidate) and actual.allowed
        assert actual.funding_reserve_quote == D('.0088') and actual.funding_reference_price_usdt == 2
        assert await capital.claims(cfg.binding) == ()
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_failed_feed_is_durable_denial_and_original_retry_never_calls_source(database):
    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database)
    try:
        assert (await repo.check_carry(cfg.binding)).allowed

        def failed():
            raise ConnectionError('synthetic unavailable funding feed')

        repo.observation = failed
        cut = await repo.cut(cfg.binding)
        first = await repo.observe('failed', cut=cut, owner=owner)
        assert first.decision.reason_code == 'CARRY_EVIDENCE_UNAVAILABLE' and not first.trading_permission
        assert (await financial.snapshot(cfg.binding)).recovery_required
        repo.observation = lambda: feed[0]
        replay = await repo.observe('failed', cut=cut, owner=owner)
        assert replay == replace(first, replayed=True)
        frame = await repo.frame(cfg.binding)
        assert frame['head']['last_checked_at_ms'] == first.checked_at_ms
        assert frame['bundle'] == _json(asdict(feed[0]))
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.carry_checks') == 2
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_financial_refusal_retains_newer_expensive_terms_and_prevents_cheap_rollback(database):
    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database, funding_budget_quote=D('0'))
    candidate = native_claim(owner, 'swap', product='SWAP', amount='1')
    try:
        old = feed[0]
        feed[0] = replace(old, terms=replace(old.terms, sequence=2, taker_fee_rate=D('.01')))
        first = await repo.check_carry(cfg.binding, candidate)
        assert not first.allowed and first.reason_code == 'CARRY_FUNDING_BUDGET'
        frame = await repo.frame(cfg.binding)
        assert frame['bundle']['terms']['sequence'] == 2
        feed[0] = old
        second = await repo.check_carry(cfg.binding, candidate)
        assert not second.allowed and second.reason_code == 'CARRY_STREAM_REPLAY_OR_MUTATION'
        assert (await repo.frame(cfg.binding))['bundle']['terms']['sequence'] == 2
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['generation', 'expiry'])
async def test_carry_primary_commit_rechecks_generation_and_original_stream_age(database, monkeypatch, fault):
    import asyncio
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database)
    original = financial._runtime._commit
    if fault == 'generation':
        await install_generation_fault(database)

    async def changed(tx):
        if fault == 'generation':
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        else:
            await asyncio.sleep(1.05)
        await original(tx)

    monkeypatch.setattr(financial._runtime, '_commit', changed)
    try:
        cut = await repo.cut(cfg.binding)
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT|CARRY_FACTS_STALE_AT_COMMIT'):
            await repo.observe('boundary', cut=cut, owner=owner)
        assert await repo.lookup('boundary', cfg.binding) is None
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.carry_heads') == 0
        assert (await repo.cut(cfg.binding)).checkpoint.revision == cut.checkpoint.revision
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['backend', 'process'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_carry_commit_loss_recovers_original_operation_without_funding_credit(database, monkeypatch, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.carry_process import exit_carry

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository

    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database, max_age_ms=10000)
    admin = await asyncpg.connect(database[1])
    cold = None
    try:
        cut = await repo.cut(cfg.binding)
        before = await financial.balance(cfg.binding)
        if fault == 'process':
            await exit_carry(cfg, owner, feed[0], committed)
        else:
            original = financial._runtime._commit

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid) is True
                raise ConnectionError('synthetic carry COMMIT reply lost')

            monkeypatch.setattr(financial._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await repo.observe('boundary', cut=cut, owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        cold_capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: account_feed[0], max_rows=64, max_bytes=1048576)
        restored = PgCarryRepository(cold_capital, observation=lambda: feed[0], max_rows=64, max_bytes=1048576)
        found = await restored.lookup('boundary', cfg.binding)
        assert (found is not None) is committed
        if not committed:
            await restored.observe('boundary', cut=cut, owner=owner)

        def forbidden():
            raise AssertionError('Original readback must not recapture feeds')

        restored.observation = forbidden
        result = await restored.observe('boundary', cut=cut, owner=owner)
        assert result.replayed and result.decision.allowed and not result.trading_permission
        assert await cold.balance(cfg.binding) == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.carry_checks') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.financial_events') == 0
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stream', ['mark', 'index', 'funding', 'terms'])
@pytest.mark.parametrize('fault', ['source', 'future', 'stale', 'mutation', 'sequence'])
async def test_independent_stream_denial_retains_newer_evidence(database, stream, fault):
    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database)
    try:
        assert (await repo.check_carry(cfg.binding)).allowed
        b = feed[0]
        item = getattr(b, stream)
        changed = {'source': dict(source='internal_quote'), 'future': dict(observed_at_ms=item.observed_at_ms + 60000),
                   'stale': dict(observed_at_ms=item.observed_at_ms - 1001),
                   'mutation': dict(observed_at_ms=item.observed_at_ms - 1), 'sequence': dict(sequence=0)}[fault]
        feed[0] = replace(b, **{stream: replace(item, **changed)})
        actual = await repo.check_carry(cfg.binding)
        assert not actual.allowed
        frame = await repo.frame(cfg.binding)
        assert frame['bundle'][stream] == _json(asdict(item))
        assert frame['latest_receipt']['decision']['allowed'] is False
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.carry_checks') == 2
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_rolled_funding_history_retains_payments_and_cannot_spend_receipts_twice(database):
    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database)
    payment = FundingPayment('bill1', policy.anchor_ms + 1, D('-.01'))
    b = feed[0]
    feed[0] = replace(b, account=replace(b.account, included_funding_bill_ids=('bill1',)),
                      funding=replace(b.funding, payments=(payment,)))
    try:
        before = await financial.balance(cfg.binding)
        first = await repo.check_carry(cfg.binding)
        assert first.allowed and first.settled_payment_quote == D('-.01') and first.funding_debits_quote == D('.01')
        feed[0] = replace(feed[0], account=replace(feed[0].account, included_funding_bill_ids=()),
                          funding=replace(feed[0].funding, sequence=2, payments=()))
        denied = await repo.check_carry(cfg.binding)
        assert not denied.allowed and denied.reason_code == 'CARRY_FUNDING_HISTORY_OR_ACCOUNT_MISMATCH'
        assert len((await repo.frame(cfg.binding))['payments']) == 1
        assert await financial.balance(cfg.binding) == before
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('corrupt', ['payment', 'receipt', 'stream', 'head'])
async def test_carry_corruption_denies_cold_reads_and_new_risk(database, corrupt):
    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database)
    b = feed[0]
    feed[0] = replace(b, account=replace(b.account, included_funding_bill_ids=('bill1',)),
                      funding=replace(b.funding, payments=(FundingPayment('bill1', policy.anchor_ms + 1, D('-.01')),)))
    try:
        assert (await repo.check_carry(cfg.binding)).allowed
        table, sql = {
            'payment': ('carry_payments', "UPDATE life_financial.carry_payments SET signed_payment_quote=1"),
            'receipt': ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{decision,funding_events}','99') WHERE command->>'kind'='CARRY_CHECK'"),
            'stream': ('carry_checks', "UPDATE life_financial.carry_checks SET bundle=jsonb_set(bundle,'{mark,price_usdt}','\"99\"')"),
            'head': ('carry_heads', "UPDATE life_financial.carry_heads SET check_count=2"),
        }[corrupt]
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        await database[0].execute(sql)
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        assert (await financial.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='PG_CARRY_UNVERIFIED'):
            await repo.frame(cfg.binding)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['uid', 'scope', 'nan', 'negative_fee', 'fractional_sequence', 'tier', 'schedule', 'duplicate_bill', 'future_payment'])
async def test_finite_complete_cost_and_schedule_validation_denies_malformed_bundles(database, fault):
    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database)
    b = feed[0]
    modifications = {
        'uid': dict(funding=replace(b.funding, account_uid='999')),
        'scope': dict(terms=replace(b.terms, scope_complete=False)),
        'nan': dict(funding=replace(b.funding, rate=D('NaN'))),
        'negative_fee': dict(terms=replace(b.terms, maker_fee_rate=D('-.1'))),
        'fractional_sequence': dict(mark=replace(b.mark, sequence=1.0)),
        'tier': dict(terms=replace(b.terms, tiers=())),
        'schedule': dict(funding=replace(b.funding, next_settlement_ms=b.funding.settlement_ms)),
        'duplicate_bill': dict(account=replace(b.account, included_funding_bill_ids=('b',)),
                               funding=replace(b.funding, payments=(FundingPayment('b', policy.anchor_ms, D('0')), FundingPayment('b', policy.anchor_ms, D('0'))))),
        'future_payment': dict(account=replace(b.account, included_funding_bill_ids=('b',)),
                               funding=replace(b.funding, payments=(FundingPayment('b', b.funding.coverage_through_ms + 1, D('0')),))),
    }
    feed[0] = replace(b, **modifications[fault])
    try:
        denied = await repo.check_carry(cfg.binding)
        assert not denied.allowed
        assert (await financial.snapshot(cfg.binding)).recovery_required
        assert (await repo.frame(cfg.binding))['bundle'] is None
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.carry_payments') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('armed', [False, True])
async def test_native_prepare_requires_current_carry_and_late_ack_survives_feed_denial(database, armed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack
    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database)
    try:
        value = replace(native_claim(owner, amount='2'), intent_id='intent1', wire_id='wire1')
        assert (await capital.reserve(ReserveCapital('shared', value), await capital.cut(cfg.binding), owner)).decision.allowed
        command = order(owner, risk_epoch=1, fee_buffer_quote_usdt=D('.002'), fee_buffer_base_life=D('0'))
        with pytest.raises(StorageError, match='RECOVERY_REQUIRED|CARRY_NOT_READY'):
            await financial.prepare(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.check_carry(cfg.binding)).allowed
        await financial.prepare(command, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        if armed:
            await financial.arm('operation1', cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        before = await financial.balance(cfg.binding)
        feed[0] = replace(feed[0], funding=replace(feed[0].funding, scope_complete=False))
        assert not (await repo.check_carry(cfg.binding)).allowed
        if not armed:
            with pytest.raises(StorageError, match='RECOVERY_REQUIRED|CARRY_NOT_READY'):
                await financial.arm('operation1', cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        else:
            # Diagnostic/late monetary observations do not obtain allocation rights.
            observed = await financial.observation_cut(cfg.binding)
            await financial.acknowledge(ack(owner, observed.observed_at), cut=observed.cut, owner=owner)
        assert await financial.balance(cfg.binding) == before
        assert (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_new_shared_claim_revokes_previous_carry_readiness_until_full_recheck(database):
    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database)
    try:
        assert (await repo.check_carry(cfg.binding)).allowed
        assert not (await financial.snapshot(cfg.binding)).recovery_required
        candidate = native_claim(owner, 'new', product='SWAP', amount='1')
        assert (await capital.reserve(ReserveCapital('new', candidate), await capital.cut(cfg.binding), owner)).decision.allowed
        assert (await financial.snapshot(cfg.binding)).recovery_required
        current = await repo.check_carry(cfg.binding)
        assert current.allowed and current.funding_reserve_quote == D('.0044')
        assert not (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_bounded_reads_roles_and_concurrent_checks_cannot_bypass_account_scope(database):
    import asyncio
    from uuid import uuid4

    import asyncpg

    financial, cfg, owner, cap_policy, account_feed, capital, policy, feed, repo = await setup(database)
    reporter = None
    try:
        cut = await repo.cut(cfg.binding)
        results = await asyncio.gather(*(repo.observe(key, cut=cut, owner=owner) for key in ('one', 'two')), return_exceptions=True)
        assert sum(isinstance(r, StorageError) for r in results) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.carry_checks') == 1
        assert (await repo.check_carry(cfg.binding)).allowed
        for rows, size in ((1, 1048576), (64, 32)):
            bounded = PgCarryRepository(capital, observation=lambda: feed[0], max_rows=rows, max_bytes=size)
            with pytest.raises(StorageError, match='PG_CARRY_READ_BOUND'):
                await bounded.frame(cfg.binding)
        reporter = await asyncpg.connect(cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
        assert await reporter.fetchval('SELECT count(*) FROM life_financial.carry_checks') == 2
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.execute('INSERT INTO life_financial.carry_payments SELECT * FROM life_financial.carry_payments')
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.fetchval('SELECT life_financial.carry_check($1,NULL,NULL,0,NULL,NULL,NULL)', cfg.account_id)
        with pytest.raises(asyncpg.RaiseError, match='LIFE_ACCOUNT_SCOPE_INVALID'):
            await reporter.fetchval('SELECT life_financial.carry_read($1,64,1048576,NULL)', uuid4())
    finally:
        if reporter is not None:
            await reporter.close()
        await financial.close()
