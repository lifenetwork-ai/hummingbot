"""Explicit recovery of a permanently retired, never prepared preallocation."""
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_trading import configured

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace


async def stranded(database, monkeypatch, *, keys=None):
    t, service = await configured(database, provisional_quotes=True, maker_cycle=True,
                                  configuration_extra=None if keys is None else {'operator_verifier': keys.verifier().policy()})
    commit, seen = service.runtime._commit, []

    async def stop_after_birth(transaction):
        await commit(transaction)
        if _current_trace.get().operation == 'quote_birth_issue':
            seen.append(True)
            raise ConnectionError('synthetic lost birth response')
    try:
        with monkeypatch.context() as patch:
            patch.setattr(service.runtime, '_commit', stop_after_birth)
            with pytest.raises(StorageError) as failed:
                await service.quote('unused-original', TradeType.BUY, 0)
        assert seen == [True], 'Did not reach physical birth COMMIT: ' + str(failed.value) + repr(getattr(failed.value, '__notes__', ()))
        ticket = await database[0].fetchval('SELECT ticket_id FROM life_financial.quote_births')
        assert ticket and not t.service_orders
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        return t, service, ticket
    except BaseException:
        await service.close()
        raise


@pytest.mark.asyncio
async def test_unused_claim_release_keeps_money_history_and_cannot_restore_sender(database, monkeypatch):
    t, service, ticket = await stranded(database, monkeypatch)
    c = database[0]
    try:
        import json

        # Explain the actual empty-history predicate on real native history.
        # A new feature with no records must not scan unrelated command JSON.
        plan = json.loads(await c.fetchval('''EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=$1
                AND command->>'kind'='SHARED_UNUSED_RELEASE')''', t.cfg.account_id))[0]['Plan']

        def nodes(node):
            yield node
            for child in node.get('Plans', ()):
                yield from nodes(child)
        operations = [node for node in nodes(plan) if node.get('Relation Name') == 'operations']
        print('unused_release_empty_plan=' + json.dumps([
            {key: node.get(key) for key in ('Node Type', 'Index Name', 'Actual Rows', 'Rows Removed by Filter', 'Shared Hit Blocks', 'Shared Read Blocks')}
            for node in operations]))
        assert operations and all('Index' in node['Node Type'] for node in operations)
        assert all(node['Actual Rows'] == 0 and node.get('Rows Removed by Filter', 0) == 0 for node in operations)
        # This is the actual combined orphan predicate used by the common
        # release verifier, not only the new variant's private helper.
        orphan = json.loads(await c.fetchval('''EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT NOT EXISTS(SELECT 1 FROM life_financial.operations op
                WHERE op.account_id=$1 AND op.command->>'kind' IN ('SHARED_RELEASE','SHARED_UNUSED_RELEASE')
                AND NOT EXISTS(SELECT 1 FROM life_financial.shared_claim_releases r
                    WHERE r.account_id=op.account_id AND r.operation_id=op.operation_id))''', t.cfg.account_id))[0]['Plan']
        scans = [node for node in nodes(orphan) if node.get('Relation Name') == 'operations']
        assert scans and all('Index' in node['Node Type'] for node in scans), scans
        assert all(node['Actual Rows'] == 0 and node.get('Rows Removed by Filter', 0) == 0 for node in scans)
        await service.retire_unprepared_birth('retire-original', ticket)
        before = await service.financial.snapshot(t.cfg.binding)
        result = await service.release_unused_claim('release-original', ticket)
        assert result.released and not result.trading_permission and not result.replayed
        assert (await service.release_unused_claim('release-original', ticket)).replayed
        after = await service.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert after.cut.checkpoint.revision == before.cut.checkpoint.revision + 1

        async def state(c):
            return await c.fetchval('SELECT life_financial.spot_state($1)', t.cfg.account_id)
        current = await service.runtime._admit(state, safety=True)
        assert current['unverified'] is False and current['financial_recovery_required'] is False
        # Readiness may require a fresh capital/carry observation after release;
        # no financial corruption or automatic trading permission is implied.
        assert not await service.source.capital.claims(t.cfg.binding)
        assert len((await service.source.capital.frame(t.cfg.binding))['settled_claims']) == 1
        assert await c.fetchval('SELECT count(*) FROM life_financial.quote_births') == 1
        assert await c.fetchval('SELECT count(*) FROM life_financial.quote_birth_retirements') == 1
        assert await c.fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert await c.fetchval('SELECT count(*) FROM life_financial.shared_claim_releases WHERE terminal_operation_id IS NOT NULL') == 0
        assert await c.fetchval("SELECT count(*) FROM life_financial.shared_claim_releases WHERE terminal_operation_id IS NULL AND basis->>'kind'='UNUSED_BIRTH'") == 1
        assert service._revoked and not t.service_orders
        import asyncpg
        await c.execute('ALTER TABLE life_financial.shared_claim_releases DISABLE TRIGGER USER')
        try:
            # No fabricated terminal or untagged NULL-terminal variant, even
            # when a privileged corruption test bypasses immutable triggers.
            with pytest.raises(asyncpg.CheckViolationError):
                await c.execute('UPDATE life_financial.shared_claim_releases SET terminal_operation_id=operation_id')
            with pytest.raises(asyncpg.CheckViolationError):
                await c.execute("UPDATE life_financial.shared_claim_releases SET basis='{}'::jsonb")
        finally:
            await c.execute('ALTER TABLE life_financial.shared_claim_releases ENABLE TRIGGER USER')
        assert await c.fetchval('SELECT life_financial.shared_release_verified($1)', t.cfg.account_id)
    finally:
        await service.close()


async def cold_observer(t, service):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.service import PgLiquidityService
    await service.close()
    return await PgLiquidityService.open(
        service.config, runtime_config=t.cfg, owner=t.owner, connectors=dict(service.connectors),
        market_data_provider=service._provider, sources=tuple(e.source for e in service.reporting.events),
        limits=service.limits, clock=service.clock, independent_fill_value=lambda *args: None,
        independent_cashflow_value=lambda *args: None, native_trading=False)


@pytest.mark.asyncio
async def test_unused_claim_release_cold_observation_requires_a_separate_signed_successor(database, monkeypatch):
    from datetime import timedelta
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import signed
    from uuid import uuid4

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, OwnerToken
    from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys, OwnerTransition
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
    keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('DRAIN', 'ACTIVATE'))})
    t, service, ticket = await stranded(database, monkeypatch, keys=keys)
    c, cold = database[0], None
    try:
        await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', t.cfg.account_id)
        await c.execute('INSERT INTO life_financial.control_policies VALUES($1,$2,1000,32,1048576,60000)', t.cfg.account_id, t.cfg.binding.policy_hash)
        control = PgControlRepository(c, service.financial, verifier=keys.verifier())
        drain = OwnerTransition('drain-unused', OwnerMode.DRAINING, t.owner)
        await control.transition_owner(drain, **await signed(database, service.financial, t.cfg, keys, drain))
        await service.retire_unprepared_birth('retire-original', ticket)
        cold = await cold_observer(t, service)
        assert not cold.native_trading and cold.quote_application is None
        control = PgControlRepository(c, cold.financial, verifier=keys.verifier())
        now = await c.fetchval('SELECT clock_timestamp()')
        successor = OwnerToken(uuid4(), uuid4(), t.owner.epoch, t.owner.session_id, now, now + timedelta(seconds=10))
        activate = OwnerTransition('new-generation', OwnerMode.ACTIVE, successor)
        with pytest.raises(StorageError, match='PG_CONTROL_ACTIVATION_UNQUALIFIED'):
            await control.transition_owner(activate, **await signed(database, cold.financial, t.cfg, keys, activate))
        receipt = await cold.release_unused_claim('release-original', ticket)
        assert receipt.released and not receipt.trading_permission and cold._revoked
        assert await c.fetchval('SELECT mode FROM life_financial.accounts') == 'DRAINING'
        assert await c.fetchval('SELECT count(*) FROM life_financial.activation_grants') == 0
        assert (await cold.release_unused_claim('release-original', ticket)).replayed
        arguments = await signed(database, cold.financial, t.cfg, keys, activate)
        assert not (await control.transition_owner(activate, **arguments)).trading_permission
        assert (await control.transition_owner(activate, **arguments)).replayed
        assert await c.fetchval('SELECT owner_generation FROM life_financial.accounts') == successor.generation
        assert await c.fetchval('SELECT count(*) FROM life_financial.activation_grants') == 1
        assert cold._revoked and not t.service_orders
        with pytest.raises(StorageError):
            await cold.quote('unused-original', TradeType.BUY, 0)
        assert await c.fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await service.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['not_retired', 'foreign_ticket', 'expired_at_commit', 'native_expired_at_commit', 'unfenced', 'changed_source', 'operation_conflict', 'release_bound', 'unverified_liability'])
async def test_unused_claim_release_refuses_unqualified_origin_or_source_without_refund(database, monkeypatch, fault):
    import asyncio

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.unused_release import QUERY
    t, service, ticket = await stranded(database, monkeypatch)
    c = database[0]
    try:
        if fault != 'not_retired':
            await service.retire_unprepared_birth('retire-original', ticket)
        if fault == 'release_bound':
            await c.execute('ALTER TABLE life_financial.shared_release_policies DISABLE TRIGGER USER')
            await c.execute('UPDATE life_financial.shared_release_policies SET max_total_bytes=1')
            await c.execute('ALTER TABLE life_financial.shared_release_policies ENABLE TRIGGER USER')
        elif fault == 'unverified_liability':
            # Corrupt native liability while retaining the version. A complete
            # runner DTO must not excuse a damaged financial account.
            await c.execute('ALTER TABLE life_financial.balances DISABLE TRIGGER USER')
            await c.execute("UPDATE life_financial.balances SET liability=.01 WHERE asset='USDT'")
            await c.execute('ALTER TABLE life_financial.balances ENABLE TRIGGER USER')
        if fault == 'native_expired_at_commit':
            # Delay inside physical COMMIT, after the Python fence has passed.
            # Wait only to the original expiry; do not change any clock/limit.
            await c.execute('''CREATE FUNCTION life_financial.test_unused_delay() RETURNS trigger
                LANGUAGE plpgsql AS $$ BEGIN
                    PERFORM pg_sleep(greatest(0,extract(epoch FROM
                        ((current_setting('life.joint_source_deadline')::jsonb->>'expires_at')::timestamptz-clock_timestamp())))+.01);
                    RETURN NULL;
                END; $$''')
            await c.execute('''CREATE CONSTRAINT TRIGGER aaa_test_unused_delay AFTER INSERT ON life_financial.unused_claim_releases
                DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION life_financial.test_unused_delay()''')
        before = await service.financial.snapshot(t.cfg.binding)
        if fault == 'expired_at_commit':
            original = service.runtime._commit

            async def stale(transaction):
                if _current_trace.get().operation == 'unused_release_command':
                    await asyncio.sleep(1.1)
                await original(transaction)
            monkeypatch.setattr(service.runtime, '_commit', stale)
        elif fault in ('unfenced', 'changed_source'):
            original = service.runtime._transaction

            async def corrupt(query, args, owner, receipt, **options):
                if query == QUERY:
                    if fault == 'unfenced':
                        options['source_fence'] = None
                    else:
                        from copy import deepcopy
                        args = list(deepcopy(args))
                        args[-1]['source']['source_hash'] = '0' * 64
                return await original(query, args, owner, receipt, **options)
            monkeypatch.setattr(service.runtime, '_transaction', corrupt)
        target = 'foreign-ticket' if fault == 'foreign_ticket' else ticket
        if fault == 'operation_conflict':
            await service.release_unused_claim('release-original', ticket)
            before = await service.financial.snapshot(t.cfg.binding)
            target = 'another-ticket'
        with pytest.raises(StorageError) as refused:
            await service.release_unused_claim('release-original', target)
        if fault == 'native_expired_at_commit':
            assert str(refused.value) == 'PG_JOINT_SOURCE_EXPIRED_AT_COMMIT'
        after = await service.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert after.cut.checkpoint.revision == before.cut.checkpoint.revision
        assert await c.fetchval('SELECT count(*) FROM life_financial.unused_claim_releases') == int(fault == 'operation_conflict')
        assert not t.service_orders and not service.runtime.healthy
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_unused_claim_release_unknown_physical_commit_recovers_only_original_receipt(database, monkeypatch, committed):
    import asyncpg

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.unused_release import PgUnusedReleaseRepository
    t, service, ticket = await stranded(database, monkeypatch)
    admin = await asyncpg.connect(database[1])
    cold = None
    try:
        await service.retire_unprepared_birth('retire-original', ticket)
        before = await service.financial.snapshot(t.cfg.binding)
        original, seen = service.runtime._commit, []

        async def lost(transaction):
            trace = _current_trace.get()
            if trace.operation != 'unused_release_command':
                return await original(transaction)
            if committed:
                await original(transaction)
            assert await admin.fetchval('SELECT pg_terminate_backend($1)', trace.backend_pid)
            seen.append(True)
            raise ConnectionError('synthetic release response loss')
        with monkeypatch.context() as patch:
            patch.setattr(service.runtime, '_commit', lost)
            with pytest.raises(CommitUnknown):
                await service.release_unused_claim('release-original', ticket)
        assert seen == [True] and not t.service_orders
        cold = await cold_observer(t, service)
        history = await PgUnusedReleaseRepository(cold.collector.swap.capital).read('release-original')
        assert (history is not None) is committed
        after = await cold.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert after.cut.checkpoint.revision == before.cut.checkpoint.revision + int(committed)
        if committed:
            assert (await cold.release_unused_claim('release-original', ticket)).replayed
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
    finally:
        await admin.close()
        await service.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['money', 'source_age', 'owner', 'owner_null', 'intent', 'ticket', 'receipt', 'causal_retirement', 'missing_release_fact'])
async def test_unused_claim_release_detects_same_version_history_damage_even_with_rehashed_command(database, monkeypatch, damage):
    import json

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.unused_release import PgUnusedReleaseRepository
    t, service, ticket = await stranded(database, monkeypatch)
    c = database[0]
    try:
        await service.retire_unprepared_birth('retire-original', ticket)
        await service.release_unused_claim('release-original', ticket)
        version = await c.fetchval('SELECT version FROM life_financial.accounts')
        row = await c.fetchrow("SELECT command::text,receipt::text FROM life_financial.operations WHERE operation_id='release-original'")
        command, receipt = json.loads(row['command']), json.loads(row['receipt'])
        if damage == 'money':
            command['command']['facts']['account_quote_usdt'] = '11'
        elif damage == 'source_age':
            command['command']['source']['max_age_ms'] = 2000
        elif damage == 'owner':
            command['owner']['generation'] = '00000000-0000-0000-0000-000000000001'
        elif damage == 'owner_null':
            command['owner'] = None
        elif damage in ('intent', 'ticket'):
            command['command'][damage + '_id'] = 'foreign-original'
        elif damage == 'missing_release_fact':
            # The empty-history fast path must still discover its orphan op.
            await c.execute('ALTER TABLE life_financial.unused_claim_releases DISABLE TRIGGER USER')
            await c.execute('DELETE FROM life_financial.unused_claim_releases')
            await c.execute('ALTER TABLE life_financial.unused_claim_releases ENABLE TRIGGER USER')
        elif damage == 'causal_retirement':
            body = json.loads(await c.fetchval('SELECT body::text FROM life_financial.quote_birth_retirements'))
            body['retired_at_ms'] = await c.fetchval('SELECT received_at_ms+1 FROM life_financial.unused_claim_releases')
            await c.execute('ALTER TABLE life_financial.quote_birth_retirements DISABLE TRIGGER USER')
            await c.execute('UPDATE life_financial.quote_birth_retirements SET body=$1::jsonb,body_hash=$2', json.dumps(body), fingerprint(body))
            await c.execute('ALTER TABLE life_financial.quote_birth_retirements ENABLE TRIGGER USER')
            assert await c.fetchval('SELECT life_financial.quote_birth_verified($1)', t.cfg.account_id)
        else:
            receipt['trading_permission'] = True
        await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.operations SET command=$1::jsonb,fingerprint=$2,receipt=$3::jsonb WHERE operation_id='release-original'",
                        json.dumps(command), fingerprint(command), json.dumps(receipt))
        await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
        assert await c.fetchval('SELECT version FROM life_financial.accounts') == version
        with pytest.raises(StorageError, match='PG_UNUSED_RELEASE_UNVERIFIED'):
            await PgUnusedReleaseRepository(service.collector.swap.capital).read('release-original')
        assert (await service.financial.snapshot(t.cfg.binding)).recovery_required
        assert not t.service_orders
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_unused_claim_recovers_after_physical_birth_process_death_without_any_wire(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.service_process import (
        cold_service_context,
        crash_service,
    )
    manifest = await crash_service(database, 'capture', provisional_quotes=True)
    context = cold_service_context(manifest)
    cfg, c = context.config, database[0]
    # Explicit synthetic policy import, before collecting recovery evidence.
    # The service never creates these policy/calibration rows on startup.
    await c.execute('INSERT INTO life_financial.portfolio_policies VALUES($1,$2,1,1000,5000,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    await c.execute('INSERT INTO life_financial.shared_release_policies VALUES($1,$2,64,1048576)', cfg.account_id, cfg.binding.policy_hash)
    service = await context.reopen(False)
    try:
        ticket = await c.fetchval('SELECT ticket_id FROM life_financial.quote_births')
        before = await service.financial.snapshot(cfg.binding)
        await service.retire_unprepared_birth('retire-dead-process', ticket)
        result = await service.release_unused_claim('release-dead-process', ticket)
        assert result.released and not result.trading_permission
        assert (await service.release_unused_claim('release-dead-process', ticket)).replayed
        after = await service.financial.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert not await service.collector.swap.capital.claims(cfg.binding)
        assert await c.fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert context.calls and all(r['method'] == 'GET' for r in context.calls)
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['prepared', 'foreign_owner'])
async def test_unused_claim_release_cannot_bypass_preparation_or_original_generation(database, monkeypatch, fault):
    from dataclasses import replace
    from uuid import uuid4

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_release import PgJointReleaseApplication
    if fault == 'prepared':
        t, service = await configured(database, provisional_quotes=True, maker_cycle=True)
        original = service.runtime._commit

        async def stop(transaction):
            await original(transaction)
            if _current_trace.get().operation == 'quote_birth_prepare':
                raise ConnectionError('synthetic loss after preparation')
        with monkeypatch.context() as patch:
            patch.setattr(service.runtime, '_commit', stop)
            with pytest.raises(StorageError):
                await service.quote('prepared-original', TradeType.BUY, 0)
        ticket = await database[0].fetchval('SELECT ticket_id FROM life_financial.quote_births')
        # Join valid late recorder callbacks before testing the consumption
        # gate; a stale-version refusal would not exercise that gate.
        service = await cold_observer(t, service)
    else:
        t, service, ticket = await stranded(database, monkeypatch)
    c = database[0]
    try:
        before = await service.financial.snapshot(t.cfg.binding)
        if fault == 'prepared':
            assert await c.fetchval('SELECT count(*) FROM life_financial.send_attempts') == 1
            with pytest.raises(StorageError, match='PG_BIRTH_RETIREMENT_REQUIRES_UNCONSUMED'):
                await service.retire_unprepared_birth('never-retire-prepared', ticket)
            with pytest.raises(StorageError, match='PG_UNUSED_RELEASE_ORIGIN_REQUIRED'):
                await service.release_unused_claim('never-release-prepared', ticket)
        else:
            await service.retire_unprepared_birth('retire-original', ticket)
            # Original retired birth remains readable to the account. A source
            # assembled for a different generation still cannot release it.
            assert (await PgQuoteBirthRepository(service.financial).read(ticket)).historical
            collector = service.collector
            collector.swap.owner = replace(t.owner, generation=uuid4())
            with pytest.raises(StorageError):
                await PgJointReleaseApplication(collector).release_unused('foreign-release', ticket)
            collector.swap.owner = t.owner
        after = await service.financial.snapshot(t.cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert await c.fetchval('SELECT count(*) FROM life_financial.unused_claim_releases') == 0
        assert await c.fetchval('SELECT count(*) FROM life_financial.shared_capital_claims') == 1
        assert not t.service_orders
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_unused_claim_release_upgrade_needs_no_direct_birth_table_access(database, monkeypatch):
    t, service, ticket = await stranded(database, monkeypatch)
    try:
        await service.retire_unprepared_birth('retire-original', ticket)
        # Existing principals gain fixed API execution on upgrade, without
        # gaining SELECT on tables introduced after their original provisioning.
        await database[0].execute('REVOKE SELECT ON life_financial.quote_births,life_financial.quote_birth_retirements FROM life_pg2_runtime')
        result = await service.release_unused_claim('release-original', ticket)
        assert result.released and not result.trading_permission and not t.service_orders
    finally:
        await service.close()
