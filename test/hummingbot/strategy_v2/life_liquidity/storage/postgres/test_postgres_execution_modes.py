"""Signed native execution modes preserve original owner, economics and deadlines."""
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_risk import observe, risk_store
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_risk_markout import refresh

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorKeys


async def execution_store(database):
    keys = OperatorKeys({'operator': b'x' * 32}, {'operator': frozenset(('ACTIVATE', 'DRAIN', 'REVOKE', 'RECOVER', 'OBSERVE_SESSION', 'INVENTORY'))})
    values = await spot(database, configuration_body=lambda _: dict(operator_verifier=keys.verifier().policy()))
    repo, cfg, owner, risk = await risk_store(database, financial_values=values)
    await database[0].execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,true)', cfg.account_id)
    await database[0].execute('INSERT INTO life_financial.control_policies VALUES($1,$2,1000,64,1048576,60000)', cfg.account_id, cfg.binding.policy_hash)
    body = dict(campaign_id='execution1', quote_budget=dict(campaign='6', day='3', session='.24'), exit_budget=dict(campaign='4', day='2', session='1.76'))
    await database[0].execute('INSERT INTO life_financial.execution_policies VALUES($1,$2,$3,$4::jsonb)', cfg.account_id, cfg.binding.policy_hash, fingerprint(body), json.dumps(body))
    await refresh(database, repo, cfg, owner, risk, 'execution-nav')
    ready = await observe(database, repo, cfg, owner, risk, 'normal')
    return repo, cfg, owner, risk, ready, keys, body


@pytest.mark.asyncio
async def test_enabled_execution_policy_requires_an_explicit_signed_initial_mode(database):
    repo, cfg, owner, risk, ready, keys, body = await execution_store(database)
    try:
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_EXECUTION_MODE_REQUIRED'):
            await repo.prepare(order(owner, quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
    finally:
        await repo.close()


async def choose_mode(database, repo, cfg, owner, keys, body, mode='MM', operation='mode1'):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import signed

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import NativeExecutionMode, PgControlRepository
    control = PgControlRepository(database[0], repo, verifier=keys.verifier())
    command = NativeExecutionMode(operation, mode, body['campaign_id'], fingerprint(body), repo._runtime._risk_observer)
    arguments = await signed(database, repo, cfg, keys, command)
    return control, command, arguments, await control.set_execution_mode(command, **arguments)


@pytest.mark.asyncio
async def test_signed_native_mm_mode_keeps_owner_money_and_has_no_replay_activation(database):
    repo, cfg, owner, risk, ready, keys, body = await execution_store(database)
    try:
        before = await repo.snapshot(cfg.binding)
        control, command, arguments, result = await choose_mode(database, repo, cfg, owner, keys, body)
        assert result.mode == 'MM' and not result.replayed and not result.trading_permission
        assert (await control.set_execution_mode(command, **arguments)).replayed
        after = await repo.snapshot(cfg.binding)
        assert (before.balance, before.budget, before.session) == (after.balance, after.budget, after.session)
        await refresh(database, repo, cfg, owner, risk, 'after-mode')
        await repo.prepare(order(owner, quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        assert (await control.lookup(command.operation_id)).trading_permission is False
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['signature', 'wrong-role', 'changed-policy'])
async def test_native_execution_mode_requires_original_approval_and_policy(database, fault):
    from dataclasses import replace
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import signed

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import NativeExecutionMode, PgControlRepository
    repo, cfg, owner, risk, ready, keys, body = await execution_store(database)
    try:
        control = PgControlRepository(database[0], repo, verifier=keys.verifier())
        command = NativeExecutionMode('mode1', 'MM', body['campaign_id'], fingerprint(body), repo._runtime._risk_observer)
        args = await signed(database, repo, cfg, keys, command)
        if fault == 'signature':
            args['approval'] = replace(args['approval'], signature='00' * 64)
        elif fault == 'wrong-role':
            args['approval'] = replace(args['approval'], role='DRAIN')
        else:
            command = replace(command, policy_digest='f' * 64)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await control.set_execution_mode(command, **args)
        after = await repo.snapshot(cfg.binding)
        assert (before.balance, before.budget) == (after.balance, after.budget)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.control_records') == 0
        assert not repo._runtime.healthy
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_mm_cannot_borrow_the_independent_exit_budget(database):
    repo, cfg, owner, risk, ready, keys, body = await execution_store(database)
    try:
        await choose_mode(database, repo, cfg, owner, keys, body)
        await refresh(database, repo, cfg, owner, risk, 'mm-budget-nav')
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_EXECUTION_QUOTE_BUDGET_EXCEEDED'):
            await repo.prepare(order(owner, quantity_base=D('.2'), subsidy_quote_usdt=D('.25'), risk_epoch=ready.risk_epoch), cut=before.cut, owner=owner)
        after = await repo.snapshot(cfg.binding)
        assert (before.balance, before.budget) == (after.balance, after.budget)
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_native_mode_change_with_an_unresolved_original_order_is_refused(database):
    repo, cfg, owner, risk, ready, keys, body = await execution_store(database)
    try:
        await choose_mode(database, repo, cfg, owner, keys, body)
        await refresh(database, repo, cfg, owner, risk, 'pending-nav')
        await repo.prepare(order(owner, quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_EXECUTION_MODE_QUIESCENCE_REQUIRED'):
            await choose_mode(database, repo, cfg, owner, keys, body, operation='mode2')
        after = await repo.snapshot(cfg.binding)
        assert (before.balance, before.budget) == (after.balance, after.budget)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.control_records') == 1
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_native_mode_unknown_commit_retains_original_approval_and_never_heals_admission(database, monkeypatch, committed):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import signed

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import NativeExecutionMode, PgControlRepository
    repo, cfg, owner, risk, ready, keys, body = await execution_store(database)
    try:
        control = PgControlRepository(database[0], repo, verifier=keys.verifier())
        command = NativeExecutionMode('unknown', 'MM', body['campaign_id'], fingerprint(body), repo._runtime._risk_observer)
        args = await signed(database, repo, cfg, keys, command)
        original = control._commit
        once = True

        async def lost(tx):
            nonlocal once
            if not once:
                return await original(tx)
            once = False
            if committed:
                await original(tx)
            raise ConnectionError('synthetic mode COMMIT reply lost')
        monkeypatch.setattr(control, '_commit', lost)
        with pytest.raises(CommitUnknown):
            await control.set_execution_mode(command, **args)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.control_records') == int(committed)
        receipt = await control.set_execution_mode(command, **args)
        assert receipt.replayed is committed and not receipt.trading_permission
        assert not repo._runtime.healthy
    finally:
        await repo.close()


@pytest.mark.asyncio
async def test_signed_mode_expiring_during_actual_commit_cannot_change_permission(database, monkeypatch):
    from datetime import timedelta
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_control import evidence

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import NativeExecutionMode, PgControlRepository
    repo, cfg, owner, risk, ready, keys, body = await execution_store(database)
    try:
        control = PgControlRepository(database[0], repo, verifier=keys.verifier())
        command = NativeExecutionMode('expire', 'MM', body['campaign_id'], fingerprint(body), repo._runtime._risk_observer)
        cut, facts = await evidence(database, repo, cfg)
        expiry = facts.observed_at + timedelta(milliseconds=700)
        approval = keys.sign('operator', cfg.binding, 'INVENTORY', command.json(), cut, facts, issued_at=facts.observed_at - timedelta(seconds=1), expires_at=expiry)
        original = control._commit

        async def late(tx):
            await tx._connection.execute('SELECT pg_sleep(greatest(0,extract(epoch FROM ($1::timestamptz-clock_timestamp())))+.01)', expiry)
            await original(tx)
        monkeypatch.setattr(control, '_commit', late)
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError, match='PG_CONTROL_EXPIRED_OR_CHANGED_AT_COMMIT'):
            await control.set_execution_mode(command, approval=approval, cut=cut, facts=facts)
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.control_records') == 0
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['record', 'receipt', 'missing'])
async def test_corrupt_or_missing_original_mode_cannot_rearm(database, fault):
    repo, cfg, owner, risk, ready, keys, body = await execution_store(database)
    try:
        control, command, args, _ = await choose_mode(database, repo, cfg, owner, keys, body)
        table = 'operations' if fault == 'receipt' else 'control_records'
        await database[0].execute('ALTER TABLE life_financial.' + table + ' DISABLE TRIGGER USER')
        query = "UPDATE life_financial.operations SET receipt=receipt-'mode' WHERE operation_id='mode1'" if fault == 'receipt' else "UPDATE life_financial.control_records SET body=body #- '{payload,observer_id}'" if fault == 'record' else 'DELETE FROM life_financial.control_records'
        await database[0].execute(query)
        await database[0].execute('ALTER TABLE life_financial.' + table + ' ENABLE TRIGGER USER')
        # Missing history is refused by the financial execution gate even if
        # the general control journal no longer has a row to audit.
        before = await repo.snapshot(cfg.binding)
        with pytest.raises(StorageError):
            await repo.prepare(order(owner, quantity_base=D('.2'), risk_epoch=ready.risk_epoch), cut=before.cut, owner=owner)
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
    finally:
        await repo.close()
