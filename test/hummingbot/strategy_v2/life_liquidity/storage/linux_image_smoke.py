"""Run inside the built artifact, with only test code mounted from the host."""
import importlib
import json
import platform
import sys
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.storage.image_manifest import verify_execution_manifest


def main():
    import asyncpg
    import psycopg
    import sqlalchemy

    from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import load_migrations

    assert platform.system() == 'Linux'
    assert sys.executable.startswith('/opt/conda/envs/hummingbot/')
    assert asyncpg.__version__ == '0.31.0' and psycopg.__version__ == '3.2.9'
    assert sqlalchemy.__version__ == '2.1.1'
    for name in ('hummingbot.core.data_type.order_book', 'hummingbot.connector.connector_base',
                 'hummingbot.strategy.strategy_base'):
        module = importlib.import_module(name)
        assert module.__file__.startswith('/home/hummingbot/') and module.__file__.endswith('.so'), name
    for name in ('hummingbot.strategy.strategy_v2_base', 'hummingbot.connector.markets_recorder',
                 'controllers.generic.life_liquidity', 'hummingbot.connector.exchange.okx.okx_exchange',
                 'hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative',
                 'hummingbot.core.event.event_forwarder',
                 'hummingbot.strategy_v2.life_liquidity.storage.headless'):
        assert importlib.import_module(name).__file__.startswith('/home/hummingbot/')
    expected = json.loads(Path(sys.argv[1]).read_text())
    root = Path('/home/hummingbot')
    verify_execution_manifest(root, expected)
    assert not (root / '.env').exists()
    assert {migration.name for migration in load_migrations(root / 'migrations/life_liquidity')} == {
        Path(name).name for name in expected if name.startswith('migrations/life_liquidity/') and name.endswith('.sql')}
    print(json.dumps({'system': platform.system(), 'machine': platform.machine(), 'python': platform.python_version(),
                      'asyncpg': asyncpg.__version__, 'psycopg': psycopg.__version__, 'sqlalchemy': sqlalchemy.__version__,
                      'verified_files': len(expected), 'trading_permission': False}))


if __name__ == '__main__':
    main()
