"""Atomic captured-action holds and late accounting on a disposable primary."""
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_actions import action, setup, transition
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import PrepareQuoteAction, QuoteAdmissionFacts
from hummingbot.strategy_v2.life_liquidity.storage.actions import ClaimQuoteActions
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def bridge(database, *, side='BUY', objective='liquidity_service', prepare_policy=True, claim=True,
                 min_edge=D('0'), uncertainty=D('0'), payload_changes=None, action_fact_age_ms=1000, **seed_options):
    repo, cfg, owner = await setup(database, max_fact_age_ms=action_fact_age_ms, **seed_options)
    if prepare_policy:
        await database[0].execute('''INSERT INTO life_financial.spot_quote_admission_policies VALUES(
            $1,$2,$3,$4,$5,.01,.01,.01,1000,1000,1000,1000)''', cfg.account_id, cfg.binding.policy_hash, objective, min_edge, uncertainty)
    at = await database[0].fetchval('SELECT clock_timestamp()')
    executor = OrderExecutorConfig(id='intent1', controller_id='controller1', timestamp=at.timestamp(), connector_name='okx',
                                   trading_pair='LIFE-USDT', side=TradeType[side], amount=D('2'), price=D('1'),
                                   level_id='0', execution_strategy=ExecutionStrategy.LIMIT_MAKER)
    raw = executor.model_dump_json()
    if payload_changes is not None:
        import json
        raw = json.dumps({**json.loads(raw), **payload_changes})
    row = action(owner, side=side, payload_json=raw)
    if not claim:
        return repo, cfg, owner, row
    await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    await repo.transition_quote_action(await transition(database, cfg, row, 'DISPATCHED'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    return repo, cfg, owner, row


async def command(database, cfg, owner, row, **changes):
    at = await database[0].fetchval('SELECT clock_timestamp()')
    facts = QuoteAdmissionFacts(cfg.binding, at, at, at, at, D('1'), D('1'), D('.99'), D('1.01'),
                                D('.01'), D('0'), D('0'), D('0'), D('0'), False, 'independent_market')
    prepare = order(owner, side=row.side, subsidy_quote_usdt=D('.02'))
    return PrepareQuoteAction(row, prepare, replace(facts, **changes))


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_claim_bound_preparation_atomically_publishes_action_holds_and_receipt(database, side):
    repo, cfg, owner, row = await bridge(database, side=side)
    try:
        before = await repo.snapshot(cfg.binding)
        cmd = await command(database, cfg, owner, row)
        result = await repo.prepare_quote_action(cmd, cut=before.cut, owner=owner)
        assert result.state == 'PREPARED' and not result.replayed
        assert (await repo.quote_actions(cfg.binding))[0].state == 'PREPARED'
        assert (await repo.lookup_prepare('operation1', cfg.binding)).intent_id == 'intent1'
        view = await repo.snapshot(cfg.binding)
        assert view.budget.session_subsidy_committed_usdt == D('.02')
        assert view.balance.reserved_base_life == (D('.1') if side == 'BUY' else D('2.1'))
        assert view.balance.reserved_quote_usdt == (D('2.2') if side == 'BUY' else D('.2'))
        assert not view.recovery_required
        assert (await repo.prepare_quote_action(cmd, cut=before.cut, owner=owner)).replayed
        assert (await repo.snapshot(cfg.binding)).cut == view.cut
        with pytest.raises(StorageError):
            await repo.prepare(cmd.prepare, cut=view.cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['missing_policy', 'profit_edge', 'post_only', 'fee_buffer', 'wrong_amount', 'under_subsidy'])
async def test_failed_admission_retains_action_and_rolls_back_all_money(database, case):
    repo, cfg, owner, row = await bridge(database, objective='profit_mm' if case == 'profit_edge' else 'liquidity_service',
                                         prepare_policy=case != 'missing_policy')
    try:
        cmd = await command(database, cfg, owner, row)
        if case == 'post_only':
            cmd = replace(cmd, admission=replace(cmd.admission, best_ask_usdt=D('1')))
        elif case == 'fee_buffer':
            cmd = replace(cmd, prepare=replace(cmd.prepare, fee_buffer_base_life=D('0')))
        elif case == 'wrong_amount':
            cmd = replace(cmd, prepare=replace(cmd.prepare, quantity_base=D('3')))
        elif case == 'under_subsidy':
            cmd = replace(cmd, prepare=replace(cmd.prepare, subsidy_quote_usdt=D('0')))
        before = await repo.snapshot(cfg.binding)
        reasons = {'missing_policy': 'QUOTE_ADMISSION_POLICY_REQUIRED', 'profit_edge': 'QUOTE_ECONOMICS_DENIED',
                   'post_only': 'QUOTE_RULE_BOOK_OR_FEES_DENIED', 'fee_buffer': 'QUOTE_RULE_BOOK_OR_FEES_DENIED',
                   'wrong_amount': 'QUOTE_CAPTURE_MISMATCH', 'under_subsidy': 'QUOTE_ECONOMICS_DENIED'}
        with pytest.raises(StorageError, match=reasons[case]):
            await repo.prepare_quote_action(cmd, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await repo.quote_actions(cfg.binding))[0].state == 'DISPATCHED'
        assert await repo.attempt('intent1', cfg.binding) is None
        assert await repo.lookup_prepare('operation1', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_pending_sibling_action_does_not_block_late_fill_accounting(database):
    repo, cfg, owner, row = await bridge(database)
    try:
        await database[0].execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', cfg.account_id, cfg.binding.policy_hash)
        await repo.prepare_quote_action(await command(database, cfg, owner, row), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        at = await database[0].fetchval('SELECT clock_timestamp()')
        await repo.acknowledge(ack(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        other = action(owner, intent_id='other', side='SELL')
        await repo.claim_quote_actions(ClaimQuoteActions('sibling', (other,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).recovery_required
        at = await database[0].fetchval('SELECT clock_timestamp()')
        await repo.apply_fills(filled(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.balance(cfg.binding)).total_base_life == 6
        assert (await repo.reservation('intent1', cfg.binding)).remaining_base_life == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('key', ['observed_at', 'reference_at', 'exit_value_at', 'fee_at'])
async def test_stale_admission_sources_deny_without_partial_holds(database, key):
    from datetime import timedelta
    repo, cfg, owner, row = await bridge(database)
    try:
        cmd = await command(database, cfg, owner, row)
        old = cmd.admission.observed_at - timedelta(seconds=2)
        changes = {key: old}
        if key == 'observed_at':
            changes.update(reference_at=old, exit_value_at=old, fee_at=old)
        cmd = replace(cmd, admission=replace(cmd.admission, **changes))
        with pytest.raises(StorageError, match='QUOTE_(ADMISSION|SOURCE)_STALE|ADMISSION_BEFORE_DISPATCH'):
            await repo.prepare_quote_action(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.attempt('intent1', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('objective', ['profit_mm', 'liquidity_service'])
@pytest.mark.parametrize('maker', [D('-.01'), D('0'), D('.01')])
@pytest.mark.parametrize('favorable', [False, True])
async def test_existing_economic_engine_and_sql_admission_agree(database, side, objective, maker, favorable):
    from hummingbot.strategy_v2.life_liquidity.economics import EconomicInputs, EconomicPolicy, evaluate_quote
    value = (D('1.05') if side == 'BUY' else D('.95')) if favorable else D('1')
    repo, cfg, owner, row = await bridge(database, side=side, objective=objective, min_edge=D('50'), uncertainty=D('25'))
    try:
        cmd = await command(database, cfg, owner, row, exit_value_usdt=value, maker_fee_rate=maker, exit_fee_rate=D('.005'),
                            impact_cost_usdt=D('.003'), carry_cost_usdt=D('.002'), inventory_risk_usdt=D('.001'))
        expected = evaluate_quote(EconomicInputs(side, D('1'), D('2'), value, D('.01'), D('.01'), D('.01'), maker,
                                                 D('.005'), D('.003'), D('.002'), D('.001'), D('25'), False),
                                  EconomicPolicy(objective, D('50')), subsidy_remaining_quote=D('100'))
        cmd = replace(cmd, prepare=replace(cmd.prepare, subsidy_quote_usdt=expected.subsidy_reserved_quote))
        cut = (await repo.snapshot(cfg.binding)).cut
        if expected.allowed:
            await repo.prepare_quote_action(cmd, cut=cut, owner=owner)
            assert (await repo.budget(cfg.binding)).session_subsidy_committed_usdt == expected.subsidy_reserved_quote
        else:
            with pytest.raises(StorageError, match='QUOTE_ECONOMICS_DENIED'):
                await repo.prepare_quote_action(cmd, cut=cut, owner=owner)
            assert (await repo.snapshot(cfg.binding)).cut == cut
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_atomic_same_operation_concurrency_and_changed_replay(database):
    import asyncio
    repo, cfg, owner, row = await bridge(database)
    try:
        cmd = await command(database, cfg, owner, row)
        cut = (await repo.snapshot(cfg.binding)).cut
        results = await asyncio.gather(repo.prepare_quote_action(cmd, cut=cut, owner=owner), repo.prepare_quote_action(cmd, cut=cut, owner=owner))
        assert sorted(result.replayed for result in results) == [False, True]
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.spot_action_events WHERE operation_id='operation1'") == 1
        with pytest.raises(StorageError, match='OPERATION_CONFLICT'):
            await repo.prepare_quote_action(replace(cmd, prepare=replace(cmd.prepare, client_order_id='changed')), cut=cut, owner=owner)
        assert (await repo.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_generation_at_commit_rolls_back_claim_link_and_all_holds(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    repo, cfg, owner, row = await bridge(database)
    await install_generation_fault(database)
    original = repo._runtime._commit

    async def changed(tx):
        await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', changed)
    try:
        cmd = await command(database, cfg, owner, row)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT'):
            await repo.prepare_quote_action(cmd, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert await repo.attempt('intent1', cfg.binding) is None
        assert (await repo.quote_actions(cfg.binding))[0].state == 'DISPATCHED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_admission_expiring_at_physical_commit_rolls_back_everything(database, monkeypatch):
    import asyncio
    repo, cfg, owner = await setup(database)
    await database[0].execute('''INSERT INTO life_financial.spot_quote_admission_policies VALUES(
        $1,$2,'liquidity_service',0,0,.01,.01,.01,100,1000,1000,1000)''', cfg.account_id, cfg.binding.policy_hash)
    at = await database[0].fetchval('SELECT clock_timestamp()')
    executor = OrderExecutorConfig(id='intent1', controller_id='controller1', timestamp=at.timestamp(), connector_name='okx',
                                   trading_pair='LIFE-USDT', side=TradeType.BUY, amount=D('2'), price=D('1'), level_id='0',
                                   execution_strategy=ExecutionStrategy.LIMIT_MAKER)
    row = action(owner, payload_json=executor.model_dump_json())
    await repo.claim_quote_actions(ClaimQuoteActions('claim', (row,)), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    await repo.transition_quote_action(await transition(database, cfg, row, 'DISPATCHED'), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    original = repo._runtime._commit

    async def delayed(tx):
        await asyncio.sleep(.12)
        await original(tx)
    monkeypatch.setattr(repo._runtime, '_commit', delayed)
    try:
        cmd = await command(database, cfg, owner, row)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='QUOTE_ADMISSION_STALE'):
            await repo.prepare_quote_action(cmd, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await repo.quote_actions(cfg.binding))[0].state == 'DISPATCHED'
        assert await repo.lookup_prepare('operation1', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_backend_termination_keeps_action_holds_and_operation_atomic(database, monkeypatch, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, row = await bridge(database)
    cmd = await command(database, cfg, owner, row)
    cut = (await repo.snapshot(cfg.binding)).cut
    original = repo._runtime._commit
    admin = await asyncpg.connect(database[1])

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await original(tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic lost action preparation reply')
    monkeypatch.setattr(repo._runtime, '_commit', lost)
    try:
        with pytest.raises(CommitUnknown):
            await repo.prepare_quote_action(cmd, cut=cut, owner=owner)
        assert not repo._runtime.healthy
    finally:
        await admin.close()
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        saved = await cold.lookup_prepare('operation1', cfg.binding)
        assert (saved is not None) is committed
        assert (await cold.quote_actions(cfg.binding))[0].state == ('PREPARED' if committed else 'DISPATCHED')
        assert (await cold.prepare_quote_action(cmd, cut=cut, owner=owner)).replayed is committed
        assert (await cold.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.operations WHERE operation_id='operation1'") == 1
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('changes', [dict(execution_strategy='MARKET'), dict(connector_name='okx_perpetual'), dict(leverage=2),
                                     dict(side=2), dict(type='position_executor'), dict(chaser_config={}), dict(timestamp=0),
                                     dict(level_id='1'), dict(amount='3'), dict(slippage_multiplier='1'), dict(extra='ignored')])
async def test_sql_rejects_unqualified_captured_executor_payload(database, changes):
    repo, cfg, owner, row = await bridge(database, payload_changes=changes)
    try:
        cmd = await command(database, cfg, owner, row)
        cut = (await repo.snapshot(cfg.binding)).cut
        reason = 'CONSTRAINT_REJECTED' if changes.get('timestamp') == 0 else 'QUOTE_CAPTURE_MISMATCH'
        with pytest.raises(StorageError, match=reason):
            await repo.prepare_quote_action(cmd, cut=cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == cut
        assert await repo.attempt('intent1', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_process_exit_preserves_atomic_action_preparation(database, committed):
    import asyncio
    import json
    import sys
    from dataclasses import fields

    from hummingbot.strategy_v2.life_liquidity.storage.actions import SpotQuoteAction
    from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut, PrepareSpot
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, _ = await bridge(database, claim=False)
    config = {field.name: getattr(cfg, field.name) for field in fields(cfg)}
    config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    payload = json.dumps(dict(config=config, owner=owner.json(), committed=committed))
    child = r'''
import asyncio, json, os, sys
from datetime import datetime, timezone
from decimal import Decimal as D
from uuid import UUID
from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig,ExecutionStrategy
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken,StateBinding
from hummingbot.strategy_v2.life_liquidity.storage.financial import PrepareSpot
from hummingbot.strategy_v2.life_liquidity.storage.actions import SpotQuoteAction,ClaimQuoteActions,RunnerActionFacts,TransitionQuoteAction
from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import PrepareQuoteAction,QuoteAdmissionFacts
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig,PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
async def run():
    data=json.load(sys.stdin); cfg=data['config']; cfg['binding']=StateBinding.from_json(cfg['binding'])
    for key in ('account_id','database_lineage'): cfg[key]=UUID(cfg[key])
    repo=PgSpotRepository(await PgRuntime.open(PgConfig(**cfg))); owner=OwnerToken.from_json(data['owner'])
    now=lambda:datetime.now(timezone.utc)
    capture=OrderExecutorConfig(id='intent1',controller_id='controller1',timestamp=now().timestamp(),connector_name='okx',trading_pair='LIFE-USDT',side=TradeType.BUY,amount=D('2'),price=D('1'),level_id='0',execution_strategy=ExecutionStrategy.LIMIT_MAKER)
    row=SpotQuoteAction('intent1','controller1',str(owner.session_id),owner.epoch,1,'LIFE-USDT','BUY',0,capture.model_dump_json())
    await repo.claim_quote_actions(ClaimQuoteActions('claim',(row,)),cut=(await repo.snapshot(cfg['binding'])).cut,owner=owner)
    facts=RunnerActionFacts(cfg['binding'],now(),'controller1',('intent1',),True)
    await repo.transition_quote_action(TransitionQuoteAction('dispatch',row,'PROPOSED','DISPATCHED',facts),cut=(await repo.snapshot(cfg['binding'])).cut,owner=owner)
    at=now(); admission=QuoteAdmissionFacts(cfg['binding'],at,at,at,at,D('1'),D('1'),D('.99'),D('1.01'),D('.01'),D('0'),D('0'),D('0'),D('0'),False,'independent_market')
    prep=PrepareSpot('operation1','intent1','wire1','LIFE-USDT','BUY',D('2'),D('1'),D('1'),D('.02'),str(owner.session_id),owner.epoch,1,0,D('.2'),D('.1'))
    command=PrepareQuoteAction(row,prep,admission); cut=(await repo.snapshot(cfg['binding'])).cut
    print(json.dumps(dict(command=command.json(),cut=cut.json())),flush=True)
    original=repo._runtime._commit
    async def die(tx):
        if data['committed']: await original(tx)
        os._exit(73)
    repo._runtime._commit=die
    await repo.prepare_quote_action(command,cut=cut,owner=owner)
asyncio.run(run())
'''
    try:
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', child, stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(payload.encode()), 15)
        except BaseException:
            process.kill()
            await process.wait()
            raise
        assert process.returncode == 73, stderr.decode()
        raw = json.loads(stdout)
    finally:
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        assert ((await cold.lookup_prepare('operation1', cfg.binding)) is not None) is committed
        assert (await cold.quote_actions(cfg.binding))[0].state == ('PREPARED' if committed else 'DISPATCHED')
        command = raw['command']
        cmd = PrepareQuoteAction(SpotQuoteAction(**command['action']), PrepareSpot.from_json(command['prepare']),
                                 QuoteAdmissionFacts.from_json(command['admission']))
        assert (await cold.prepare_quote_action(cmd, cut=FinancialCut.from_json(raw['cut']), owner=owner)).replayed is committed
        assert (await cold.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['null_fee', 'null_capture', 'extra_facts', 'model_exit', 'false_binding'])
async def test_raw_sql_cannot_skip_action_admission_contract(database, case):
    import json

    import asyncpg
    repo, cfg, owner, row = await bridge(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        cmd = (await command(database, cfg, owner, row)).json()
        if case == 'null_fee':
            cmd['admission']['maker_fee_rate'] = None
        elif case == 'null_capture':
            cmd['action'] = None
        elif case == 'extra_facts':
            cmd['admission']['permission'] = True
        elif case == 'model_exit':
            cmd['admission']['exit_source_kind'] = 'quote_model'
        elif case == 'false_binding':
            cmd['admission']['binding']['account_uid'] = '999'
        cut = (await repo.snapshot(cfg.binding)).cut
        with pytest.raises(asyncpg.RaiseError, match='(ADMISSION_FACTS|PREPARATION)_INVALID'):
            await c.fetchval('SELECT life_financial.spot_action_prepare($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7::jsonb)',
                             cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(owner.json()),
                             cut.checkpoint.revision, cut.checkpoint.state_digest, 'f' * 64, json.dumps(cmd))
        assert (await repo.snapshot(cfg.binding)).cut == cut
        assert await repo.lookup_prepare('operation1', cfg.binding) is None
    finally:
        await c.close()
        await repo.close()


@pytest.mark.asyncio
async def test_internal_kernel_and_imported_admission_policy_are_not_runtime_writable(database):
    import asyncpg
    repo, cfg, owner, row = await bridge(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.execute('SELECT life_financial.spot_prepare_action_core(NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)')
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.execute('DELETE FROM life_financial.spot_quote_admission_policies')
        with pytest.raises(asyncpg.ObjectNotInPrerequisiteStateError, match='IMMUTABLE_HISTORY'):
            await database[0].execute('UPDATE life_financial.spot_quote_admission_policies SET uncertainty_bps=0')
    finally:
        await c.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_actual_file_and_action_bound_postgres_money_parity(database, tmp_path, side):
    from test.hummingbot.strategy_v2.life_liquidity.storage.test_spot_financial_contracts import (
        BUDGET,
        CAPACITY,
        NOW,
        RISK,
        create_fixture,
    )

    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import open_legacy_spot
    repo, cfg, owner, row = await bridge(database, side=side)
    files, binding, file_owner = create_fixture(tmp_path)
    native = await open_legacy_spot(files, binding=binding, limits=CAPACITY, risk_limits=RISK, subsidy_policy=BUDGET,
                                    loss_policy=BUDGET, config_version=1, account_lock_root=tmp_path / 'locks', owner=file_owner,
                                    create_binding=True, clock=lambda: NOW)
    try:
        cmd = await command(database, cfg, owner, row)
        await repo.prepare_quote_action(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        file_cmd = replace(cmd.prepare, session_id=str(file_owner.session_id), epoch=file_owner.epoch)
        await native.prepare(file_cmd, cut=(await native.snapshot(binding)).cut, owner=file_owner)
        pg, file = await repo.snapshot(cfg.binding), await native.snapshot(binding)
        assert pg.balance == file.balance and pg.budget == file.budget
        assert (await repo.attempt('intent1', cfg.binding)).state == (await native.attempt('intent1', binding)).state
    finally:
        await native.close()
        await repo.close()
