"""Original lost PREPARING receipts during coupled outer monetary recovery."""
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_core_cashflows import restore
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_joint import apply_recovery, setup

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand


async def refresh_repaired_account(database, financial, owner, previous):
    from dataclasses import replace

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.carry import PgCarryRepository
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_capital import PgSharedCapitalRepository
    now = await database[0].fetchval('SELECT clock_timestamp()')
    at = int(now.timestamp() * 1000)
    sequence = max(previous.account.snapshot.sequence, previous.mark.sequence, previous.index.sequence,
                   previous.funding.sequence, previous.terms.sequence) + 1
    account = replace(previous.account.snapshot, sequence=sequence, observed_at_ms=at)
    captured = replace(previous, account=replace(previous.account, snapshot=account),
                       mark=replace(previous.mark, sequence=sequence, observed_at_ms=at),
                       index=replace(previous.index, sequence=sequence, observed_at_ms=at),
                       funding=replace(previous.funding, sequence=sequence, observed_at_ms=at, coverage_through_ms=at),
                       terms=replace(previous.terms, sequence=sequence, observed_at_ms=at))
    capital = PgSharedCapitalRepository(financial, owner=owner, observation=lambda: account, max_rows=64, max_bytes=1048576)
    carry = PgCarryRepository(capital, observation=lambda: captured, max_rows=64, max_bytes=1048576)
    assert (await capital.check_capital(capital.binding)).allowed
    assert (await carry.check_carry(capital.binding)).allowed
    return capital


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('stage', ['outer_before_cash', 'wallet_after', 'receipt', 'cash_done'])
async def test_original_preparing_receipt_retains_holds_until_explicit_original_unsent_abort(database, tmp_path, monkeypatch, side, stage):
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, stage, spot_side=side, spot_unsent=True, funding=D('-.1'), file_result=expected)
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        original = await c.fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='spot-prepare'", cfg.account_id)
        assert len(claims) == 2 and before.budget.session_subsidy_committed_usdt == D('.5')
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        _, repair, _ = await apply_recovery(database, batch, command, facts, store)
        repaired = await cold.snapshot(cfg.binding)
        assert repaired.balance.total_base_life == expected['balance'].total_base_life
        assert repaired.balance.total_quote_usdt == expected['balance'].total_quote_usdt
        assert repaired.budget.session_subsidy_committed_usdt == D('.5')
        assert (await cold.attempt('spotintent', cfg.binding)).state.value == 'PREPARED'
        assert await capital.claims(cfg.binding) == claims
        # The actual File full-resume oracle aborts its original unsent order.
        # Native monetary repair does not silently do that release. Run the
        # existing original unsent proof as an explicit lifecycle command.
        abort = SpotLifecycleCommand('explicit-original-abort', 'spot-prepare', 'spotintent', 'spotwire', 'ABORT_PREPARED')
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
        with pytest.raises(StorageError, match='PG_ABORT_NOT_PROVEN_UNSENT'):
            await cold.apply_lifecycle(abort, cut=repaired.cut, owner=owner)
        # Recovery readback is not a renewed source. Collect a new synthetic
        # account/carry cohort after the money write before requesting release.
        capital = await refresh_repaired_account(database, cold, owner, repair.replacement.funding.bundle)
        await cold.apply_lifecycle(abort, cut=(await cold.snapshot(cfg.binding)).cut, owner=owner)
        await cold.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        assert await cold.balance(cfg.binding) == expected['balance']
        assert await cold.budget(cfg.binding) == expected['budget']
        assert await capital.claims(cfg.binding) == claims
        assert (await importer.lookup(command.operation_id)).replayed
        assert await c.fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='spot-prepare'", cfg.account_id) == original
        assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=$1 AND operation_id='arm:spot-prepare')", cfg.account_id)
        assert await c.fetchval('SELECT halted FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('part', ['wal', 'principal', 'subsidy', 'receipt'])
@pytest.mark.parametrize('written', [False, True])
async def test_original_outer_abort_interruption_does_not_repeat_principal_or_budget_release(database, tmp_path, monkeypatch, side, part, written):
    import json

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import fingerprint
    from hummingbot.strategy_v2.life_liquidity.storage.financial import PrepareSpot
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, f'abort_{part}_{"after" if written else "before"}',
        spot_side=side, spot_unsent=True, funding=D('-.1'), file_result=expected)
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        resources = json.loads(source.checkpoint_bytes)['state']['resources']
        spot = PrepareSpot.from_json(resources['commands']['spot-prepare']['command'])
        original_id = 'jointunsent:' + fingerprint(spot.json())
        original = await c.fetchval('SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id=$2', cfg.account_id, original_id)
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        assert original and before.budget.session_subsidy_committed_usdt == 0
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        await apply_recovery(database, batch, command, facts, store)
        await cold.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        assert await cold.balance(cfg.binding) == expected['balance']
        assert await cold.budget(cfg.binding) == expected['budget']
        assert await capital.claims(cfg.binding) == claims
        assert (await importer.lookup(command.operation_id)).replayed
        assert await c.fetchval('SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id=$2', cfg.account_id, original_id) == original
        assert not await c.fetchval("SELECT EXISTS(SELECT 1 FROM life_financial.operations WHERE account_id=$1 AND operation_id='arm:spot-prepare')", cfg.account_id)
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()
        if cold is not None:
            await cold.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('part', ['wal', 'principal', 'subsidy', 'receipt'])
async def test_missing_independent_native_original_abort_retains_every_hold(database, tmp_path, monkeypatch, part):
    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
    values, _, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, f'abort_{part}_after', spot_side='BUY', spot_unsent=True, funding=D('-.1'), native_abort=False)
    financial, cfg = values[:2]
    try:
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        with pytest.raises(StorageError):
            await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget, after.cut) == (before.balance, before.budget, before.cut)
        assert await values[5].claims(cfg.binding) == claims
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['REQUEST_CANCEL', 'RECORD_CANCEL_ATTEMPT'])
@pytest.mark.parametrize('part', ['wal', 'principal', 'receipt'])
@pytest.mark.parametrize('written', [False, True])
async def test_interrupted_cancel_finishes_original_bookkeeping_before_retiring_outer_money(database, tmp_path, monkeypatch, action, part, written):
    import json
    expected = {}
    values, batch, command, cut, facts, approval, importer, store, source = await setup(
        database, tmp_path, monkeypatch, 'wallet_after', spot_side='BUY', funding=D('-.1'), file_result=expected,
        spot_cancel=(action, part, written))
    financial, cfg, owner = values[:3]
    c, cold = database[0], None
    try:
        resources = json.loads(source.checkpoint_bytes)['state']['resources']
        lifecycle = resources['lifecycle_operations']['original-spot-cancel']
        assert lifecycle['state'] == 'APPLIED' and lifecycle['command']['action'] == action
        original = await c.fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='original-spot-cancel'", cfg.account_id)
        before, claims = await financial.snapshot(cfg.binding), await values[5].claims(cfg.binding)
        await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)
        after = await financial.snapshot(cfg.binding)
        assert (after.balance, after.budget) == (before.balance, before.budget)
        await financial.close()
        cold, capital, batch, importer = await restore(cfg, owner, c)
        await apply_recovery(database, batch, command, facts, store)
        await cold.close()
        cold, capital, _, importer = await restore(cfg, owner, c)
        assert await cold.balance(cfg.binding) == expected['balance']
        assert await cold.budget(cfg.binding) == expected['budget']
        assert await capital.claims(cfg.binding) == claims
        attempt = await cold.attempt('spotintent', cfg.binding)
        assert attempt.cancel_requested and attempt.cancel_attempts == (1 if action == 'RECORD_CANCEL_ATTEMPT' else 0)
        assert attempt.state.value != 'TERMINAL'
        assert (await importer.lookup(command.operation_id)).replayed
        assert await c.fetchval("SELECT to_jsonb(o) FROM life_financial.operations o WHERE account_id=$1 AND operation_id='original-spot-cancel'", cfg.account_id) == original
        assert await store.frozen_legacy_applying_source(store.binding) == source
    finally:
        await financial.close()
        await store.close()
        if cold is not None:
            await cold.close()
