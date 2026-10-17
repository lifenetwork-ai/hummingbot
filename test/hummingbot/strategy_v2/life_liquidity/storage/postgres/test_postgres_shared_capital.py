"""Native shared capital decisions; all policies/account observations synthetic."""
import json
from dataclasses import asdict, replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
from test.hummingbot.strategy_v2.life_liquidity.test_shared_capital import claim, policy, snapshot

import pytest

from hummingbot.strategy_v2.life_liquidity.shared_capital import SharedCapitalAuthority, _json
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.products import ProductReceipt, ReserveCapital


async def setup(database, *, configured=None, observed=None, max_claims=32, spot_options=None):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    financial, cfg, owner = await spot(database, **(spot_options or {}))
    configured = configured or policy()
    body = _json(asdict(configured))
    await database[0].execute('''INSERT INTO life_financial.shared_capital_policies VALUES($1,$2,$3::jsonb,$4,$5)''',
                              cfg.account_id, cfg.binding.policy_hash, json.dumps(body), fingerprint({'policy': body, 'max_claims': max_claims}), max_claims)
    await database[0].execute('UPDATE life_financial.accounts SET risk_epoch=1 WHERE account_id=$1', cfg.account_id)
    at = int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
    feed = [observed or snapshot(spot_life_base=D('5'), observed_at_ms=at)]

    async def observe():
        return feed[0]
    repository = PgSharedCapitalRepository(financial, owner=owner, observation=observe, max_rows=64, max_bytes=1048576)
    return financial, cfg, owner, configured, feed, repository


def native_claim(owner, key='spot', **changes):
    return replace(claim(key, **changes), session_id=str(owner.session_id))


@pytest.mark.asyncio
async def test_shared_spot_swap_cannot_allocate_same_cash_and_collateral_twice(database, tmp_path):
    financial, cfg, owner, configured, feed, repository = await setup(database)
    try:
        authority = SharedCapitalAuthority(tmp_path / 'capital.json', policy=configured,
                                           observation=lambda: feed[0], clock_ms=lambda: feed[0].observed_at_ms, create=True)
        spot_claim = native_claim(owner)
        hedge_claim = native_claim(owner, 'hedge', product='SWAP', side='SELL', amount='6')
        first = await repository.reserve(ReserveCapital('reserve1', spot_claim), await repository.cut(cfg.binding), owner)
        assert isinstance(first, ProductReceipt) and first.binding == cfg.binding
        assert first.decision == authority.reserve(spot_claim) and first.changed and not first.trading_permission
        denied = await repository.reserve(ReserveCapital('reserve2', hedge_claim), await repository.cut(cfg.binding), owner)
        assert denied.decision == authority.reserve(hedge_claim)
        assert not denied.decision.allowed and denied.decision.reason_code == 'CAPITAL_INITIAL_MARGIN_LOW'
        assert await repository.claims(cfg.binding) == (spot_claim,)
        assert (await financial.balance(cfg.binding)).total_quote_usdt == 10
        assert (await financial.balance(cfg.binding)).reserved_quote_usdt == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [
    {'long_base': D('6'), 'short_base': D('6'), 'collateral_quote': D('8')},
    {'long_base': D('2'), 'initial_margin_quote': D('4'), 'maintenance_margin_quote': D('1'), 'collateral_quote': D('100')},
    {'short_base': D('5'), 'collateral_quote': D('100')},
    {'funding_liability_quote': D('11'), 'collateral_quote': D('100')},
    {'mark_price_usdt': D('1.1'), 'index_price_usdt': D('.9'), 'long_base': D('2')},
])
async def test_native_risk_metrics_and_decisions_match_file_authority(database, tmp_path, changes):
    financial, cfg, owner, configured, feed, repository = await setup(database)
    feed[0] = replace(feed[0], **changes)
    try:
        authority = SharedCapitalAuthority(tmp_path / 'capital.json', policy=configured,
                                           observation=lambda: feed[0], clock_ms=lambda: feed[0].observed_at_ms, create=True)
        assert await repository.check_capital(cfg.binding) == authority.check()
        assert not (await financial.snapshot(cfg.binding)).recovery_required
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_shared_policy_blocks_native_spot_prepare_without_original_claim(database):
    financial, cfg, owner, configured, feed, repository = await setup(database)
    try:
        with pytest.raises(StorageError, match='PG_CAPITAL_CLAIM_REQUIRED'):
            await financial.prepare(order(owner, risk_epoch=1), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.reservations') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_native_pending_numeric_representation_does_not_duplicate_retained_claim(database):
    financial, cfg, owner, configured, feed, repository = await setup(database)
    try:
        original = native_claim(owner, amount='1E-8')
        assert (await repository.reserve(ReserveCapital('tiny', original), await repository.cut(cfg.binding), owner)).decision.allowed
        feed[0] = replace(feed[0], sequence=2, pending=(replace(original, quantity_base=D('0.000000010')),))
        decision = await repository.check_capital(cfg.binding)
        assert decision.allowed and decision.spot_buy_hold_quote == D('1E-8')
        assert (await repository.lookup('tiny', cfg.binding)).decision.allowed
        assert await repository.claims(cfg.binding) == (original,)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_historical_denied_receipt_is_verified_before_readback(database):
    financial, cfg, owner, configured, feed, repository = await setup(database)
    try:
        denied = await repository.reserve(ReserveCapital('denied', native_claim(owner, amount='11')), await repository.cut(cfg.binding), owner)
        assert not denied.decision.allowed
        assert (await repository.reserve(ReserveCapital('later', native_claim(owner, amount='1')), await repository.cut(cfg.binding), owner)).decision.allowed
        await database[0].execute('''ALTER TABLE life_financial.operations DISABLE TRIGGER USER;
            UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{decision,allowed}','true') WHERE operation_id='denied';
            ALTER TABLE life_financial.operations ENABLE TRIGGER USER''')
        with pytest.raises(StorageError, match='PG_CAPITAL_UNVERIFIED'):
            await repository.lookup('denied', cfg.binding)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_imported_capacity_is_bound_by_original_model_hash(database):
    financial, cfg, owner, configured, feed, repository = await setup(database)
    try:
        await database[0].execute('ALTER TABLE life_financial.shared_capital_policies DISABLE TRIGGER USER')
        import asyncpg
        with pytest.raises(asyncpg.CheckViolationError):
            await database[0].execute('UPDATE life_financial.shared_capital_policies SET max_claims=33')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['cash', 'inventory', 'close', 'wire', 'delta', 'gross', 'basis', 'funding', 'stress', 'tier', 'third_leverage', 'margin_increment', 'base_fee_delta', 'base_fee_inventory'])
async def test_shared_native_denial_and_metrics_match_file_for_independent_endpoints(database, tmp_path, kind):
    from hummingbot.strategy_v2.life_liquidity.shared_capital import MarginTier
    configured = policy()
    changes = {}
    candidate = None
    if kind == 'cash':
        candidate = dict(amount='11')
    elif kind == 'inventory':
        candidate = dict(side='SELL', amount='6')
    elif kind == 'close':
        candidate = dict(product='SWAP', side='SELL', action='CLOSE', amount='1')
    elif kind == 'wire':
        candidate = dict(amount='1')
    elif kind in ('delta', 'gross', 'basis', 'funding'):
        field, limit = {'delta': ('max_abs_delta_base', '4'), 'gross': ('max_gross_base', '4'),
                        'basis': ('max_basis_loss_quote', '.01'), 'funding': ('max_funding_quote', '.01')}[kind]
        configured = replace(configured, limits=replace(configured.limits, **{field: D(limit)}))
        changes = dict(long_base=D('1'), funding_liability_quote=D('.02'))
    elif kind == 'stress':
        configured = replace(configured, max_stress_loss_quote=D('.01'))
    elif kind == 'tier':
        changes = dict(long_base=D('1001'), collateral_quote=D('9999'))
    elif kind in ('third_leverage', 'margin_increment'):
        configured = replace(configured, leverage=3, tiers=(MarginTier(D('100'), D('.1'), D('.03')),))
        changes = dict(leverage=3, long_base=D('1.23456789123456789'), mark_price_usdt=D('1.123456789123456789'),
                       index_price_usdt=D('1.01'), collateral_quote=D('100'))
    if kind == 'base_fee_delta':
        configured = replace(configured, limits=replace(configured.limits, max_abs_delta_base=D('5.99')))
        changes = dict(short_base=D('6'), collateral_quote=D('100'))
        candidate = dict(side='SELL', amount='4.99')
    elif kind == 'base_fee_inventory':
        candidate = dict(side='SELL', amount='5')
    if kind == 'margin_increment':
        changes = dict(leverage=3, long_base=D('1'), initial_margin_quote=D('1'), collateral_quote=D('100'))
    financial, cfg, owner, configured, feed, repository = await setup(database, configured=configured)
    feed[0] = replace(feed[0], **changes)
    try:
        authority = SharedCapitalAuthority(tmp_path / 'capital.json', policy=configured, observation=lambda: feed[0],
                                           clock_ms=lambda: feed[0].observed_at_ms, create=True)
        if candidate is not None:
            value = native_claim(owner, **candidate)
            if kind == 'wire':
                assert (await repository.reserve(ReserveCapital('first', value), await repository.cut(cfg.binding), owner)).decision == authority.reserve(value)
                value = replace(value, intent_id='same-wire')
            actual = (await repository.reserve(ReserveCapital('candidate', value), await repository.cut(cfg.binding), owner)).decision
            expected = authority.reserve(value)
        else:
            actual, expected = await repository.check_capital(cfg.binding), authority.check()
        assert actual == expected
        assert actual.allowed is (kind in ('third_leverage', 'margin_increment'))
        if kind == 'margin_increment':
            assert actual.initial_margin_quote == D('1.033333333333333334')
        elif kind == 'base_fee_delta':
            assert actual.net_delta_worst_base == D('5.99499')
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['uid', 'stale', 'future', 'scope', 'source', 'mode', 'leverage', 'nan', 'negative', 'fraction', 'position', 'pending_duplicate'])
async def test_unqualified_shared_snapshot_keeps_original_claim_and_native_balances(database, fault):
    configured = policy(position_mode='ONEWAY') if fault == 'position' else policy()
    financial, cfg, owner, configured, feed, repository = await setup(database, configured=configured)
    try:
        if fault == 'position':
            feed[0] = replace(feed[0], position_mode='ONEWAY')
        value = native_claim(owner, amount='1')
        await repository.reserve(ReserveCapital('first', value), await repository.cut(cfg.binding), owner)
        before = await financial.snapshot(cfg.binding)
        changes = {
            'uid': dict(account_uid='99999'), 'stale': dict(observed_at_ms=feed[0].observed_at_ms - 1001),
            'future': dict(observed_at_ms=feed[0].observed_at_ms + 60000), 'scope': dict(scope_complete=False),
            'source': dict(mark_source='internal_quote'), 'mode': dict(margin_mode='isolated'),
            'leverage': dict(leverage=3), 'nan': dict(collateral_quote=D('NaN')), 'negative': dict(collateral_quote=D('-1')),
            'fraction': dict(collateral_quote=D('1E-19')), 'position': dict(long_base=D('1'), short_base=D('1')),
            'pending_duplicate': dict(pending=(value, value)),
        }
        feed[0] = replace(feed[0], sequence=2, **changes[fault])
        with pytest.raises(StorageError, match='PG_CAPITAL_SNAPSHOT_UNQUALIFIED'):
            await repository.check_capital(cfg.binding)
        assert (await financial.snapshot(cfg.binding)).cut == before.cut
        assert await repository.claims(cfg.binding) == (value,)
        assert (await financial.balance(cfg.binding)) == before.balance
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_concurrent_spot_and_swap_allocations_serialize_without_double_commit(database):
    import asyncio
    financial, cfg, owner, configured, feed, repository = await setup(database)
    try:
        cut = await repository.cut(cfg.binding)
        commands = (ReserveCapital('spot7', native_claim(owner, amount='7')),
                    ReserveCapital('swap7', native_claim(owner, 'swap', product='SWAP', side='SELL', amount='7')))
        outcomes = await asyncio.gather(*(repository.reserve(c, cut, owner) for c in commands), return_exceptions=True)
        assert sum(isinstance(result, StorageError) for result in outcomes) == 1
        assert sum(getattr(getattr(result, 'decision', None), 'allowed', False) for result in outcomes) == 1
        loser = commands[next(i for i, result in enumerate(outcomes) if isinstance(result, StorageError))]
        result = await repository.reserve(loser, await repository.cut(cfg.binding), owner)
        assert not result.decision.allowed
        assert len(await repository.claims(cfg.binding)) == 1
        assert (await financial.balance(cfg.binding)).reserved_quote_usdt == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['claim', 'observation', 'receipt', 'head', 'original_claim', 'missing_operation'])
async def test_native_capital_corruption_denies_cold_cut_frame_and_readback(database, fault):
    financial, cfg, owner, configured, feed, repository = await setup(database)
    try:
        await repository.reserve(ReserveCapital('first', native_claim(owner, amount='1')), await repository.cut(cfg.binding), owner)
        table, sql = {
            'claim': ('shared_capital_claims', 'UPDATE life_financial.shared_capital_claims SET quantity_base=2'),
            'observation': ('shared_capital_observations', "UPDATE life_financial.shared_capital_observations SET body=jsonb_set(body,'{collateral_quote}','\"999\"')"),
            'receipt': ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{decision,fees_quote}','\"0\"') WHERE operation_id='first'"),
            'head': ('shared_capital_heads', 'UPDATE life_financial.shared_capital_heads SET operation_count=2'),
            'original_claim': ('operations', "UPDATE life_financial.operations SET command=jsonb_set(command,'{command,claim,wire_id}','\"changed\"') WHERE operation_id='first'"),
            'missing_operation': ('operations', "UPDATE life_financial.operations SET command=jsonb_set(command,'{kind}','\"OTHER\"') WHERE operation_id='first'"),
        }[fault]
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        await database[0].execute(sql)
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        view = await financial.snapshot(cfg.binding)
        assert view.recovery_required
        with pytest.raises(StorageError, match='PG_CAPITAL_UNVERIFIED'):
            await repository.frame(cfg.binding)
        with pytest.raises(StorageError, match='PG_CAPITAL_UNVERIFIED'):
            await repository.lookup('missing', cfg.binding)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_original_operation_replay_does_not_request_new_snapshot_or_refresh_age(database):
    financial, cfg, owner, configured, feed, repository = await setup(database)
    try:
        value = ReserveCapital('original', native_claim(owner, amount='1'))
        cut = await repository.cut(cfg.binding)
        result = await repository.reserve(value, cut, owner)
        before = await financial.snapshot(cfg.binding)
        feed[0] = replace(feed[0], sequence=2, collateral_quote=D('999'))
        assert (await repository.reserve(value, cut, owner)) == replace(result, replayed=True)
        assert (await financial.snapshot(cfg.binding)).cut == before.cut
        with pytest.raises(StorageError, match='PG_OPERATION_CONFLICT'):
            await repository.reserve(replace(value, claim=replace(value.claim, price_usdt=D('2'))), cut, owner)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['generation', 'stale'])
async def test_shared_claim_physical_commit_rechecks_generation_and_unchanged_fact_age(database, monkeypatch, fault):
    import asyncio
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    financial, cfg, owner, configured, feed, repository = await setup(database)
    original = financial._runtime._commit
    if fault == 'generation':
        await install_generation_fault(database)

    async def fail(tx):
        if fault == 'generation':
            await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        else:
            await asyncio.sleep(1.05)
        await original(tx)
    monkeypatch.setattr(financial._runtime, '_commit', fail)
    try:
        before = await financial.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT|CAPITAL_FACTS_STALE_AT_COMMIT'):
            await repository.reserve(ReserveCapital('first', native_claim(owner, amount='1')), await repository.cut(cfg.binding), owner)
        assert (await financial.snapshot(cfg.binding)).cut == before.cut
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['fees', 'quantity', 'wire', 'matching'])
async def test_native_spot_prepare_requires_original_shared_claim_with_fee_coverage(database, fault):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_lifecycle import lifecycle
    financial, cfg, owner, configured, feed, repository = await setup(database)
    try:
        value = replace(native_claim(owner, amount='2'), intent_id='intent1', wire_id='wire1')
        await repository.reserve(ReserveCapital('shared', value), await repository.cut(cfg.binding), owner)
        prepared = order(owner, risk_epoch=1, fee_buffer_quote_usdt=D('.002'), fee_buffer_base_life=D('0'))
        if fault == 'fees':
            prepared = replace(prepared, fee_buffer_quote_usdt=D('.003'))
        elif fault == 'quantity':
            prepared = replace(prepared, quantity_base=D('3'))
        elif fault == 'wire':
            prepared = replace(prepared, client_order_id='other')
        if fault != 'matching':
            with pytest.raises(StorageError, match='PG_CAPITAL_FEE_BUFFER_UNCOVERED|PG_CAPITAL_NATIVE_CLAIM_MISMATCH'):
                await financial.prepare(prepared, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
            assert (await financial.balance(cfg.binding)).reserved_quote_usdt == 0
        else:
            await financial.prepare(prepared, cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
            assert (await financial.balance(cfg.binding)).reserved_quote_usdt == D('2.002')
            await financial.apply_lifecycle(lifecycle(), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
            assert (await financial.balance(cfg.binding)).reserved_quote_usdt == 0
        assert await repository.claims(cfg.binding) == (value,)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['process', 'backend'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_shared_claim_commit_loss_is_read_back_without_double_allocation(database, monkeypatch, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital_process import exit_shared_capital

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository

    # Explicit synthetic child import/startup allowance; the normal age/expiry
    # tests above keep the unchanged 1,000 ms policy.
    financial, cfg, owner, configured, feed, repository = await setup(database, configured=policy(max_age_ms=10000))
    admin = await asyncpg.connect(database[1])
    cold = None
    try:
        command = ReserveCapital('first', native_claim(owner, amount='1'))
        cut = await repository.cut(cfg.binding)
        before = await financial.snapshot(cfg.binding)
        if fault == 'process':
            await exit_shared_capital(cfg, command, owner, feed[0], committed)
        else:
            original = financial._runtime._commit

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid) is True
                raise ConnectionError('synthetic shared capital COMMIT reply lost')
            monkeypatch.setattr(financial._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await repository.reserve(command, cut, owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        restored = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: feed[0], max_rows=64, max_bytes=1048576)
        found = await restored.lookup('first', cfg.binding)
        assert (found is not None) is committed
        if not committed:
            await restored.reserve(command, cut, owner)
        replay = await restored.reserve(command, cut, owner)
        assert replay.replayed and not replay.trading_permission
        assert await restored.claims(cfg.binding) == (command.claim,)
        assert (await cold.balance(cfg.binding)) == before.balance
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.operations WHERE command->>'kind'='SHARED_CAPITAL'") == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
async def test_native_claim_scope_permanent_identity_and_snapshot_sequence_are_enforced(database):
    financial, cfg, owner, configured, feed, repository = await setup(database)
    try:
        original = native_claim(owner, amount='1')
        await repository.reserve(ReserveCapital('first', original), await repository.cut(cfg.binding), owner)
        for index, changed in enumerate((replace(original, side='SELL'), replace(original, wire_id='changed'))):
            with pytest.raises(StorageError, match='PG_CAPITAL_CLAIM_CHANGED'):
                await repository.reserve(ReserveCapital('change' + str(index), changed), await repository.cut(cfg.binding), owner)
        for index, changed in enumerate((replace(original, risk_epoch=2), replace(original, config_version=2), replace(original, epoch=2))):
            with pytest.raises(StorageError, match='PG_CAPITAL_CLAIM_SCOPE_INVALID'):
                await repository.reserve(ReserveCapital('scope' + str(index), changed), await repository.cut(cfg.binding), owner)
        feed[0] = replace(feed[0], collateral_quote=D('11'))
        with pytest.raises(StorageError, match='PG_CAPITAL_SNAPSHOT_REPLAY_OR_MUTATION'):
            await repository.check_capital(cfg.binding)
        assert await repository.claims(cfg.binding) == (original,)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_shared_snapshot_cannot_omit_native_working_order_and_wallet_must_match(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    financial, cfg, owner = await spot(database)
    try:
        await financial.prepare(order(owner), cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        configured = policy()
        body = _json(asdict(configured))
        await database[0].execute('INSERT INTO life_financial.shared_capital_policies VALUES($1,$2,$3::jsonb,$4,32)',
                                  cfg.account_id, cfg.binding.policy_hash, json.dumps(body), fingerprint({'policy': body, 'max_claims': 32}))
        at = int((await database[0].fetchval('SELECT clock_timestamp()')).timestamp() * 1000)
        feed = [snapshot(spot_life_base=D('5'), observed_at_ms=at)]
        repository = PgSharedCapitalRepository(financial, owner=owner, observation=lambda: feed[0], max_rows=64, max_bytes=1048576)
        with pytest.raises(StorageError, match='PG_CAPITAL_NATIVE_ORDER_SCOPE_INCOMPLETE'):
            await repository.check_capital(cfg.binding)
        feed[0] = replace(feed[0], spot_cash_quote=D('11'))
        with pytest.raises(StorageError, match='PG_CAPITAL_SPOT_BALANCE_MISMATCH'):
            await repository.check_capital(cfg.binding)
        assert (await financial.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_claim_reads_require_both_bounds_and_read_only_role_cannot_write(database):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    financial, cfg, owner, configured, feed, repository = await setup(database)
    reporter = None
    try:
        for key in ('one', 'two'):
            await repository.reserve(ReserveCapital(key, native_claim(owner, key, amount='.1')), await repository.cut(cfg.binding), owner)
        for rows, size in ((1, 1048576), (64, 32)):
            bounded = PgSharedCapitalRepository(financial, owner=owner, observation=lambda: feed[0], max_rows=rows, max_bytes=size)
            with pytest.raises(StorageError, match='PG_CAPITAL_READ_BOUND'):
                await bounded.frame(cfg.binding)
        reporter = await asyncpg.connect(cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
        assert await reporter.fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 2
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.execute('INSERT INTO life_financial.shared_capital_claims SELECT * FROM life_financial.shared_capital_claims')
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.fetchval('SELECT life_financial.shared_capital_command($1,NULL,NULL,0,NULL,NULL,NULL)', cfg.account_id)
        from uuid import uuid4
        with pytest.raises(asyncpg.RaiseError, match='LIFE_ACCOUNT_SCOPE_INVALID'):
            await reporter.fetchval('SELECT life_financial.shared_capital_frame($1,64,1048576)', uuid4())
        for function in ('shared_capital_verified(uuid)', 'shared_operation_verified(uuid,text)', 'shared_evaluate(jsonb,jsonb,jsonb)'):
            assert not await database[0].fetchval("SELECT has_function_privilege('life_pg2_runtime',$1,'EXECUTE')", 'life_financial.' + function)
    finally:
        if reporter is not None:
            await reporter.close()
        await financial.close()


@pytest.mark.asyncio
async def test_shared_snapshot_leverage_must_be_an_integer_wire_type(database):
    financial, cfg, owner, configured, feed, repository = await setup(database)
    feed[0] = replace(feed[0], leverage=2.0)
    try:
        assert await database[0].fetchval('SELECT life_financial.shared_snapshot_valid($1::jsonb,$2::jsonb,$3,32)',
                                          json.dumps(_json(asdict(feed[0]))), json.dumps(_json(asdict(configured))), feed[0].observed_at_ms) is False
        with pytest.raises(StorageError, match='PG_CAPITAL_SNAPSHOT_UNQUALIFIED'):
            await repository.check_capital(cfg.binding)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_imported_claim_capacity_cannot_expand_implicitly(database):
    financial, cfg, owner, configured, feed, repository = await setup(database, max_claims=1)
    try:
        await repository.reserve(ReserveCapital('first', native_claim(owner, amount='.1')), await repository.cut(cfg.binding), owner)
        with pytest.raises(StorageError, match='PG_CAPITAL_CLAIM_BOUND'):
            await repository.reserve(ReserveCapital('second', native_claim(owner, 'second', amount='.1')), await repository.cut(cfg.binding), owner)
        assert len(await repository.claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['lot', 'leverage', 'unowned_pending', 'changed_pending'])
async def test_swap_units_and_pending_identity_cannot_bypass_original_allocation(database, fault):
    financial, cfg, owner, configured, feed, repository = await setup(database)
    try:
        value = native_claim(owner, 'swap', product='SWAP', amount='1')
        if fault == 'lot':
            value = replace(value, quantity_base=D('.01'))
        elif fault == 'leverage':
            value = replace(value, leverage=3)
        elif fault == 'unowned_pending':
            feed[0] = replace(feed[0], pending=(value,))
        else:
            await repository.reserve(ReserveCapital('first', value), await repository.cut(cfg.binding), owner)
            feed[0] = replace(feed[0], sequence=2, pending=(replace(value, quantity_base=D('2')),))
            denied = await repository.check_capital(cfg.binding)
            assert not denied.allowed and denied.reason_code == 'CAPITAL_PENDING_IDENTITY_MISMATCH'
            assert await repository.claims(cfg.binding) == (value,)
            return
        with pytest.raises(StorageError, match='PG_CAPITAL_CLAIM_SCOPE_INVALID|PG_CAPITAL_UNOWNED_PENDING_ID'):
            await repository.reserve(ReserveCapital('first', value), await repository.cut(cfg.binding), owner)
        assert await repository.claims(cfg.binding) == ()
    finally:
        await financial.close()
