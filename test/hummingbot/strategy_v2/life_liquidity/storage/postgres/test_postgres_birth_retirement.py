"""Permanent unconsumed-ticket retirement has no monetary release or sender."""
import asyncio
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_quote_birth import (
    MODEL,
    born,
    prepare_birth,
)

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace


@pytest.mark.asyncio
async def test_retired_birth_keeps_original_identity_and_money_but_unblocks_new_ticket(database, monkeypatch):
    repo, cfg, owner, births, intent = await born(database)
    c = database[0]
    try:
        ticket = await births.issue('ticket-old', intent, model_hash=fingerprint(MODEL),
                                    expected_version=(await repo.snapshot(cfg.binding)).cut.checkpoint.revision)
        before = await repo.snapshot(cfg.binding)
        result = await births.retire('retire-old', ticket.ticket_id, cut=before.cut, owner=owner)
        assert result.retired and not result.trading_permission and not result.replayed
        assert result.ticket_id == ticket.ticket_id
        assert (await births.retire('retire-old', ticket.ticket_id, cut=before.cut, owner=owner)).replayed
        after = await repo.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert after.cut.checkpoint.revision == before.cut.checkpoint.revision
        assert after.cut != before.cut
        assert await births.read(ticket.ticket_id) == replace(ticket, historical=True)
        assert (await births.lookup_retirement('retire-old')).replayed
        assert await births.lookup_retirement('missing') is None
        with monkeypatch.context() as patch:
            async def original_ticket(*args, **kwargs):
                return ticket
            patch.setattr(births, 'issue', original_ticket)
            with pytest.raises(StorageError, match='PG_BIRTH_IDENTITY_RETIRED'):
                await prepare_birth(database, repo, cfg, owner, births, intent, 7)
        cold = PgQuoteBirthRepository(repo)
        with pytest.raises(StorageError, match='PG_BIRTH_IDENTITY_USED'):
            await cold.issue('ticket-reused', intent, model_hash=fingerprint(MODEL), expected_version=after.cut.checkpoint.revision)
        new = await births.issue('ticket-new', replace(intent, intent_id='fresh-business-intent'),
                                 model_hash=fingerprint(MODEL), expected_version=after.cut.checkpoint.revision)
        assert new.action.intent_id != ticket.action.intent_id
        assert await c.fetchval('SELECT count(*) FROM life_financial.quote_births') == 2
        assert await c.fetchval('SELECT count(*) FROM life_financial.quote_birth_retirements') == 1
        assert await c.fetchval('SELECT count(*) FROM life_financial.send_attempts') == 0
        assert (await repo.snapshot(cfg.binding)).balance == before.balance
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('damage', ['hash', 'cut_null', 'version_null', 'time_null', 'receipt', 'birth_hash'])
async def test_retirement_history_damage_is_detected_on_next_read_without_account_version_change(database, damage):
    repo, cfg, owner, births, intent = await born(database)
    c = database[0]
    try:
        ticket = await births.issue('ticket-old', intent, model_hash=fingerprint(MODEL),
                                    expected_version=(await repo.snapshot(cfg.binding)).cut.checkpoint.revision)
        await births.retire('retire-old', ticket.ticket_id, cut=(await repo.snapshot(cfg.binding)).cut, owner=owner)
        version = await c.fetchval('SELECT version FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        await c.execute('ALTER TABLE life_financial.quote_birth_retirements DISABLE TRIGGER USER')
        if damage == 'hash':
            await c.execute("UPDATE life_financial.quote_birth_retirements SET body_hash=repeat('0',64)")
        else:
            path, value = {
                'cut_null': ('{request,cut}', 'null'), 'version_null': ('{request,version}', 'null'),
                'time_null': ('{retired_at_ms}', 'null'), 'receipt': ('{receipt,retired}', 'false'),
                'birth_hash': ('{birth_hash}', '"' + '0' * 64 + '"'),
            }[damage]
            await c.execute('UPDATE life_financial.quote_birth_retirements SET body=jsonb_set(body,$1::text[],$2::jsonb)', path.strip('{}').split(','), value)
            # A structural check must not rely only on the stored self-hash.
            await c.execute("UPDATE life_financial.quote_birth_retirements SET body_hash=encode(sha256(convert_to(life_financial.canonical_json_v1(body),'UTF8')),'hex')")
        await c.execute('ALTER TABLE life_financial.quote_birth_retirements ENABLE TRIGGER USER')
        assert await c.fetchval('SELECT version FROM life_financial.accounts WHERE account_id=$1', cfg.account_id) == version
        with pytest.raises(StorageError, match='PG_BIRTH_UNVERIFIED'):
            await births.lookup_retirement('retire-old')
        with pytest.raises(StorageError, match='PG_BIRTH_UNVERIFIED'):
            await births.read(ticket.ticket_id)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['consumed', 'owner', 'stale_cut', 'changed_ticket', 'duplicate_id'])
async def test_birth_retirement_refuses_consumption_or_changed_origin(database, fault):
    repo, cfg, owner, births, intent = await born(database)
    c = database[0]
    try:
        if fault == 'consumed':
            ticket = await prepare_birth(database, repo, cfg, owner, births, intent, 0)
        else:
            ticket = await births.issue('ticket-old', intent, model_hash=fingerprint(MODEL),
                                        expected_version=(await repo.snapshot(cfg.binding)).cut.checkpoint.revision)
        cut = (await repo.snapshot(cfg.binding)).cut
        if fault == 'stale_cut':
            await births.retire('retire-once', ticket.ticket_id, cut=cut, owner=owner)
        elif fault == 'duplicate_id':
            await births.retire('retire-once', ticket.ticket_id, cut=cut, owner=owner)
            cut = (await repo.snapshot(cfg.binding)).cut
        original = await c.fetchval('SELECT to_jsonb(t) FROM life_financial.quote_births t WHERE ticket_id=$1', ticket.ticket_id)
        kwargs = dict(cut=cut, owner=owner)
        if fault == 'owner':
            from uuid import uuid4
            kwargs['owner'] = replace(owner, generation=uuid4())
        target = 'missing-ticket' if fault in ('changed_ticket', 'duplicate_id') else ticket.ticket_id
        with pytest.raises(StorageError):
            await births.retire('retire-once' if fault == 'duplicate_id' else 'retire-refused', target, **kwargs)
        assert await c.fetchval('SELECT to_jsonb(t) FROM life_financial.quote_births t WHERE ticket_id=$1', ticket.ticket_id) == original
        assert await c.fetchval('SELECT count(*) FROM life_financial.quote_birth_retirements') == int(fault in ('stale_cut', 'duplicate_id'))
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('winner', ['quote_birth_retire', 'quote_birth_prepare'])
async def test_retirement_and_original_prepare_serialize_to_one_permanent_outcome(database, monkeypatch, winner):
    repo, cfg, owner, births, intent = await born(database)
    c = database[0]
    original_prepare = births.prepare
    captured = []

    async def capture(ticket, command, facts, **options):
        captured.append((ticket, command, facts, options))

    try:
        with monkeypatch.context() as patch:
            patch.setattr(births, 'prepare', capture)
            ticket = await prepare_birth(database, repo, cfg, owner, births, intent, 0)
        _, command, facts, options = captured[0]
        before = await repo.snapshot(cfg.binding)
        entered, release, winner_pids = asyncio.Event(), asyncio.Event(), []
        original_commit = repo._runtime._commit

        async def gated(transaction):
            if _current_trace.get().operation == winner:
                winner_pids.append(_current_trace.get().backend_pid)
                entered.set()
                await release.wait()
            await original_commit(transaction)
        monkeypatch.setattr(repo._runtime, '_commit', gated)
        works = {
            'quote_birth_prepare': lambda: original_prepare(ticket, command, facts, **options),
            'quote_birth_retire': lambda: births.retire('race-retire', ticket.ticket_id, cut=before.cut, owner=owner),
        }
        first = asyncio.create_task(works[winner]())
        async with asyncio.timeout(5):
            await entered.wait()
        loser = next(k for k in works if k != winner)
        second = asyncio.create_task(works[loser]())
        try:
            # Observe the actual primary lock conflict before releasing the
            # winner. Scheduling two tasks alone does not prove overlap.
            async with asyncio.timeout(.75):
                while not await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND $1=ANY(pg_blocking_pids(pid)))', winner_pids[0]):
                    await asyncio.sleep(.001)
        finally:
            release.set()
            # Even a failed overlap assertion must join the physical commands.
            results = await asyncio.gather(first, second, return_exceptions=True)
        assert not isinstance(results[0], BaseException) and isinstance(results[1], StorageError)
        prepared = int(winner == 'quote_birth_prepare')
        assert await c.fetchval('SELECT count(*) FROM life_financial.send_attempts') == prepared
        assert await c.fetchval('SELECT count(*) FROM life_financial.quote_birth_consumptions') == prepared
        assert await c.fetchval('SELECT count(*) FROM life_financial.quote_birth_retirements') == 1 - prepared
        after = await repo.snapshot(cfg.binding)
        assert after.balance.total_base_life == before.balance.total_base_life
        assert after.balance.total_quote_usdt == before.balance.total_quote_usdt
        assert (after.balance.reserved_quote_usdt > 0) is bool(prepared)
        assert await births.read(ticket.ticket_id) == replace(ticket, historical=True)
    finally:
        await repo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('committed', [False, True])
async def test_retirement_lost_commit_recovers_one_tombstone_without_releasing_money(database, monkeypatch, committed):
    repo, cfg, owner, births, intent = await born(database)
    admin = await asyncpg.connect(database[1])
    original_commit = repo._runtime._commit
    lost = []

    async def unknown(transaction):
        if _current_trace.get().operation != 'quote_birth_retire':
            return await original_commit(transaction)
        assert not lost
        if committed:
            await original_commit(transaction)
        assert await admin.fetchval('SELECT pg_terminate_backend($1)', _current_trace.get().backend_pid)
        lost.append(True)
        raise ConnectionError('synthetic retirement response loss')
    try:
        ticket = await births.issue('ticket-old', intent, model_hash=fingerprint(MODEL),
                                    expected_version=(await repo.snapshot(cfg.binding)).cut.checkpoint.revision)
        before = await repo.snapshot(cfg.binding)
        with monkeypatch.context() as patch:
            patch.setattr(repo._runtime, '_commit', unknown)
            with pytest.raises(StorageError, match='PG_COMMIT_UNKNOWN'):
                await births.retire('retire-once', ticket.ticket_id, cut=before.cut, owner=owner)
        cold = PgQuoteBirthRepository(repo)
        readback = await cold.lookup_retirement('retire-once')
        assert (readback is not None) is committed
        result = await cold.retire('retire-once', ticket.ticket_id, cut=before.cut, owner=owner)
        assert result.retired and result.replayed is committed and not result.trading_permission
        assert await database[0].fetchval('SELECT count(*) FROM life_financial.quote_birth_retirements') == 1
        after = await repo.snapshot(cfg.binding)
        assert after.balance == before.balance and after.budget == before.budget
        assert after.cut.checkpoint.revision == before.cut.checkpoint.revision
    finally:
        await admin.close()
        await repo.close()
