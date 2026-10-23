"""Pinned pre-84 test client, never a production admission fallback.

These original upgrade oracles retain their explicit v82 destination, exact
snapshot representation and original receipts. Current-release qualification
runs separately; this client does not grant compatibility to an old DB.
"""
from hummingbot.strategy_v2.life_liquidity.storage.postgres import access, runtime


async def pin_pre84_client(monkeypatch, connection):
    batch = 'life_financial.spot_capture_provider_pages(uuid,jsonb)'
    missing = await connection.fetch('SELECT signature FROM unnest($1::text[]) signature WHERE to_regprocedure(signature) IS NULL',
                                     list(access.MUTATING_ENTRYPOINTS))
    assert [r['signature'] for r in missing] == [batch], 'Unknown historical role-profile difference'
    original = runtime.load_migrations
    monkeypatch.setattr(runtime, 'load_migrations', lambda *a, **k: tuple(m for m in original(*a, **k) if m.version <= 82))
    previous = tuple(v for v in access.MUTATING_ENTRYPOINTS if v != batch)
    monkeypatch.setattr(access, 'MUTATING_ENTRYPOINTS', previous)
    monkeypatch.setattr(runtime, 'MUTATING_ENTRYPOINTS', previous)
    # Explicit synthetic server profile for these historical correctness oracles.
    # LLVM compilation is outside the oracle's subject; SQL deadlines remain
    # unchanged. Scope defaults to this disposable database only, never roles.
    name = await connection.fetchval('SELECT current_database()')
    assert name.startswith('life_pg2_') and name.replace('_', '').isalnum()
    await connection.execute(f'ALTER DATABASE "{name}" SET jit=off')
    await connection.execute(f'ALTER DATABASE "{name}" SET plan_cache_mode=force_generic_plan')


async def provision_upgraded_client(connection, account_id):
    # Newly installed read/command functions stay denied until explicit owner
    # enrollment. Installing SQL alone cannot extend a runtime principal.
    await access.provision_access(connection, principal='life_pg2_runtime', account_id=account_id, write=True)
    await access.provision_access(connection, principal='life_pg2_reporter', account_id=account_id, write=False)
