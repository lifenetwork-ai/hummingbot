"""Explicit pool settings from .env/environment; identity comes from the caller.

No driver/connection/financial bootstrap. Existing file workers do not invoke
this opt-in loader. Missing operational bounds have no implicit live defaults.
"""
import os
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.config import ROOT, _dotenv
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig

INTEGERS = {
    'SERVER_MAJOR': 'server_major', 'POOL_MIN_SIZE': 'min_size', 'POOL_MAX_SIZE': 'max_size', 'MAX_PENDING': 'max_pending',
    'SAFETY_POOL_SIZE': 'safety_size', 'SAFETY_MAX_PENDING': 'safety_max_pending', 'ACQUIRE_TIMEOUT_MS': 'acquire_timeout_ms',
    'STATEMENT_TIMEOUT_MS': 'statement_timeout_ms', 'TRANSACTION_TIMEOUT_MS': 'transaction_timeout_ms',
    'IDLE_TRANSACTION_TIMEOUT_MS': 'idle_transaction_timeout_ms', 'SHUTDOWN_TIMEOUT_MS': 'shutdown_timeout_ms',
    'RETRY_COUNT': 'retry_count', 'RETRY_BASE_MS': 'retry_base_ms',
}
KEYS = {'LIFE_PG_' + name for name in INTEGERS} | {'LIFE_PG_DSN', 'LIFE_PG_PRINCIPAL', 'LIFE_PG_ACCESS', 'LIFE_PG_LOCAL_INSECURE', 'LIFE_PG_SSL_CA_FILE'}


def load_runtime_config(*, binding, account_id, database_lineage, env_file=None, environ=None, no_env_file=False):
    environ = os.environ if environ is None else environ
    explicit = env_file is not None or 'LIFE_ENV_FILE' in environ
    selected = str(env_file) if env_file is not None else environ.get('LIFE_ENV_FILE', str(ROOT / '.env'))
    path = Path(selected).resolve() if selected and not no_env_file else None
    values = {}
    if path is not None:
        if path.exists():
            values = _dotenv(path, keys=KEYS)
        elif explicit:
            raise StorageError('PG_ENV_FILE_MISSING')
    values.update({key: environ[key] for key in KEYS if key in environ})
    try:
        numbers = {field: int(values['LIFE_PG_' + key]) for key, field in INTEGERS.items()}
        access = values['LIFE_PG_ACCESS']
        insecure = values.get('LIFE_PG_LOCAL_INSECURE', 'false').lower()
        if access not in ('runtime', 'reporting') or insecure not in ('true', 'false'):
            raise ValueError()
        ca = values.get('LIFE_PG_SSL_CA_FILE', '')
        ca_file = None if not ca else Path(ca)
        if ca_file is not None and not ca_file.is_absolute():
            ca_file = ((path.parent if path is not None else ROOT) / ca_file).resolve()
        return PgConfig(dsn=values['LIFE_PG_DSN'], principal=values['LIFE_PG_PRINCIPAL'], binding=binding,
                        account_id=account_id, database_lineage=database_lineage, write=access == 'runtime',
                        local_insecure=insecure == 'true', ssl_ca_file=ca_file, **numbers)
    except (ValueError, TypeError, KeyError):
        raise StorageError('PG_ENV_CONFIG_INVALID') from None
