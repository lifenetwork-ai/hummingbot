"""Exact-wire one-shot SPOT boundary; receipts never authorize HTTP."""
import asyncio
import hashlib
import json
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_action_preparation import bridge, command

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.quote_send import ArmQuoteAction


def wire(row):
    return json.dumps({'clOrdId': 'wire1', 'instId': 'LIFE-USDT', 'side': row.side.lower(),
                       'ordType': 'post_only', 'tdMode': 'cash', 'px': '1.000', 'sz': '2.00'}, separators=(',', ':'))


async def prepared(database, side='BUY', *, max_fact_age_ms=1000):
    repo, cfg, owner, row = await bridge(database, side=side, prepare_policy=False)
    await database[0].execute('''INSERT INTO life_financial.spot_quote_admission_policies VALUES(
        $1,$2,'liquidity_service',0,0,.01,.01,.01,$3,1000,1000,1000)''', cfg.account_id, cfg.binding.policy_hash, max_fact_age_ms)
    await repo.prepare_quote_action(await command(database, cfg, owner, row), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    cmd = await command(database, cfg, owner, row)
    return repo, cfg, owner, ArmQuoteAction('operation1', row, cmd.admission, wire(row))


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
async def test_exact_wire_and_unknown_attempt_commit_together_once_without_monetary_change(database, side):
    repo, cfg, owner, cmd = await prepared(database, side)
    try:
        before = await repo.snapshot(cfg.binding)
        receipt = await repo.arm_quote_action(cmd, cut=before.cut, owner=owner)
        assert receipt.state == 'SEND_UNKNOWN' and not receipt.replayed and receipt.trading_permission is False
        assert receipt.wire_sha256 == hashlib.sha256(cmd.wire_json.encode()).hexdigest()
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert after.recovery_required and (await repo.attempt('intent1', cfg.binding)).state.value == 'SEND_UNKNOWN'
        saved = await repo.lookup_quote_arm('operation1', cfg.binding)
        assert saved.replayed and saved.wire_sha256 == receipt.wire_sha256 and not saved.trading_permission
        with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
            await repo.arm_quote_action(cmd, cut=after.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == after.cut
        raw = json.loads(await database[0].fetchval("SELECT command FROM life_financial.operations WHERE operation_id='arm:operation1'"))
        assert raw['command']['wire_json'] == cmd.wire_json
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('key,value', [('clOrdId', 'wire2'), ('instId', 'BTC-USDT'), ('side', 'sell'),
                                       ('ordType', 'limit'), ('tdMode', 'cross'), ('px', '1.01'), ('sz', '3')])
async def test_wire_substitution_denied_before_arm_with_all_holds_retained(database, key, value):
    repo, cfg, owner, cmd = await prepared(database)
    try:
        data = json.loads(cmd.wire_json)
        data[key] = value
        bad = replace(cmd, wire_json=json.dumps(data))
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='QUOTE_WIRE_MISMATCH'):
            await repo.arm_quote_action(bad, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await repo.attempt('intent1', cfg.binding)).state.value == 'PREPARED'
        assert await repo.lookup_quote_arm('operation1', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['post_only', 'fee', 'economic', 'stale', 'before_prepare'])
async def test_final_fresh_admission_rechecks_book_fees_subsidy_and_causal_capture(database, case):
    repo, cfg, owner, cmd = await prepared(database, max_fact_age_ms=100 if case == 'stale' else 1000)
    try:
        facts = cmd.admission
        reasons = {'post_only': 'QUOTE_RULE_BOOK_OR_FEES_DENIED', 'fee': 'QUOTE_RULE_BOOK_OR_FEES_DENIED',
                   'economic': 'QUOTE_ECONOMICS_DENIED', 'stale': 'QUOTE_ADMISSION_STALE',
                   'before_prepare': 'QUOTE_ARM_FACTS_BEFORE_PREPARE'}
        if case == 'post_only':
            facts = replace(facts, best_ask_usdt=D('1'))
        elif case == 'fee':
            facts = replace(facts, maker_fee_rate=D('.2'))
        elif case == 'economic':
            facts = replace(facts, exit_value_usdt=D('.9'))
        elif case == 'before_prepare':
            facts = replace(facts, observed_at=owner.valid_from, reference_at=owner.valid_from, exit_value_at=owner.valid_from, fee_at=owner.valid_from)
        else:
            await database[0].execute('SELECT pg_sleep(.12)')
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match=reasons[case]):
            await repo.arm_quote_action(replace(cmd, admission=facts), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await repo.attempt('intent1', cfg.binding)).state.value == 'PREPARED'
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_concurrent_boundary_has_one_success_and_no_replay_grant(database):
    repo, cfg, owner, cmd = await prepared(database)
    try:
        cut = (await repo.snapshot(cfg.binding)).cut
        results = await asyncio.gather(*(repo.arm_quote_action(cmd, cut=cut, owner=owner) for _ in range(2)), return_exceptions=True)
        assert sum(not isinstance(r, Exception) for r in results) == 1
        assert any(isinstance(r, StorageError) and r.code == 'PG_EXPECTED_VERSION_CONFLICT' for r in results)
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.operations WHERE operation_id='arm:operation1'") == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_generic_arm_cannot_be_adopted_as_a_wire_bound_submission(database):
    repo, cfg, owner, cmd = await prepared(database)
    try:
        await repo.arm('operation1', cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert await repo.lookup_quote_arm('operation1', cfg.binding) is None
        with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
            await repo.arm_quote_action(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_generation_change_at_physical_commit_rolls_back_unknown_state_and_wire_record(database, monkeypatch):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import install_generation_fault
    repo, cfg, owner, cmd = await prepared(database)
    await install_generation_fault(database)
    commit = repo._runtime._commit

    async def changed(tx):
        await tx._connection.execute('SELECT life_financial.test_change_generation($1)', cfg.account_id)
        await commit(tx)
    monkeypatch.setattr(repo._runtime, '_commit', changed)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='EXPIRED_OR_CHANGED_AT_COMMIT'):
            await repo.arm_quote_action(cmd, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await repo.attempt('intent1', cfg.binding)).state.value == 'PREPARED'
        assert await repo.lookup_quote_arm('operation1', cfg.binding) is None
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_freshness_rechecked_at_physical_commit_not_only_at_function_entry(database, monkeypatch):
    repo, cfg, owner, cmd = await prepared(database, max_fact_age_ms=100)
    commit = repo._runtime._commit

    async def delayed(tx):
        await tx._connection.execute('SELECT pg_sleep(.12)')
        await commit(tx)
    monkeypatch.setattr(repo._runtime, '_commit', delayed)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='QUOTE_ADMISSION_STALE|QUOTE_CAPTURE_MISMATCH'):
            await repo.arm_quote_action(cmd, cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await repo.attempt('intent1', cfg.binding)).state.value == 'PREPARED'
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('case', ['duplicate', 'extra', 'null', 'number', 'source', 'action', 'owner'])
async def test_raw_runtime_sql_cannot_bypass_wire_source_or_original_owner_contract(database, case):
    import asyncpg
    repo, cfg, owner, cmd = await prepared(database)
    c = await asyncpg.connect(cfg.dsn)
    try:
        raw, writer = cmd.json(), owner.json()
        if case == 'duplicate':
            raw['wire_json'] = cmd.wire_json[:-1] + ',"sz":"2"}'
        elif case == 'extra':
            raw['wire_json'] = json.dumps({**json.loads(cmd.wire_json), 'tag': 'extra'})
        elif case in ('null', 'number'):
            raw['wire_json'] = json.dumps({**json.loads(cmd.wire_json), 'px': None if case == 'null' else 1})
        elif case == 'source':
            raw['admission']['exit_source_kind'] = 'quote_model'
        elif case == 'action':
            raw['action']['payload_json'] += ' '
        else:
            from uuid import uuid4
            writer['process_id'] = str(uuid4())
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(asyncpg.RaiseError, match='LIFE_(QUOTE_WIRE_INVALID|QUOTE_ADMISSION_FACTS_INVALID|QUOTE_ARM_ORIGIN_MISMATCH|OWNER_)'):
            await c.fetchval('SELECT life_financial.spot_arm_quote($1,$2::jsonb,$3::jsonb,$4,$5,$6,$7::jsonb)',
                             cfg.account_id, json.dumps(cfg.binding.json()), json.dumps(writer), before.cut.checkpoint.revision,
                             before.cut.checkpoint.state_digest, 'f' * 64, json.dumps(raw))
        assert (await repo.snapshot(cfg.binding)).cut == before.cut
        assert (await repo.attempt('intent1', cfg.binding)).state.value == 'PREPARED'
    finally:
        await c.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['wire', 'receipt', 'kind', 'admission'])
async def test_privileged_single_field_corruption_is_not_accepted_as_a_bound_record(database, field):
    repo, cfg, owner, cmd = await prepared(database)
    try:
        await repo.arm_quote_action(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        c = database[0]
        await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
        try:
            if field == 'wire':
                await c.execute("UPDATE life_financial.operations SET command=jsonb_set(command,'{command,wire_json}',to_jsonb('changed'::text)) WHERE operation_id='arm:operation1'")
            elif field == 'admission':
                await c.execute("UPDATE life_financial.operations SET command=jsonb_set(command,'{command,admission,maker_fee_rate}',to_jsonb('0.02'::text)) WHERE operation_id='arm:operation1'")
            elif field == 'kind':
                await c.execute("UPDATE life_financial.operations SET command=command-'kind' WHERE operation_id='arm:operation1'")
            else:
                await c.execute("UPDATE life_financial.operations SET receipt=jsonb_set(receipt,'{wire_sha256}',to_jsonb(repeat('a',64))) WHERE operation_id='arm:operation1'")
        finally:
            await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        assert (await repo.snapshot(cfg.binding)).recovery_required
        with pytest.raises(StorageError, match='QUOTE_SEND_UNVERIFIED'):
            await repo.lookup_quote_arm('operation1', cfg.binding)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_scope_and_least_privilege_keep_kernel_private_and_reporter_nonwriting(database):
    from uuid import uuid4

    import asyncpg
    repo, cfg, owner, cmd = await prepared(database)
    runtime = await asyncpg.connect(cfg.dsn)
    reporter = await asyncpg.connect(cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'))
    try:
        for c in (runtime, reporter):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute('SELECT life_financial.guard_quote_wire(NULL,NULL)')
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute('SELECT life_financial.guard_quote_admission_core(NULL,NULL,NULL,NULL,NULL,true)')
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute('SELECT life_financial.spot_state_v13(NULL)')
            assert await c.fetchval('SELECT life_financial.spot_quote_arms_verified($1)', uuid4()) is False
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await reporter.execute('SELECT life_financial.spot_arm_quote(NULL,NULL,NULL,NULL,NULL,NULL,NULL)')
    finally:
        await runtime.close()
        await reporter.close()
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_actual_backend_termination_keeps_boundary_atomic_without_regrant(database, monkeypatch, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, cmd = await prepared(database)
    before = await repo.snapshot(cfg.binding)
    commit = repo._runtime._commit
    admin = await asyncpg.connect(database[1])

    async def lost(tx):
        pid = tx._connection.get_server_pid()
        if committed:
            await commit(tx)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', pid)
        raise ConnectionError('synthetic lost wire boundary reply')
    monkeypatch.setattr(repo._runtime, '_commit', lost)
    try:
        with pytest.raises(CommitUnknown):
            await repo.arm_quote_action(cmd, cut=before.cut, owner=owner)
        assert not repo._runtime.healthy
    finally:
        await admin.close()
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        saved = await cold.lookup_quote_arm('operation1', cfg.binding)
        assert (saved is not None) is committed
        if saved is not None:
            assert saved.replayed and not saved.trading_permission
            with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
                await cold.arm_quote_action(cmd, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        assert (await cold.attempt('intent1', cfg.binding)).state.value == ('SEND_UNKNOWN' if committed else 'PREPARED')
        assert ((await cold.snapshot(cfg.binding)).balance, (await cold.snapshot(cfg.binding)).budget) == (before.balance, before.budget)
    finally:
        await cold.close()


@pytest.mark.asyncio
async def test_historical_wire_proof_remains_valid_for_late_ack_without_renewing_permission(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack
    repo, cfg, owner, cmd = await prepared(database)
    try:
        await repo.arm_quote_action(cmd, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        await database[0].execute('SELECT pg_sleep(1.1)')
        at = await database[0].fetchval('SELECT clock_timestamp()')
        await repo.acknowledge(ack(owner, at), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await repo.attempt('intent1', cfg.binding)).state.value == 'ACKED'
        assert not (await repo.snapshot(cfg.binding)).recovery_required
        saved = await repo.lookup_quote_arm('operation1', cfg.binding)
        assert saved.state == 'SEND_UNKNOWN' and saved.replayed and not saved.trading_permission
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_process_exit_before_after_commit_retains_wire_identity_and_holds_without_rearm(database, committed):
    import sys
    from dataclasses import fields

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    repo, cfg, owner, _ = await bridge(database, claim=False)
    config = {field.name: getattr(cfg, field.name) for field in fields(cfg)}
    config.update(account_id=str(cfg.account_id), database_lineage=str(cfg.database_lineage), binding=cfg.binding.json())
    payload = json.dumps(dict(config=config, owner=owner.json(), committed=committed))
    # Captures are created after interpreter/driver startup; production freshness
    # limits are not widened to accommodate a test subprocess import.
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
from hummingbot.strategy_v2.life_liquidity.storage.quote_send import ArmQuoteAction
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
    def admission():
        at=now(); return QuoteAdmissionFacts(cfg['binding'],at,at,at,at,D('1'),D('1'),D('.99'),D('1.01'),D('.01'),D('0'),D('0'),D('0'),D('0'),False,'independent_market')
    prep=PrepareSpot('operation1','intent1','wire1','LIFE-USDT','BUY',D('2'),D('1'),D('1'),D('.02'),str(owner.session_id),owner.epoch,1,0,D('.2'),D('.1'))
    await repo.prepare_quote_action(PrepareQuoteAction(row,prep,admission()),cut=(await repo.snapshot(cfg['binding'])).cut,owner=owner)
    wire=json.dumps(dict(clOrdId='wire1',instId='LIFE-USDT',side='buy',ordType='post_only',tdMode='cash',px='1.00',sz='2'))
    command=ArmQuoteAction('operation1',row,admission(),wire); cut=(await repo.snapshot(cfg['binding'])).cut
    print(json.dumps(command.json()),flush=True)
    original=repo._runtime._commit
    async def die(tx):
        if data['committed']: await original(tx)
        os._exit(73)
    repo._runtime._commit=die
    await repo.arm_quote_action(command,cut=cut,owner=owner)
asyncio.run(run())
'''
    try:
        process = await asyncio.create_subprocess_exec(sys.executable, '-c', child, stdin=asyncio.subprocess.PIPE,
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(payload.encode()), 15)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        assert process.returncode == 73, stderr.decode()
        original_command = ArmQuoteAction.from_json(json.loads(stdout))
    finally:
        await repo.close()
    cold = PgSpotRepository(await PgRuntime.open(cfg))
    try:
        saved = await cold.lookup_quote_arm('operation1', cfg.binding)
        assert (saved is not None) is committed
        if saved is not None:
            assert saved.wire_sha256 == original_command.wire_sha256 and saved.replayed and not saved.trading_permission
            with pytest.raises(StorageError, match='RECONCILE_BEFORE_RETRY'):
                await cold.arm_quote_action(original_command, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        assert (await cold.attempt('intent1', cfg.binding)).state.value == ('SEND_UNKNOWN' if committed else 'PREPARED')
        assert (await cold.balance(cfg.binding)).reserved_quote_usdt == D('2.2')
        assert (await cold.budget(cfg.binding)).session_subsidy_committed_usdt == D('.02')
    finally:
        await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('exit_value', [D('1.005'), D('1.02')])
async def test_improved_current_economics_uses_conservative_existing_subsidy_without_refund(database, exit_value):
    repo, cfg, owner, cmd = await prepared(database)
    try:
        before = await repo.snapshot(cfg.binding)
        improved = replace(cmd, admission=replace(cmd.admission, exit_value_usdt=exit_value))
        result = await repo.arm_quote_action(improved, cut=before.cut, owner=owner)
        assert result.state == 'SEND_UNKNOWN' and not result.trading_permission
        after = await repo.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        assert after.budget.session_subsidy_committed_usdt == D('.02')
    finally:
        await repo.close()
