"""Real physical base backup + archived WAL target recovery in isolated Docker.

Only --suite pg6_pitr provides this test's dedicated disposable volume. Nothing
loads project .env, exchange credentials or a user database. No rearm is implied.
"""
import asyncio
import json
import os
from dataclasses import replace
from datetime import timedelta
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_financial_commands import order, spot
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_restore import (
    LIMITS,
    request as restore_request,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.run_migrations import IMAGE
from time import perf_counter
from uuid import uuid4

import asyncpg
import pytest

from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import capture_database, verify_database_image
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine


async def docker(*args, timeout=60):
    process = await asyncio.create_subprocess_exec('docker', *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, error = await asyncio.wait_for(process.communicate(), timeout)
        if process.returncode:
            raise AssertionError('Disposable Docker command failed: ' + error.decode(errors='replace')[-3000:])
        return (output + (error if args[0] == 'logs' else b'')).decode().strip()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


@pytest.mark.asyncio
async def test_physical_wal_restore_reaches_named_cut_and_excludes_later_activity(database, request):
    source = os.environ['LIFE_PG_TEST_CONTAINER']
    volume = os.environ['LIFE_PG_TEST_VOLUME']
    target = 'life-pg6-recovered-' + uuid4().hex[:12]
    c = database[0]
    name = await c.fetchval('SELECT current_database()')
    f, cfg, owner = await spot(database, owner_duration=timedelta(minutes=5), session_duration=timedelta(minutes=5))
    restored, created = None, False
    # Explicit local synthetic functional ceiling; never an economic/source limit.
    started = perf_counter()
    try:
        assert await c.fetchval("SELECT current_setting('archive_mode')") == 'on'
        await docker('exec', '-u', 'postgres', source, 'pg_basebackup', '-U', 'postgres', '-D', '/pg6/base',
                     '--wal-method=stream', '--checkpoint=fast')
        await docker('exec', '-u', 'postgres', source, 'pg_verifybackup', '/pg6/base')
        # A real damaged physical file must fail verification, then the exact
        # original is restored before the valid recovery. No ignore/checksum waiver.
        await docker('exec', '-u', 'postgres', source, 'cp', '-p', '/pg6/base/global/pg_control', '/pg6/original_control')
        await docker('exec', '-u', 'postgres', source, 'sh', '-c',
                     'printf CORRUPT | dd of=/pg6/base/global/pg_control bs=1 conv=notrunc')
        with pytest.raises(AssertionError, match='checksum mismatch'):
            await docker('exec', '-u', 'postgres', source, 'pg_verifybackup', '/pg6/base')
        await docker('exec', '-u', 'postgres', source, 'mv', '/pg6/original_control', '/pg6/base/global/pg_control')
        await docker('exec', '-u', 'postgres', source, 'pg_verifybackup', '/pg6/base')
        backup_digest = (await docker('exec', source, 'sha256sum', '/pg6/base/backup_manifest')).split()[0]
        base_ms = (perf_counter() - started) * 1000
        await f.prepare(order(owner), cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
        image = await capture_database(c, limits=LIMITS)
        # Restore point is a real WAL record strictly after A's COMMIT.
        marker = 'life_pg6_' + uuid4().hex
        admin = await asyncpg.connect(database[1])
        try:
            await admin.fetchval('SELECT pg_create_restore_point($1)', marker)
            await f.prepare(order(owner, operation_id='after_cut', intent_id='after_cut', client_order_id='aftercut', quote_level=1),
                            cut=(await f.snapshot(cfg.binding)).cut, owner=owner)
            assert await c.fetchval('SELECT count(*) FROM life_financial.send_attempts') == 2
            segment = await admin.fetchval('SELECT pg_walfile_name(pg_current_wal_lsn())')
            await admin.fetchval('SELECT pg_switch_wal()')
            for _ in range(100):
                if await admin.fetchval('SELECT last_archived_wal >= $1 FROM pg_stat_archiver', segment):
                    break
                await asyncio.sleep(.1)
            else:
                pytest.fail('WAL segment containing target was not archived')
        finally:
            await admin.close()
        await f.close()
        f = None
        # HBA is an external startup admission fence, before any restored worker
        # can connect. Old roles/keys still exist in the physical cluster.
        hba = 'local all postgres trust\nhost all life_pg2_migrator 0.0.0.0/0 trust\nhost all all 0.0.0.0/0 reject\nhost all all ::0/0 reject\n'
        config = "\nrestore_command = 'cp /pg6/wal/%f %p'\nrecovery_target_name = '" + marker + "'\nrecovery_target_action = 'promote'\nrecovery_target_timeline = 'current'\n"

        async def start_target():
            await docker('exec', '-u', 'postgres', source, 'cp', '-a', '/pg6/base', '/pg6/recovered')
            # Fixed shell program; dynamic text is positional data, not code.
            await docker('exec', '-u', 'postgres', source, 'sh', '-c',
                         'printf "%s" "$1" > /pg6/isolated_hba.conf; printf "%s" "$2" >> /pg6/recovered/postgresql.auto.conf; touch /pg6/recovered/recovery.signal',
                         'pg6-fixture', hba, config)
            await docker('run', '-d', '--name', target, '--label', 'life.pg6_test=disposable', '--cpus', '2', '--memory', '1g',
                         '-e', 'PGDATA=/pg6/recovered', '-v', volume + ':/pg6', '-p', '127.0.0.1::5432', IMAGE,
                         'postgres', '-c', 'hba_file=/pg6/isolated_hba.conf', '-c', 'archive_mode=off')

        # Without WAL containing the target, PostgreSQL must exit. Never promote
        # whatever earlier history happened to survive in the base backup.
        await docker('exec', '-u', 'postgres', source, 'mv', '/pg6/wal', '/pg6/withheld_wal')
        await start_target()
        created = True
        for _ in range(100):
            if await docker('inspect', '-f', '{{.State.Status}}', target) == 'exited':
                break
            await asyncio.sleep(.1)
        else:
            pytest.fail('Missing-WAL recovery did not fail closed')
        assert 'recovery ended before configured recovery target was reached' in await docker('logs', target)
        await docker('rm', '-f', target)
        created = False
        await docker('exec', '-u', 'postgres', source, 'rm', '-rf', '/pg6/recovered')
        await docker('exec', '-u', 'postgres', source, 'mv', '/pg6/withheld_wal', '/pg6/wal')
        recovery_started = perf_counter()
        await start_target()
        created = True
        port = (await docker('port', target, '5432/tcp')).split(':')[-1]
        dsn = 'postgresql://life_pg2_migrator@127.0.0.1:' + port + '/' + name
        for _ in range(200):
            try:
                restored = await asyncpg.connect(dsn, timeout=1)
                break
            except (asyncpg.PostgresError, OSError):
                await asyncio.sleep(.1)
        else:
            pytest.fail('Physical recovery did not become ready: ' + await docker('logs', target))
        for _ in range(200):
            if not await restored.fetchval('SELECT pg_is_in_recovery()'):
                break
            await asyncio.sleep(.1)
        else:
            pytest.fail('Restored standby never promoted at the required target')
        for role in ('life_pg2_runtime', 'life_pg2_reporter'):
            with pytest.raises(asyncpg.InvalidAuthorizationSpecificationError):
                await asyncpg.connect(dsn.replace('life_pg2_migrator@', role + '@'), timeout=1)
        verify_database_image(image, await capture_database(restored, limits=LIMITS))
        assert await restored.fetchval('SELECT count(*) FROM life_financial.send_attempts') == 1
        assert await restored.fetchval("SELECT count(*) FROM life_financial.operations WHERE operation_id='after_cut'") == 0
        await docker('exec', '-u', 'postgres', target, 'psql', '-U', 'postgres', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1',
                     '-c', 'ALTER DATABASE "' + name + '" ALLOW_CONNECTIONS false')
        receipt = await PgRestoreQuarantine(restored, limits=LIMITS).quarantine(replace(restore_request(image), backup_sha256=backup_digest), manifest=image)
        assert not receipt.trading_permission
        assert await restored.fetchval("SELECT bool_and(mode='UNARMED' AND halted) FROM life_financial.accounts")
        assert await restored.fetchval('SELECT sum(reserved) FROM life_financial.balances') > 0
        restore_ms = (perf_counter() - recovery_started) * 1000
        total_ms = (perf_counter() - started) * 1000
        # Explicit small-fixture rehearsal ceiling. Not deployed RPO/RTO.
        assert total_ms < 120000
        sizes = await docker('exec', target, 'du', '-sk', '/pg6/base', '/pg6/wal', '/pg6/recovered')
        storage_kib = sum(int(line.split()[0]) for line in sizes.splitlines())
        assert storage_kib < 1024 * 1024
        request.node.user_properties.append(('life_pg6_pitr_rehearsal', json.dumps(dict(
            base_ms=round(base_ms, 3), restore_quarantine_ms=round(restore_ms, 3), total_ms=round(total_ms, 3),
            storage_kib=storage_kib, target_rpo_lost_commits=0, later_commits_excluded=1,
            profile='two isolated PostgreSQL16 containers, each 2 CPU / 1 GiB, synthetic fixture',
            deployed_rpo_rto_qualified=False, postcut_reconciliation_required=True, missing_wal_denied=True, damaged_backup_denied=True))))
    finally:
        if f is not None:
            await f.close()
        if restored is not None:
            await restored.close()
        if created:
            await docker('rm', '-f', target)
