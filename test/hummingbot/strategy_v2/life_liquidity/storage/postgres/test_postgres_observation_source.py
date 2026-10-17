"""Actual signed GET recovery source; pending finance can never become a send grant."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_capital import flow
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_source import private_http
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_account_source import source
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_transport import (
    AGES,
    URL,
    transport,
)
from uuid import uuid4

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_account_source import PgObservationAccountSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_transport import PgQuoteRequestGuard


def arguments(t):
    return dict(connector=t.connector, max_uid_age_ms=1000, max_auth_age_ms=1000,
                max_clock_skew_ms=100, max_pending=2, collection_timeout_ms=1000)


async def pending(t, database):
    c = database[0]
    at = await c.fetchval('SELECT clock_timestamp()')
    ms = int(at.timestamp() * 1000)
    await c.execute('INSERT INTO life_financial.spot_fill_policies VALUES($1,$2,1000,4096)', t.cfg.account_id, t.cfg.binding.policy_hash)
    await c.execute("INSERT INTO life_financial.spot_capital_policies VALUES($1,$2,5,10,1,$3,'100',4096)", t.cfg.account_id, t.cfg.binding.policy_hash, at - timedelta(seconds=1))
    await c.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::double precision/1000),$6)',
                    t.cfg.account_id, '101', 'USDT', D('-11'), ms, b'synthetic-preverified-import')
    result = await t.repo.apply_cashflow(flow(at, ms, signed_amount=D('-11')), cut=(await t.repo.snapshot(t.cfg.binding)).cut, owner=t.owner)
    assert result.state == 'RECOVERY_REQUIRED'


@pytest.mark.asyncio
async def test_actual_pending_finance_allows_authenticated_recovery_reads_without_quote_authority(database):
    t = await transport(database)
    requests = private_http(t)
    try:
        await pending(t, database)
        before = await t.repo.snapshot(t.cfg.binding)
        assert before.recovery_required
        with pytest.raises(StorageError, match='PG_QUOTE_POLICY_UNVERIFIED'):
            await source(t)
        observed = await PgObservationAccountSource.create(t.journal, **arguments(t))
        reader = PgSpotAccountReader(observed, max_reads=4, max_bytes=1048576, timeout_ms=1000)
        async with reader.collection():
            assert await reader.get_account_bills_page(after='100') == {'code': '0', 'data': []}
            assert await reader.get_spot_cash_balances() == {'code': '0', 'data': []}
        assert len(requests) == 4 and all(row['data'] is None for row in requests)
        after = await t.repo.snapshot(t.cfg.binding)
        assert after.cut == before.cut and after.balance == before.balance and after.budget == before.budget and after.recovery_required
        assert not observed.check(None)
        for read in (lambda: observed.age_limits, lambda: observed.guard(action=t.create, preparation=t.preparation)):
            with pytest.raises(StorageError, match='HAS_NO_SEND_AUTHORITY'):
                read()
        with pytest.raises(StorageError, match='HAS_NO_SEND_AUTHORITY'):
            await observed.capture(None)
        with pytest.raises(StorageError, match='PG_REQUEST_ACCOUNT_SOURCE_MISMATCH'):
            PgQuoteRequestGuard(t.journal, action=t.create, preparation=t.preparation, admission_source=t.facts,
                                endpoint_url=URL, age_limits=AGES, account_source=observed)
        assert await database[0].fetchval("SELECT count(*) FROM life_financial.spot_cashflow_facts WHERE state='RECOVERY_REQUIRED'") == 1
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.cashflows') == 0
        assert (await t.repo.attempt('intent1', t.cfg.binding)).state.value == 'PREPARED'
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_direct_unqualified_constructor_is_not_a_reader_factory_fallback(database):
    t = await transport(database)
    try:
        observed = PgObservationAccountSource(t.journal, **arguments(t))
        with pytest.raises(StorageError, match='PG_FINANCIAL_SOURCE_CONFIG_INVALID'):
            PgSpotAccountReader(observed, max_reads=4, max_bytes=1048576, timeout_ms=1000)
    finally:
        await t.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['owner', 'binding', 'closing', 'uid'])
async def test_invalid_original_identity_is_denied_without_private_financial_collection(database, fault):
    t = await transport(database)
    requests = private_http(t)
    try:
        if fault == 'owner':
            t.journal.owner = replace(t.owner, epoch=2)
        elif fault == 'binding':
            t.journal.binding = replace(t.cfg.binding, account_uid='999')
        elif fault == 'closing':
            t.repo._runtime._closing = True
        if fault != 'uid':
            with pytest.raises(StorageError):
                await PgObservationAccountSource.create(t.journal, **arguments(t))
            assert requests == []
        else:
            observed = await PgObservationAccountSource.create(t.journal, **arguments(t))
            reader = PgSpotAccountReader(observed, max_reads=4, max_bytes=1048576, timeout_ms=1000)
            original = t.session.request

            async def bad(**kwargs):
                response = await original(**kwargs)

                async def body():
                    return {'code': '0', 'data': [{'uid': '999'}]}
                response.json = body
                return response
            t.session.request = bad
            with pytest.raises(StorageError, match='PG_ACCOUNT_UID_MISMATCH'):
                async with reader.collection():
                    await reader.get_spot_cash_balances()
            assert len(requests) == 1 and not reader.runtime.healthy
    finally:
        t.repo._runtime._closing = False
        await t.close()


@pytest.mark.asyncio
async def test_original_context_is_rechecked_after_the_last_primary_factory_await(database, monkeypatch):
    from types import SimpleNamespace
    t = await transport(database)
    requests = private_http(t)
    original = t.repo._runtime._admit

    async def changed(*args, **kwargs):
        value = await original(*args, **kwargs)
        t.journal.repository = SimpleNamespace(binding=t.cfg.binding, _runtime=t.repo._runtime)
        return value
    monkeypatch.setattr(t.repo._runtime, '_admit', changed)
    try:
        with pytest.raises(StorageError, match='PG_OBSERVATION_SOURCE_CONTEXT_CHANGED'):
            await PgObservationAccountSource.create(t.journal, **arguments(t))
        assert requests == []
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_new_primary_generation_during_collection_denies_completion_and_retains_money(database):
    t = await transport(database)
    requests = private_http(t)
    try:
        observed = await PgObservationAccountSource.create(t.journal, **arguments(t))
        reader = PgSpotAccountReader(observed, max_reads=4, max_bytes=1048576, timeout_ms=1000)
        before = await t.repo.balance(t.cfg.binding)
        with pytest.raises(StorageError, match='PG_OBSERVATION_OWNER_OR_BINDING_INVALID'):
            async with reader.collection():
                await reader.get_spot_cash_balances()
                # Synthetic privileged ownership change, not a production handoff.
                await database[0].execute('ALTER TABLE life_financial.accounts DISABLE TRIGGER USER')
                await database[0].execute('UPDATE life_financial.accounts SET owner_generation=$2 WHERE account_id=$1', t.cfg.account_id, uuid4())
                await database[0].execute('ALTER TABLE life_financial.accounts ENABLE TRIGGER USER')
        assert not reader.runtime.healthy and all(row['data'] is None for row in requests)
        assert (await t.repo.balance(t.cfg.binding)) == before
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_expired_current_original_owner_can_read_late_facts_without_renewal(database):
    t = await transport(database)
    requests = private_http(t)
    try:
        expired = replace(t.owner, expires_at=t.owner.valid_from + timedelta(milliseconds=1))
        await database[0].execute('ALTER TABLE life_financial.accounts DISABLE TRIGGER USER')
        await database[0].execute("UPDATE life_financial.accounts SET owner_until=$2,mode='RECOVERY_ONLY',halted=true WHERE account_id=$1", t.cfg.account_id, expired.expires_at)
        await database[0].execute('ALTER TABLE life_financial.accounts ENABLE TRIGGER USER')
        t.journal.owner = expired
        before = await t.repo.snapshot(t.cfg.binding)
        observed = await PgObservationAccountSource.create(t.journal, **arguments(t))
        reader = PgSpotAccountReader(observed, max_reads=4, max_bytes=1048576, timeout_ms=1000)
        async with reader.collection():
            await reader.get_spot_cash_balances()
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut and not observed.check(None)
        assert await database[0].fetchval("SELECT owner_until=$2 AND mode='RECOVERY_ONLY' AND halted FROM life_financial.accounts WHERE account_id=$1", t.cfg.account_id, expired.expires_at)
        assert len(requests) == 3
    finally:
        await t.close()


@pytest.mark.asyncio
async def test_reporter_role_cannot_become_an_authenticated_write_observation_identity(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
    t = await transport(database)
    requests = private_http(t)
    reader = None
    try:
        cfg = replace(t.cfg, dsn=t.cfg.dsn.replace('life_pg2_runtime@', 'life_pg2_reporter@'), principal='life_pg2_reporter', write=False)
        reader = PgSpotRepository(await PgRuntime.open(cfg))
        original = t.journal
        journal = PgQuoteRunnerJournal(reader, owner=t.owner, controller_id=original.controller_id, config_version=original.config_version,
                                       runner_facts=original.runner_facts, local_ready=original.local_ready, clock=original.clock, max_actions=original.max_actions)
        with pytest.raises(StorageError):
            await PgObservationAccountSource.create(journal, **arguments(t))
        assert requests == []
    finally:
        if reader is not None:
            await reader.close()
        await t.close()


@pytest.mark.asyncio
async def test_observation_identity_composes_with_financially_qualified_idle_sdk_dedup(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_dedup import setup

    from hummingbot.connector.utils import TradeFillOrderDetails
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_dedup import PgSpotDedupRecovery
    t, observer, events, fill = await setup(database)
    try:
        before = await t.repo.snapshot(t.cfg.binding)
        observed = await PgObservationAccountSource.create(t.journal, **arguments(t))
        reader = PgSpotAccountReader(observed, max_reads=4, max_bytes=1048576, timeout_ms=1000)
        assert await PgSpotDedupRecovery(reader, events, local_idle=lambda: True).restore() == (1, 1)
        assert t.connector._current_trade_fills == {TradeFillOrderDetails('okx', 'trade1', 'LIFE-USDT')}
        assert t.connector._exchange_order_ids == {'123': 'wire1'} and t.connector.in_flight_orders == {}
        assert (await t.repo.snapshot(t.cfg.binding)).cut == before.cut and not observed.check(None)
    finally:
        await t.close()
