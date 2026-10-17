"""Native SWAP intent units and restart accounting, with synthetic sources only."""
import json
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_carry import setup as carry_setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import native_claim

import pytest

from hummingbot.core.data_type.common import PositionAction, TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap import PgSwapRepository
from hummingbot.strategy_v2.life_liquidity.storage.products import ReserveCapital
from hummingbot.strategy_v2.life_liquidity.storage.swap import PrepareSwap


async def setup(database, *, side=TradeType.SELL, action=PositionAction.OPEN, max_age_ms=5000, spot_options=None, mode='HEDGE', carry_options=None):
    options = dict(carry_options or {})
    values = await carry_setup(database, max_age_ms=options.pop('max_age_ms', max_age_ms), spot_options=spot_options,
                               capital_changes=dict(position_mode=mode), **options)
    financial, cfg, owner, cap_policy, account_feed, capital, carry_policy, carry_feed, carry = values
    if action is PositionAction.CLOSE:
        account_feed[0] = replace(account_feed[0], sequence=2,
                                  long_base=D('2') if side is TradeType.SELL else D('0'),
                                  short_base=D('2') if side is TradeType.BUY else D('0'))
        carry_feed[0] = replace(carry_feed[0], account=replace(carry_feed[0].account, snapshot=account_feed[0]))
    policy = dict(controller_id='main', tick_size_usdt='0.01', min_size_contracts='0.1', max_quote_levels=8, max_capture_age_ms=max_age_ms)
    await database[0].execute('INSERT INTO life_financial.swap_policies VALUES($1,$2,$3::jsonb,$4)',
                              cfg.account_id, cfg.binding.policy_hash, json.dumps(policy), fingerprint(policy))
    config = OrderExecutorConfig(id='swap1', timestamp=account_feed[0].observed_at_ms / 1000,
                                 connector_name='okx_perpetual', trading_pair='LIFE-USDT', side=side,
                                 position_action=action, amount=D('1'), price=D('1'), leverage=2, level_id='0',
                                 execution_strategy=ExecutionStrategy.LIMIT_MAKER)
    candidate = replace(native_claim(owner, 'swap1', product='SWAP', side=side.name, action=action.name, amount='1'), wire_id='SwapWire1')
    assert (await capital.reserve(ReserveCapital('claim', candidate), await capital.cut(cfg.binding), owner)).decision.allowed
    assert (await carry.check_carry(cfg.binding)).allowed
    return values, PgSwapRepository(capital), PrepareSwap.from_config('prepare1', candidate.wire_id, config)


@pytest.mark.asyncio
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
async def test_native_swap_prepare_arm_ack_preserves_life_and_contract_units_without_wallet_spend(database, side):
    values, repo, command = await setup(database, side=side)
    financial, cfg, owner = values[:3]
    try:
        before = await financial.balance(cfg.binding)
        cut = await repo.cut(cfg.binding)
        prepared = await repo.prepare(command, cut=cut, owner=owner)
        assert prepared.state == 'PREPARED' and prepared.atomic_scope is AtomicScope.ACCOUNT
        assert not prepared.trading_permission and prepared.permit.quantity_base == 1 and prepared.permit.contracts == 4
        assert prepared.permit.side is side and prepared.permit.account_uid == '12345'
        assert (await repo.prepare(command, cut=cut, owner=owner)).replayed
        assert not (await financial.snapshot(cfg.binding)).recovery_required
        armed = await repo.arm(command.operation_id, cut=await repo.cut(cfg.binding), owner=owner)
        assert armed.state == 'SEND_UNKNOWN' and not armed.trading_permission
        assert (await financial.snapshot(cfg.binding)).recovery_required
        acknowledged = await repo.acknowledge(command.operation_id, exchange_order_id='987654321', owner=owner)
        assert acknowledged.state == 'ACKED' and not acknowledged.trading_permission
        assert not (await financial.snapshot(cfg.binding)).recovery_required
        assert await financial.balance(cfg.binding) == before
        assert len(await values[5].claims(cfg.binding)) == 1
        assert (await repo.acknowledge(command.operation_id, exchange_order_id='987654321', owner=owner)).replayed
        with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
            await repo.arm(command.operation_id, cut=await repo.cut(cfg.binding), owner=owner)
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_prepared_swap_and_spot_share_one_verified_account_without_double_holds(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
    values, repo, command = await setup(database)
    financial, cfg, owner, cap_policy, account_feed, capital, carry_policy, carry_feed, carry = values
    try:
        await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
        spot = replace(native_claim(owner, 'spot', amount='2'), intent_id='intent1', wire_id='wire1')
        assert (await capital.reserve(ReserveCapital('spot', spot), await capital.cut(cfg.binding), owner)).decision.allowed
        assert (await carry.check_carry(cfg.binding)).allowed
        await financial.prepare(order(owner, risk_epoch=1, fee_buffer_quote_usdt=D('.002'), fee_buffer_base_life=D('0')),
                                cut=(await financial.snapshot(cfg.binding)).cut, owner=owner)
        view = await financial.snapshot(cfg.binding)
        assert not view.recovery_required
        assert view.balance.reserved_quote_usdt == D('2.002') and view.balance.reserved_base_life == 0
        assert len(await capital.claims(cfg.binding)) == 2
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 2
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_qualified_late_swap_ack_after_halt_expiry_and_carry_denial_never_rearms(database):
    import asyncio
    from datetime import timedelta
    values, repo, command = await setup(database, spot_options=dict(session_duration=timedelta(seconds=2)))
    financial, cfg, owner = values[:3]
    try:
        await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
        await repo.arm(command.operation_id, cut=await repo.cut(cfg.binding), owner=owner)
        now = await database[0].fetchval('SELECT clock_timestamp()')
        await asyncio.sleep(max(0, (owner.expires_at - now).total_seconds()) + .05)
        assert await database[0].fetchval('SELECT clock_timestamp()>$1', owner.expires_at)
        await database[0].execute("UPDATE life_financial.accounts SET halted=true,mode='RECOVERY_ONLY' WHERE account_id=$1", cfg.account_id)
        b = values[7][0]
        values[7][0] = replace(b, funding=replace(b.funding, scope_complete=False))
        assert not (await values[8].check_carry(cfg.binding)).allowed
        before = await financial.balance(cfg.binding)
        assert (await repo.acknowledge(command.operation_id, exchange_order_id='987654321', owner=owner)).state == 'ACKED'
        assert await financial.balance(cfg.binding) == before
        with pytest.raises(StorageError):
            await repo.arm(command.operation_id, cut=await repo.cut(cfg.binding), owner=owner)
        with pytest.raises(StorageError, match='PG_OPERATION_CONFLICT'):
            await repo.acknowledge(command.operation_id, exchange_order_id='987654322', owner=owner)
        assert (await financial.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='PG_SWAP_TRANSPORT_NOT_IMPLEMENTED'):
            await repo.authorize_boundary(command.operation_id, wire_payload=b'{}', owner=owner)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('family', ['LOSS', 'SUBSIDY', 'STRESS'])
async def test_account_budget_exhaustion_blocks_swap_without_refunding_original_claim(database, family):
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await database[0].execute("INSERT INTO life_financial.budget_buckets VALUES($1,$2,'SESSION',$3,1,0,1,0)",
                                  cfg.account_id, family, str(owner.session_id))
        await database[0].execute("INSERT INTO life_financial.budget_events VALUES($1,'synthetic-exhaustion',$2,'SESSION',$3,'SPEND',1)",
                                  cfg.account_id, family, str(owner.session_id))
        with pytest.raises(StorageError, match='BUDGET_EXHAUSTED'):
            await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
        assert len(await values[5].claims(cfg.binding)) == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['generation', 'expiry'])
@pytest.mark.parametrize('stage', ['prepare', 'arm'])
async def test_swap_physical_commit_rechecks_scope_and_original_capture_age(database, monkeypatch, fault, stage):
    import asyncio
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    values, repo, command = await setup(database, max_age_ms=2000)
    financial, cfg, owner = values[:3]
    try:
        if stage == 'arm':
            await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
        if fault == 'generation':
            await install_generation_fault(database)
        original = financial._runtime._commit

        async def changed(tx):
            key = command.operation_id if stage == 'prepare' else repo._step_id(command.operation_id, 'arm')
            staged = await tx._connection.fetchval("SELECT receipt->>'state' FROM life_financial.operations WHERE operation_id=$1", key)
            assert staged == ('PREPARED' if stage == 'prepare' else 'SEND_UNKNOWN'), 'fault must follow accepted storage publication'
            if fault == 'generation':
                await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
            else:
                b = values[7][0]
                source_ms = min(values[4][0].observed_at_ms, b.mark.observed_at_ms, b.index.observed_at_ms,
                                b.funding.observed_at_ms, b.terms.observed_at_ms)
                now_ms = await tx._connection.fetchval('SELECT floor(extract(epoch FROM clock_timestamp())*1000)::bigint')
                await asyncio.sleep(max(0, (source_ms + 2000 - now_ms) / 1000) + .1)
            await original(tx)

        monkeypatch.setattr(financial._runtime, '_commit', changed)
        cut = await repo.cut(cfg.binding)
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT|SWAP_CARRY_STALE_AT_COMMIT|SWAP_CAPTURE_STALE_AT_COMMIT'):
            if stage == 'prepare':
                await repo.prepare(command, cut=cut, owner=owner)
            else:
                await repo.arm(command.operation_id, cut=cut, owner=owner)
        assert (await repo.cut(cfg.binding)).checkpoint.revision == cut.checkpoint.revision
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.send_attempts WHERE state='SEND_UNKNOWN'") == 0
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['request', 'permit', 'payload', 'state', 'receipt', 'missing_arm'])
async def test_swap_corruption_denies_cold_reads_and_common_account_health(database, fault):
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
        await repo.arm(command.operation_id, cut=await repo.cut(cfg.binding), owner=owner)
        table, sql = {
            'request': ('swap_preparations', "UPDATE life_financial.swap_preparations SET request=jsonb_set(request,'{config,amount}','\"2\"')"),
            'permit': ('swap_preparations', "UPDATE life_financial.swap_preparations SET permit=jsonb_set(permit,'{contracts}','\"5\"')"),
            'payload': ('send_attempts', "UPDATE life_financial.send_attempts SET payload=convert_to('{}','UTF8'),payload_hash=encode(sha256(convert_to('{}','UTF8')),'hex')"),
            'state': ('send_attempts', "UPDATE life_financial.send_attempts SET state='PREPARED'"),
            'receipt': ('operations', "UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{permit,quantity_base}','\"3\"') WHERE command->>'kind'='SWAP_PREPARE'"),
            'missing_arm': ('swap_intent_events', "DELETE FROM life_financial.swap_intent_events WHERE kind='SWAP_ARM'"),
        }[fault]
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        await database[0].execute(sql)
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        assert (await financial.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='PG_SWAP_UNVERIFIED'):
            await repo.frame(cfg.binding)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('stage', ['prepare', 'arm', 'ack'])
@pytest.mark.parametrize('fault', ['process', 'backend'])
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_swap_commit_loss_retains_one_original_intent_without_send_permission(database, monkeypatch, stage, fault, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_process import exit_swap

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository

    values, repo, command = await setup(database, max_age_ms=10000)
    financial, cfg, owner = values[:3]
    admin = await asyncpg.connect(database[1])
    cold = None
    try:
        if stage != 'prepare':
            await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
        if stage == 'ack':
            await repo.arm(command.operation_id, cut=await repo.cut(cfg.binding), owner=owner)
        before = await financial.balance(cfg.binding)
        cut = await repo.cut(cfg.binding)
        if fault == 'process':
            await exit_swap(cfg, command, owner, stage, committed)
        else:
            original = financial._runtime._commit

            async def lost(tx):
                pid = tx._connection.get_server_pid()
                if committed:
                    await original(tx)
                assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid) is True
                raise ConnectionError('synthetic native SWAP COMMIT reply lost')

            monkeypatch.setattr(financial._runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                if stage == 'prepare':
                    await repo.prepare(command, cut=cut, owner=owner)
                elif stage == 'arm':
                    await repo.arm(command.operation_id, cut=cut, owner=owner)
                else:
                    await repo.acknowledge(command.operation_id, exchange_order_id='987654321', owner=owner)
        cold = PgSpotRepository(await PgRuntime.open(cfg))
        capital = PgSharedCapitalRepository(cold, owner=owner, observation=lambda: None, max_rows=64, max_bytes=1048576)
        restored = PgSwapRepository(capital)
        key = command.operation_id if stage == 'prepare' else restored._step_id(command.operation_id, stage)
        found = await restored.lookup(key, cfg.binding)
        assert (found is not None) is committed
        if not committed:
            if stage == 'prepare':
                await restored.prepare(command, cut=cut, owner=owner)
            elif stage == 'arm':
                await restored.arm(command.operation_id, cut=cut, owner=owner)
            else:
                await restored.acknowledge(command.operation_id, exchange_order_id='987654321', owner=owner)
        result = await restored.lookup(key, cfg.binding)
        assert result.replayed and not result.trading_permission
        assert await cold.balance(cfg.binding) == before
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_preparations') == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_intent_events') == {'prepare': 0, 'arm': 1, 'ack': 2}[stage]
        assert len(await capital.claims(cfg.binding)) == 1
        if stage != 'prepare':
            with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
                await restored.arm(command.operation_id, cut=await restored.cut(cfg.binding), owner=owner)
        with pytest.raises(StorageError, match='PG_SWAP_TRANSPORT_NOT_IMPLEMENTED'):
            await restored.authorize_boundary(command.operation_id, wire_payload=b'{}', owner=owner)
    finally:
        await admin.close()
        if cold is not None:
            await cold.close()
        await financial.close()


@pytest.mark.asyncio
async def test_swap_units_match_original_file_sender_permit_calculation(database):
    from types import SimpleNamespace as NS

    from hummingbot.strategy_v2.life_liquidity.market_data import LinearSwapContract
    from hummingbot.strategy_v2.life_liquidity.protected_swap import ProtectedSwapExecutorSender
    values, repo, command = await setup(database)
    financial, cfg, owner, cap_policy = values[:4]
    try:
        result = await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
        sender = object.__new__(ProtectedSwapExecutorSender)
        sender._storage_repository = None
        sender.contract = LinearSwapContract(
            'LIFE-USDT-SWAP', 'LIFE-USDT', cap_policy.contract_value_life, cap_policy.lot_contracts, D('.1'), D('.01'))
        sender.controller = NS(_order_safety_wal=None, config=NS(id='main', recovery_account_uid='12345', strategy=NS(
            perpetual=NS(enabled=True, connector='okx_perpetual', pair='LIFE-USDT', margin_mode='cross', position_mode=cap_policy.position_mode, leverage=cap_policy.leverage),
            quotes=NS(spreads_bps=(D('1'),) * 8))))
        sender.manager = NS(current_session=NS(session_id=str(owner.session_id), epoch=owner.epoch, config_version=1))
        sender.risk_epoch = lambda: 1
        assert result.permit == sender._permit_for(command.config(), command.client_order_id)
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['ONEWAY', 'HEDGE'])
@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
async def test_native_close_orders_preserve_actual_position_and_gross_funding_reserve(database, mode, side):
    values, repo, command = await setup(database, action=PositionAction.CLOSE, side=side, mode=mode)
    financial, cfg, owner = values[:3]
    try:
        decision = (await values[8].frame(cfg.binding))['latest_receipt']['decision']
        assert D(decision['funding_reserve_quote']) == D('.0088')
        receipt = await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
        assert receipt.permit.position_action is PositionAction.CLOSE and receipt.permit.position_mode.name == mode
        assert receipt.permit.side is side and receipt.permit.quantity_base == 1 and receipt.permit.contracts == 4
        await repo.arm(command.operation_id, cut=await repo.cut(cfg.binding), owner=owner)
        assert len(await values[5].claims(cfg.binding)) == 1
        frame = await values[5].frame(cfg.binding)
        assert D(frame['snapshot']['long_base']) + D(frame['snapshot']['short_base']) == 2
    finally:
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,value', [('side', 1.0), ('leverage', 2.0), ('side', True), ('amount', 'NaN'), ('timestamp', 'Infinity')])
async def test_native_sql_cannot_coerce_noninteger_enum_or_nonfinite_config_values(database, field, value):
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    try:
        config = command.json()['config']
        config[field] = value
        assert not await database[0].fetchval('''SELECT life_financial.swap_config_valid($1::jsonb,p.body,cp.body,$2)
            FROM life_financial.swap_policies p JOIN life_financial.shared_capital_policies cp USING(account_id)
            WHERE p.account_id=$3''', json.dumps(config), values[4][0].observed_at_ms, cfg.account_id)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await financial.close()


@pytest.mark.asyncio
async def test_native_swap_read_bounds_and_runtime_roles_do_not_allow_unscoped_writes(database):
    from uuid import uuid4

    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    reporter = None
    try:
        await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
        await repo.arm(command.operation_id, cut=await repo.cut(cfg.binding), owner=owner)
        await repo.acknowledge(command.operation_id, exchange_order_id='987654321', owner=owner)
        for rows, size in ((1, 1048576), (64, 32)):
            bounded_capital = PgSharedCapitalRepository(financial, owner=owner, observation=lambda: None, max_rows=rows, max_bytes=size)
            with pytest.raises(StorageError, match='PG_SWAP_READ_BOUND'):
                await PgSwapRepository(bounded_capital).frame(cfg.binding)
        reporter = await asyncpg.connect(cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
        assert await reporter.fetchval('SELECT count(*) FROM life_financial.swap_preparations') == 1
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.execute('INSERT INTO life_financial.swap_preparations SELECT * FROM life_financial.swap_preparations')
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.fetchval('SELECT life_financial.swap_command($1,NULL,NULL,0,NULL,NULL,NULL,NULL)', cfg.account_id)
        with pytest.raises(asyncpg.RaiseError, match='LIFE_ACCOUNT_SCOPE_INVALID'):
            await reporter.fetchval('SELECT life_financial.swap_read($1,64,1048576,NULL)', uuid4())
    finally:
        if reporter is not None:
            await reporter.close()
        await financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['amount', 'price', 'leverage', 'wire', 'controller', 'market', 'connector', 'level', 'strategy', 'capture'])
async def test_swap_preparation_cannot_change_original_claim_or_contract_configuration(database, fault):
    values, repo, command = await setup(database)
    financial, cfg, owner = values[:3]
    updates = {
        'amount': dict(amount=D('2')), 'price': dict(price=D('1.001')), 'leverage': dict(leverage=3),
        'controller': dict(controller_id='other'), 'market': dict(trading_pair='BTC-USDT'),
        'connector': dict(connector_name='okx'), 'level': dict(level_id='8'),
        'strategy': dict(execution_strategy=ExecutionStrategy.MARKET),
        'capture': dict(timestamp=command.config().timestamp - 60),
    }
    if fault == 'wire':
        command = replace(command, client_order_id='OtherWire')
    else:
        command = PrepareSwap.from_config(command.operation_id, command.client_order_id, command.config().model_copy(update=updates[fault]))
    try:
        with pytest.raises(StorageError, match='SWAP_'):
            await repo.prepare(command, cut=await repo.cut(cfg.binding), owner=owner)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.swap_preparations') == 0
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert len(await values[5].claims(cfg.binding)) == 1
    finally:
        await financial.close()
