"""Real 54→55 snapshots compared with the retained pre-upgrade implementations."""
import asyncio
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_carry import setup as carry_setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_shared_capital import (
    setup as shared_setup,
)
from uuid import uuid4

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres import runtime as runtime_module
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import migrate
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime

LAYERS = ('spot_state_v7', 'spot_state_v13', 'spot_state_v14', 'spot_state_v15',
          'spot_state_before_carry', 'spot_state_before_swap', 'spot_state_before_swap_accounting',
          'spot_state_before_paid_funding', 'spot_state_before_portfolio', 'spot_state_before_swap_terminal',
          'spot_state_before_shared_release', 'spot_state_before_joint_batch', 'spot_state_before_hedge',
          'spot_state_before_swap_boundary', 'spot_state_before_terminal_repair', 'spot_state')


@pytest.mark.asyncio
async def test_body_entrypoints_are_private_and_keep_no_digest_or_capture_time(database):
    c = database[0]
    for name in LAYERS:
        signature = 'life_financial.' + name.replace('spot_state', 'spot_body') + '(uuid)'
        assert await c.fetchval('SELECT to_regprocedure($1) IS NOT NULL', signature)
        for role in ('life_pg2_runtime', 'life_pg2_reporter', 'public'):
            assert not await c.fetchval("SELECT has_function_privilege($1,$2,'EXECUTE')", role, signature)


@pytest.mark.asyncio
@pytest.mark.parametrize('database', [54], indirect=True)
@pytest.mark.parametrize('state', ['no_carry', 'pending', 'qualified', 'expired', 'corrupt_carry', 'corrupt_balance',
                                   'spot_partial', 'legacy_pending', 'legacy_recovered'])
async def test_actual_upgrade_keeps_every_old_layer_body_digest_flags_and_scope(database, monkeypatch, tmp_path, state):
    c = database[0]
    load = runtime_module.load_migrations
    old_fill = None
    with monkeypatch.context() as patch:
        patch.setattr(runtime_module, 'load_migrations', lambda path: tuple(m for m in load(path) if m.version <= 54))
        if state.startswith('legacy_'):
            from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_joint import (
                apply_recovery,
                setup,
            )
            values, batch, command, cut, facts, approval, importer, store, _ = await setup(
                database, tmp_path, monkeypatch, 'wallet_after', funding=D('-.1'), cashflow=('USDT', D('2'), D('1')), cashflow_written=True)
            financial, cfg, owner = values[:3]
            try:
                assert (await importer.import_pending(command, source_store=store, approval=approval, cut=cut, facts=facts)).state == 'RECOVERY_REQUIRED'
                if state == 'legacy_recovered':
                    result, _, _ = await apply_recovery(database, batch, command, facts, store)
                    assert result.state == 'REPAIRED'
            finally:
                await store.close()
        elif state == 'spot_partial':
            from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import (
                filled,
                sent,
            )
            financial, cfg, owner = await sent(database)
            at = await c.fetchval('SELECT clock_timestamp()')
            command, cut = filled(owner, at), (await financial.snapshot(cfg.binding)).cut
            assert (await financial.apply_fills(command, cut=cut, owner=owner)).state == 'APPLIED'
            old_fill = command, cut
        elif state == 'no_carry':
            financial, cfg, owner, *_ = await shared_setup(database)
        else:
            financial, cfg, owner, _, _, capital, policy, feed, repo = await carry_setup(database, refresh_initial_facts=True)
            if state not in ('pending', 'corrupt_balance'):
                assert (await repo.check_carry(cfg.binding)).allowed
        await financial.close()
    if state == 'expired':
        await asyncio.sleep(1.05)
    if state == 'corrupt_carry':
        await c.execute('ALTER TABLE life_financial.operations DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.operations SET receipt=receipt||'{\"checked_at_ms\":1}'::jsonb WHERE account_id=$1 AND command->>'kind'='CARRY_CHECK'", cfg.account_id)
        await c.execute('ALTER TABLE life_financial.operations ENABLE TRIGGER USER')
    if state == 'corrupt_balance':
        await c.execute('ALTER TABLE life_financial.balances DISABLE TRIGGER USER')
        await c.execute("UPDATE life_financial.balances SET reserved=1 WHERE account_id=$1 AND asset='USDT'", cfg.account_id)
        await c.execute('ALTER TABLE life_financial.balances ENABLE TRIGGER USER')
    # Copy the actual old code before migration, with all nested old snapshot
    # calls pointing at this separate oracle. Financial proof functions remain
    # the original independently checked predicates, on the same tables/cut.
    await c.execute('CREATE SCHEMA snapshot_reference')
    prior = {}
    for name in LAYERS:
        signature = 'life_financial.' + name + '(uuid)'
        prior[name] = await c.fetchval('SELECT pg_get_functiondef(to_regprocedure($1))', signature)
    for name, definition in prior.items():
        for other in LAYERS:
            definition = definition.replace('life_financial.' + other + '(', 'snapshot_reference.' + other + '(')
        await c.execute(definition)
        await c.execute(f'REVOKE ALL ON FUNCTION snapshot_reference.{name}(uuid) FROM PUBLIC')
    # Grant only the test oracle entrypoints; no application role gains access
    # to the production bodies introduced by the migration.
    await c.execute('GRANT USAGE ON SCHEMA snapshot_reference TO life_pg2_runtime')
    for name in LAYERS:
        await c.execute(f'GRANT EXECUTE ON FUNCTION snapshot_reference.{name}(uuid) TO life_pg2_runtime')
    await c.execute('INSERT INTO life_financial.access_bindings VALUES(current_user,$1,false)', cfg.account_id)
    assert (await migrate(c, action='up'))['applied'] == ['000055_snapshot_body_pipeline.sql', '000056_legacy_spot_basis.sql',
                                                          '000057_collateral_source_context.sql', '000058_legacy_spot_prefix.sql',
                                                          '000059_legacy_cashflow_prefix.sql', '000060_legacy_derivative_prefix.sql', '000061_legacy_parent_prefix.sql', '000062_legacy_unsent_spot.sql', '000063_legacy_spot_ack_prefix.sql', '000064_legacy_repaired_parent_prefix.sql', '000065_legacy_cancel_prefix.sql', '000066_legacy_terminal_prefix.sql', '000067_legacy_settlement_prefix.sql', '000068_legacy_abort_nav_history.sql', '000069_legacy_joint_spot_claims.sql', '000070_repair_view_reuse.sql', '000071_required_ack_completion.sql', '000072_unfilled_swap_prefix.sql', '000073_joint_provider_routes.sql', '000074_joint_source_deadline.sql', '000075_joint_trade_id_sets.sql']
    cold = await PgRuntime.open(cfg)
    try:
        async def compare_public(connection):
            return await connection.fetchrow("SELECT life_financial.spot_state($1)-'observed_at' AS current, snapshot_reference.spot_state($1)-'observed_at' AS previous", cfg.account_id)
        public = await cold._admit(compare_public, safety=True)
        assert public['current'] == public['previous']
        if old_fill is not None:
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
            count = await c.fetchval('SELECT count(*) FROM life_financial.operations')
            command, cut = old_fill
            receipt = await PgSpotRepository(cold).apply_fills(command, cut=cut, owner=owner)
            assert receipt.replayed and receipt.state == 'APPLIED'
            assert await c.fetchval('SELECT count(*) FROM life_financial.operations') == count
            assert (await cold._admit(compare_public, safety=True))['current'] == public['current']
        if state == 'qualified':
            assert public['current']['carry']['ready'] is True
        # The exhaustive layer comparisons use aged sources in both arms to
        # avoid comparing evaluations on opposite sides of a live deadline.
        # No clock is frozen or source lifetime widened.
        await asyncio.sleep(1.05)
        for name in LAYERS:
            raw = await c.fetchrow(f'''SELECT
                (life_financial.{name}($1)-'observed_at')::text AS current,
                (snapshot_reference.{name}($1)-'observed_at')::text AS previous,
                life_financial.{name.replace('spot_state', 'spot_body')}($1)::text AS body''', cfg.account_id)
            row = {k: json.loads(v) for k, v in raw.items()}
            assert row['current'] == row['previous'], (state, name)
            assert row['body'] == {k: v for k, v in row['current'].items() if k != 'digest'}
        assert not cold.diagnostics[-1].commit_attempted
        # A caller without an account mapping is still rejected at the base
        # access guard, including through any retained historical wrapper.
        foreign = uuid4()
        for name in ('spot_state', 'spot_state_v7'):
            with pytest.raises(asyncpg.RaiseError, match='LIFE_ACCOUNT_SCOPE_INVALID'):
                await c.fetchval(f'SELECT life_financial.{name}($1)', foreign)
        native = await asyncpg.connect(cfg.dsn)
        try:
            with pytest.raises(asyncpg.RaiseError, match='LIFE_ACCOUNT_SCOPE_INVALID'):
                await native.fetchval('SELECT life_financial.spot_state($1)', foreign)
        finally:
            await native.close()
    finally:
        await cold.close()
