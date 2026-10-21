"""Migration-owner provisioning of existing least-privilege login roles.

No role, account, money or grant bootstrap. Intended for the privileged migration/
import stage; runtime has no permission to change these mappings or grants.
"""
import re
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError

# One policy for provisioning and every uncached primary admission check.
ATOMIC_DISPATCH_ENTRYPOINT = 'life_financial.spot_dispatch_claimed_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb)'
NATIVE_CAPTURE_ENTRYPOINT = 'life_financial.spot_capture_quote_action(uuid,jsonb,jsonb,bigint,life_financial.digest,jsonb)'
MUTATING_ENTRYPOINTS = (
    'life_financial.legacy_applying_pending(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.guard_account(uuid,jsonb,jsonb,bigint)',
    'life_financial.allocate_balance(uuid,jsonb,jsonb,bigint,life_financial.identity,life_financial.digest,text,life_financial.positive_amount)',
    'life_financial.spot_capture_ack_ingress(uuid,jsonb)',
    'life_financial.spot_capture_financial_ingress(uuid,jsonb)',
    'life_financial.spot_capture_provider_page(uuid,jsonb)',
    'life_financial.spot_capture_provider_pages(uuid,jsonb)',
    'life_financial.shared_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.carry_check(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.hedge_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
    'life_financial.swap_boundary_arm(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.swap_capture_ack(uuid,jsonb)',
    'life_financial.swap_mark_ack(uuid,text)',
    'life_financial.joint_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.joint_batch_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.shared_release_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.terminal_repair_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.swap_terminal_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.portfolio_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.funding_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.swap_fill_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.swap_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
    'life_financial.spot_mark_financial_ingress(uuid,life_financial.identity,life_financial.identity)',
    'life_financial.spot_mark_ack_ingress(uuid,life_financial.identity)',
    'life_financial.spot_prepare(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.spot_arm(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,life_financial.identity)',
    'life_financial.guard_spot_observation(uuid,jsonb,jsonb,bigint)',
    'life_financial.spot_observe(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
    'life_financial.spot_capital_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
    'life_financial.spot_session_tick(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.spot_observe_markout(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.spot_settle_inventory(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb,boolean)',
    'life_financial.spot_order_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb,boolean)',
    'life_financial.spot_action_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
    'life_financial.spot_action_prepare(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.recorder_event_stage(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,life_financial.identity,jsonb)',
    'life_financial.recorder_event_apply(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)',
    'life_financial.recorder_position_stage(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.recorder_position_apply(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)',
    'life_financial.recorder_config_stage(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
    'life_financial.recorder_config_apply(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)',
    'life_financial.recorder_stage_executor(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,text,jsonb)',
    'life_financial.recorder_apply_executor(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.identity)',
    'life_financial.spot_arm_quote(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)',
)

NATIVE_EXIT_ENTRYPOINT = 'life_financial.execution_exit_settle(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb,jsonb)'

NATIVE_EXECUTION_ENTRYPOINT = 'life_financial.execution_market_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'

NATIVE_STRESS_ENTRYPOINT = 'life_financial.risk_stress_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'

NATIVE_RISK_ENTRYPOINT = 'life_financial.risk_command(uuid,jsonb,jsonb,bigint,life_financial.digest,life_financial.digest,jsonb)'


def role_name(value):
    if type(value) is not str or re.fullmatch(r'[a-z][a-z0-9_]{0,62}', value) is None:
        raise StorageError('PG_ROLE_NAME_INVALID')
    return value


async def provision_access(connection, *, principal, account_id, write):
    role_name(principal)
    if type(account_id) is not UUID or type(write) is not bool:
        raise StorageError('PG_ACCESS_INVALID')
    async with connection.transaction():
        owner = await connection.fetchval("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname='life_financial'")
        if owner != await connection.fetchval('SELECT current_user'):
            raise StorageError('PG_MIGRATION_OWNER_REQUIRED')
        role = await connection.fetchrow('SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication,rolcanlogin FROM pg_roles WHERE rolname=$1', principal)
        if (role is None or not role['rolcanlogin'] or any(role[k] for k in ('rolsuper', 'rolbypassrls', 'rolcreaterole', 'rolcreatedb', 'rolreplication'))
                or await connection.fetchval("SELECT pg_has_role($1,$2,'MEMBER')", principal, owner)
                or await connection.fetchval("SELECT has_database_privilege($1,current_database(),'CREATE')", principal)):
            raise StorageError('PG_ROLE_PRIVILEGES_UNSAFE')
        if not await connection.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.accounts WHERE account_id=$1)', account_id):
            raise StorageError('PG_ACCESS_ACCOUNT_MISSING')
        # Names were validated above; SQL values remain bound parameters.
        await connection.execute(f'GRANT USAGE ON SCHEMA life_financial,life_storage,life_migrations TO "{principal}"')
        tables = await connection.fetch("SELECT tablename FROM pg_tables WHERE schemaname='life_financial' AND tablename NOT IN ('access_bindings','joint_batch_staging','joint_repair_staging','hedge_staging','legacy_applying_policies','legacy_applying_imports')")
        for row in tables:
            name = role_name(row['tablename'])
            await connection.execute(f'GRANT SELECT ON life_financial."{name}" TO "{principal}"')
        await connection.execute(f'GRANT SELECT ON life_storage.storage_metadata,life_migrations.schema_migrations TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.can_access(uuid,boolean) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.spot_state(uuid) TO "{principal}"')
        if await connection.fetchval("SELECT to_regprocedure('life_financial.risk_frame(uuid)') IS NOT NULL"):
            await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.risk_frame(uuid) TO "{principal}"')
            await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.risk_enabled(uuid) TO "{principal}"')
        if await connection.fetchval("SELECT to_regprocedure('life_financial.execution_enabled(uuid)') IS NOT NULL"):
            await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.execution_market_frame(uuid),life_financial.execution_quote_budget_frame(uuid),life_financial.execution_enabled(uuid) TO "{principal}"')
        if await connection.fetchval("SELECT to_regprocedure('life_financial.risk_stress_frame(uuid)') IS NOT NULL"):
            await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.risk_stress_frame(uuid),life_financial.risk_stress_enabled(uuid) TO "{principal}"')
        if await connection.fetchval("SELECT to_regprocedure('life_financial.risk_markout_frame(uuid)') IS NOT NULL"):
            await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.risk_markout_frame(uuid) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.spot_read_ack_ingress(uuid,life_financial.identity) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.spot_capital_read(uuid,text) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.spot_action_read(uuid) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.spot_quote_arms_verified(uuid) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.recorder_executor_verified(uuid) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.spot_observation_context(uuid,integer,integer) TO "{principal}"')
        # Preserve explicit qualification/provisioning of predecessor schemas.
        # The reconciliation adapter itself requires migration 57; it has no
        # legacy/File fallback when this entrypoint is absent or ungranted.
        if await connection.fetchval("SELECT to_regprocedure('life_financial.collateral_reconciliation_context(uuid,integer,integer)') IS NOT NULL"):
            await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.collateral_reconciliation_context(uuid,integer,integer) TO "{principal}"')
        if await connection.fetchval("SELECT to_regprocedure('life_financial.check_joint_source_deadline(uuid,text)') IS NOT NULL"):
            await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.check_joint_source_deadline(uuid,text) TO "{principal}"')
        await connection.execute(f'''GRANT EXECUTE ON FUNCTION life_financial.spot_read_financial_ingress(uuid,life_financial.identity),
            life_financial.spot_pending_financial_ingress(uuid,integer,integer) TO "{principal}"''')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.spot_markout_frame(uuid,integer,integer) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.shared_capital_read_operation(uuid,text,integer,integer) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.shared_capital_frame(uuid,integer,integer) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.carry_read(uuid,integer,integer,text) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.swap_read(uuid,integer,integer,text) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.spot_read_provider_collection(uuid,life_financial.identity,integer,integer) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.swap_fill_read(uuid,integer,integer,text) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.funding_read(uuid,integer,integer,text) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.portfolio_read(uuid,integer,integer,text) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.swap_terminal_read(uuid,integer,integer,text) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.shared_release_read(uuid,integer,integer,text) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.joint_batch_read(uuid,integer,integer,text) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.terminal_repair_read(uuid,integer,integer,text) TO \"{principal}\"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.joint_repair_read(uuid,integer,integer,text) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.hedge_read(uuid,integer,integer,text) TO "{principal}"')
        await connection.execute(f'GRANT EXECUTE ON FUNCTION life_financial.swap_boundary_read(uuid,integer,integer,text),life_financial.swap_read_ack(uuid,integer,integer,text) TO "{principal}"')
        if write:
            await connection.execute('GRANT EXECUTE ON FUNCTION ' + ','.join(MUTATING_ENTRYPOINTS) + f' TO "{principal}"')
            if await connection.fetchval('SELECT to_regprocedure($1) IS NOT NULL', ATOMIC_DISPATCH_ENTRYPOINT):
                await connection.execute('GRANT EXECUTE ON FUNCTION ' + ATOMIC_DISPATCH_ENTRYPOINT + f' TO "{principal}"')
            if await connection.fetchval('SELECT to_regprocedure($1) IS NOT NULL', NATIVE_CAPTURE_ENTRYPOINT):
                await connection.execute('GRANT EXECUTE ON FUNCTION ' + NATIVE_CAPTURE_ENTRYPOINT + f' TO "{principal}"')
            if await connection.fetchval('SELECT to_regprocedure($1) IS NOT NULL', NATIVE_RISK_ENTRYPOINT):
                await connection.execute('GRANT EXECUTE ON FUNCTION ' + NATIVE_RISK_ENTRYPOINT + f' TO "{principal}"')
            if await connection.fetchval('SELECT to_regprocedure($1) IS NOT NULL', NATIVE_STRESS_ENTRYPOINT):
                await connection.execute('GRANT EXECUTE ON FUNCTION ' + NATIVE_STRESS_ENTRYPOINT + f' TO "{principal}"')
            if await connection.fetchval('SELECT to_regprocedure($1) IS NOT NULL', NATIVE_EXECUTION_ENTRYPOINT):
                await connection.execute('GRANT EXECUTE ON FUNCTION ' + NATIVE_EXECUTION_ENTRYPOINT + ',' + NATIVE_EXIT_ENTRYPOINT + f' TO "{principal}"')
        await connection.execute('''INSERT INTO life_financial.access_bindings(principal,account_id,can_write) VALUES($1,$2,$3)
            ON CONFLICT(principal,account_id) DO UPDATE SET can_write=EXCLUDED.can_write''', principal, account_id, write)
