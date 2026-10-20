"""Retired SPOT history is retained without masquerading as a new order read."""
import asyncio
import json
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_joint_source import release_application

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def retired_spot(database):
    t = await release_application(database, spot_fee='USDT')
    await t.application.confirm_terminal('retired-terminal', 'intent1')
    await t.application.retire_claim('retired-release', 'intent1', 'retired-terminal')
    return t


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [91], indirect=True)
async def test_snapshot_reuses_shared_proofs_only_within_its_own_primary_view(database, monkeypatch):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
    load = runtime.load_migrations
    with monkeypatch.context() as patch:
        patch.setattr(runtime, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 91))
        t = await retired_spot(database)
    await t.financial.close()
    admin = await asyncpg.connect(database[1])
    try:
        await admin.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,false)', t.cfg.account_id)
        await admin.execute("SET track_functions='all'")

        async def measured(query):
            async with admin.transaction():
                stats = "SELECT funcname,calls FROM pg_stat_xact_user_functions WHERE schemaname='life_financial'"
                before = {r['funcname']: r['calls'] for r in await admin.fetch(stats)}
                result = await admin.fetchval(query, t.cfg.account_id)
                if isinstance(result, str):
                    result = json.loads(result)
                counts = {r['funcname']: r['calls'] - before.get(r['funcname'], 0) for r in await admin.fetch(stats)}
                return result, counts

        # Compare the real populated predecessor with the upgraded entrypoint.
        # Both evaluate genuinely expired carry inputs, on the same money and
        # immutable history. No clock or source validity is extended.
        await asyncio.sleep(1.05)
        previous, original_counts = await measured('SELECT life_financial.spot_state($1)')
        assert original_counts['shared_capital_verified'] == 2 and original_counts['shared_release_verified'] == 3
        assert (await migrate(database[0], action='up'))['applied'] == ['000092_snapshot_shared_proof_reuse.sql', '000093_commit_proof_group.sql', '000094_snapshot_financial_proof_reuse.sql', '000095_recorder_verification_reads.sql']
        body, counts = await measured('SELECT life_financial.spot_state($1)')
        assert {k: v for k, v in body.items() if k != 'observed_at'} == {k: v for k, v in previous.items() if k != 'observed_at'}
        assert body['shared_capital']['verified'] and body['shared_release']['verified'] and body['portfolio']['verified']
        assert counts['shared_capital_verified'] == 1
        assert counts['shared_release_verified'] == 1
        # The independent public verifier still builds its own complete proof;
        # a snapshot result never becomes a session or transaction cache.
        verified, independent = await measured('SELECT life_financial.portfolio_verified($1)')
        assert verified and independent['shared_capital_verified'] == 1
        private = 'life_financial.portfolio_after_snapshot_verification(uuid,boolean,boolean)'
        for role in ('public', 'life_pg2_runtime', 'life_pg2_reporter'):
            assert not await admin.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, private)
        before = await admin.fetchrow('SELECT version,(SELECT jsonb_agg(to_jsonb(b) ORDER BY asset) FROM life_financial.balances b WHERE b.account_id=a.account_id) AS balances FROM life_financial.accounts a WHERE account_id=$1', t.cfg.account_id)
        await admin.execute('ALTER TABLE life_financial.shared_claim_releases DISABLE TRIGGER immutable_history')
        await admin.execute("UPDATE life_financial.shared_claim_releases SET basis='{}'::jsonb WHERE account_id=$1", t.cfg.account_id)
        await admin.execute('ALTER TABLE life_financial.shared_claim_releases ENABLE TRIGGER immutable_history')
        corrupt, _ = await measured('SELECT life_financial.spot_state($1)')
        assert not corrupt['shared_capital']['verified'] and not corrupt['shared_release']['verified']
        assert not corrupt['portfolio']['verified'] and corrupt['unverified'] and corrupt['recovery_required']
        assert not await admin.fetchval('SELECT life_financial.portfolio_verified($1)', t.cfg.account_id)
        after = await admin.fetchrow('SELECT version,(SELECT jsonb_agg(to_jsonb(b) ORDER BY asset) FROM life_financial.balances b WHERE b.account_id=a.account_id) AS balances FROM life_financial.accounts a WHERE account_id=$1', t.cfg.account_id)
        assert after == before
    finally:
        await admin.close()


@pytest.mark.asyncio
async def test_no_head_shared_early_return_does_not_hide_corrupt_release_history(database):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import setup
    financial, cfg, *_ = await setup(database)
    await financial.close()
    admin = await asyncpg.connect(database[1])
    try:
        await admin.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,false)', cfg.account_id)
        assert not await admin.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.shared_capital_heads WHERE account_id=$1)', cfg.account_id)
        # Deliberate out-of-band FK corruption exercises the no-head early
        # return. A true capital verdict alone does not verify release history.
        async with admin.transaction():
            await admin.execute("SET LOCAL session_replication_role='replica'")
            await admin.execute('INSERT INTO life_financial.shared_release_policies VALUES($1,$2,32,1048576)', cfg.account_id, cfg.binding.policy_hash)
            await admin.execute("INSERT INTO life_financial.shared_claim_releases VALUES($1,'missing-claim','missing-release','missing-terminal','{}',1)", cfg.account_id)
        body = json.loads(await admin.fetchval('SELECT life_financial.spot_state($1)', cfg.account_id))
        assert body['shared_capital']['verified'] and body['shared_capital']['head'] is None
        assert not body['shared_release']['verified'] and body['unverified'] and body['recovery_required']
        assert not await admin.fetchval('SELECT life_financial.shared_release_verified($1)', cfg.account_id)
    finally:
        await admin.close()


@pytest.mark.asyncio
async def test_retired_spot_keeps_full_history_but_does_not_fetch_or_regrant_its_terminal_order(database):
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.source_fence import JointSourceFence
    t = await retired_spot(database)
    try:
        before = len(t.requests)
        report = await t.joint.collect()
        retired, = [v for v in report.orders if json.loads(v.native_json)['intent_id'] == 'intent1']
        assert json.loads(retired.native_json)['retired_spot'] is True
        assert retired.exchange_json is None  # Historical closure is not a NEW signed status.
        assert retired.trade_ids == (t.spot_fill['tradeId'],)
        reads = t.requests[before:]
        assert not [r for r in reads if r['url'].endswith('/order') and (r.get('params') or {}).get('clOrdId') == 'wire1']
        assert not [r for r in reads if r['url'].endswith('/fills-history') and (r.get('params') or {}).get('ordId') == t.spot_order['ordId']]
        assert len([r for r in reads if r['url'].endswith('/orders-pending')]) == 2
        assert report.product_trade_ids == (('SPOT', t.spot_fill['tradeId']), ('SWAP', 'trade1'))
        with pytest.raises(StorageError, match='HISTORY_ONLY'):
            JointSourceFence(t.joint, report, operation_id='forged-fresh-proof', intent_id='intent1')
        with pytest.raises(StorageError, match='TERMINAL_UNQUALIFIED'):
            await t.application.confirm_terminal('another-terminal', 'intent1')
    finally:
        await t.financial.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['late_fill', 'missing_fill', 'pending_retired', 'corrupt_release'])
async def test_retired_scope_still_rejects_new_missing_live_or_corrupt_facts(database, fault):
    t = await retired_spot(database)
    try:
        before = await database[0].fetchrow('SELECT version,(SELECT jsonb_agg(to_jsonb(b) ORDER BY asset) FROM life_financial.balances b WHERE b.account_id=a.account_id) AS balances FROM life_financial.accounts a WHERE account_id=$1', t.cfg.account_id)
        if fault == 'late_fill':
            t.account_fills['SPOT'].append({**t.spot_fill, 'tradeId': 'late'})
        elif fault == 'missing_fill':
            t.account_fills['SPOT'].clear()
        elif fault == 'pending_retired':
            t.pending['SPOT'] = [{**t.spot_order, 'state': 'live'}]
        else:
            # Privileged corruption is deliberately outside the runtime API.
            await database[0].execute('ALTER TABLE life_financial.shared_claim_releases DISABLE TRIGGER immutable_history')
            await database[0].execute("UPDATE life_financial.shared_claim_releases SET basis='{}'::jsonb WHERE account_id=$1 AND intent_id='intent1'", t.cfg.account_id)
            await database[0].execute('ALTER TABLE life_financial.shared_claim_releases ENABLE TRIGGER immutable_history')
        with pytest.raises(StorageError):
            await t.joint.collect()
        after = await database[0].fetchrow('SELECT version,(SELECT jsonb_agg(to_jsonb(b) ORDER BY asset) FROM life_financial.balances b WHERE b.account_id=a.account_id) AS balances FROM life_financial.accounts a WHERE account_id=$1', t.cfg.account_id)
        assert after == before and t.joint._report is None
        assert not t.session.sent and not t.financial._runtime.healthy
    finally:
        await t.financial.close()
