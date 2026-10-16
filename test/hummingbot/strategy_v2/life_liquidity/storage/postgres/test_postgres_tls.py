"""Certificate/hostname verification against an actual local TLS primary."""
import asyncio
import os
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.helpers import backend
from test.hummingbot.strategy_v2.life_liquidity.storage.run_migrations import docker

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime


@pytest.mark.asyncio
async def test_actual_tls_connection_requires_trusted_ca_and_verified_peer(database, tmp_path):
    import asyncpg
    name = os.environ['LIFE_PG_TEST_CONTAINER']
    assert name.startswith('life-pg2-')
    await asyncio.to_thread(docker, 'exec', '--user', 'postgres', name, 'openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                            '-keyout', '/tmp/life-tls.key', '-out', '/tmp/life-tls.crt', '-days', '1', '-subj', '/CN=localhost',
                            '-addext', 'subjectAltName=IP:127.0.0.1,DNS:localhost')
    await asyncio.to_thread(docker, 'exec', '--user', 'postgres', name, 'chmod', '600', '/tmp/life-tls.key')
    await asyncio.to_thread(docker, 'cp', name + ':/tmp/life-tls.crt', str(tmp_path / 'ca.crt'))
    admin = await asyncpg.connect(database[1])
    try:
        await admin.execute("ALTER SYSTEM SET ssl_cert_file='/tmp/life-tls.crt'")
        await admin.execute("ALTER SYSTEM SET ssl_key_file='/tmp/life-tls.key'")
        await admin.execute("ALTER SYSTEM SET ssl=on")
        await admin.fetchval('SELECT pg_reload_conf()')
    finally:
        await admin.close()
    await asyncio.sleep(.15)
    store, cfg, owner = await backend(database)
    await store.close()
    secured = replace(cfg, local_insecure=False, ssl_ca_file=tmp_path / 'ca.crt')
    with pytest.raises(StorageError, match='STARTUP_UNQUALIFIED'):
        await PgRuntime.open(replace(secured, ssl_ca_file=None))
    valid = await PgRuntime.open(secured)
    try:
        assert len(await valid.balances()) == 2
    finally:
        await valid.close()
