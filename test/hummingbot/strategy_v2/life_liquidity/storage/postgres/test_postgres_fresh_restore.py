"""Native fresh-account disaster rehearsal, without any File import or live API."""
import json
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_backup import tools
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_lifecycle import prepared, terminal
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import LIMITS
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_spot_observations import ack, filled
from time import perf_counter
from uuid import uuid4

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.archive_tools import PgLogicalBackup, restore_archive
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import capture_database
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine


@pytest.mark.asyncio
@pytest.mark.parametrize('state', ['paid_terminal', 'unknown_send'])
async def test_native_recovery_preserves_money_spending_and_unknown_holds_while_authority_stays_quarantined(database, tmp_path, request, state):
    f, cfg, owner = await prepared(database, arm=True)
    c = database[0]
    try:
        if state == 'paid_terminal':
            at = await c.fetchval('SELECT clock_timestamp()')
            trade = filled(owner, at)
            await f.acknowledge(ack(owner, at), cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
            await f.apply_fills(trade, cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
            command = await terminal(f, cfg, owner, fills=trade.fills)
            await f.confirm_terminal(command, cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
        before = await f.snapshot(cfg.binding)
        if state == 'paid_terminal':
            assert (before.balance.total_base_life, before.balance.total_quote_usdt) == (D('6'), D('8.98'))
            assert before.budget.campaign_execution_loss_usdt == before.budget.campaign_subsidy_committed_usdt == D('.12')
            assert before.balance.reserved_quote_usdt == 0
        else:
            assert before.balance.reserved_quote_usdt == D('2.2') and before.recovery_required
    finally:
        await f.close()
    source = await capture_database(c, limits=LIMITS)
    source_account = dict(await c.fetchrow('SELECT * FROM life_financial.accounts WHERE account_id=$1', cfg.account_id))
    started = perf_counter()
    bundle = await PgLogicalBackup(c, tools=tools(await c.fetchval('SELECT current_database()')), limits=LIMITS).create(tmp_path / 'backup')
    backup_ms = (perf_counter() - started) * 1000
    assert bundle.image == source
    root, name, restored = database[1].rsplit('/', 1)[0], 'fresh_restore_' + uuid4().hex, None
    admin = await asyncpg.connect(root + '/life_pg2_test')
    created = False
    try:
        await admin.execute(f'CREATE DATABASE "{name}" OWNER life_pg2_migrator')
        created = True
        await admin.execute(f'REVOKE CONNECT ON DATABASE "{name}" FROM PUBLIC')
        restored = await asyncpg.connect(root.replace('postgres@', 'life_pg2_migrator@') + '/' + name)
        started = perf_counter()
        await restore_archive(restored, tools=tools(name), bundle=bundle, limits=LIMITS)
        assert await capture_database(restored, limits=LIMITS) == source
        restore_verify_ms = (perf_counter() - started) * 1000
        await admin.execute(f'ALTER DATABASE "{name}" ALLOW_CONNECTIONS false')
        from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import (
            request as restore_request,
        )
        cmd = restore_request(source)
        started = perf_counter()
        receipt = await PgRestoreQuarantine(restored, limits=LIMITS).quarantine(cmd, manifest=source)
        quarantine_ms = (perf_counter() - started) * 1000
        assert not receipt.trading_permission
        after = await capture_database(restored, limits=LIMITS)
        changed = {'life_financial.accounts', 'life_storage.storage_metadata', 'life_storage.restore_history'}
        assert tuple(t for t in after.tables if t.name not in changed) == tuple(t for t in source.tables if t.name not in changed)
        account = await restored.fetchrow('SELECT * FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)
        assert account['mode'] == 'UNARMED' and account['halted']
        assert account['database_lineage'] == cmd.target_lineage and account['lineage'] == cfg.binding.lineage
        assert all(account[key] is None for key in ('owner_process', 'owner_generation', 'current_session', 'owner_since', 'owner_until'))
        assert account['risk_epoch'] == source_account['risk_epoch'] + 1
        assert not await restored.fetchval('SELECT life_financial.can_access($1,true)', cfg.account_id)
        assert await restored.fetchval("SELECT total FROM life_financial.balances WHERE account_id=$1 AND asset='USDT'", cfg.account_id) == before.balance.total_quote_usdt
        if state == 'paid_terminal':
            assert await restored.fetchval('SELECT life_financial.spot_capital_verified($1)', cfg.account_id)
            assert await restored.fetchval("SELECT incurred FROM life_financial.budget_buckets WHERE account_id=$1 AND family='LOSS' AND scope='CAMPAIGN'", cfg.account_id) == D('.12')
            assert await restored.fetchval('SELECT count(*) FROM life_financial.fills WHERE account_id=$1', cfg.account_id) == 1
        else:
            assert await restored.fetchval("SELECT state FROM life_financial.send_attempts WHERE account_id=$1", cfg.account_id) == 'SEND_UNKNOWN'
            assert await restored.fetchval("SELECT reserved FROM life_financial.balances WHERE account_id=$1 AND asset='USDT'", cfg.account_id) == D('2.2')
        assert dict(await c.fetchrow('SELECT * FROM life_financial.accounts WHERE account_id=$1', cfg.account_id)) == source_account
        request.node.user_properties.append(('life_pg6_native_rehearsal', json.dumps(dict(
            state=state, backup_ms=round(backup_ms, 3), restore_verify_ms=round(restore_verify_ms, 3),
            quarantine_ms=round(quarantine_ms, 3), archive_bytes=bundle.archive_bytes,
            tables=len(source.tables), rows=sum(t.rows for t in source.tables),
            profile='local PostgreSQL16 / 2 CPU / 1 GiB / synthetic small native account',
            retained_history_headroom_qualified=False, live_rpo_rto_qualified=False))))
    finally:
        if restored is not None:
            await restored.close()
        if created:
            await admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')
        await admin.close()
