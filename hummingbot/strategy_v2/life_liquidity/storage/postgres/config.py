"""Migration-only .env settings; no shell execution or environment mutation."""
import math
import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

from hummingbot.strategy_v2.life_liquidity.storage.postgres.errors import MigrationError

ROOT = Path(__file__).resolve().parents[5]
DEFAULT_DIRECTORY = ROOT / 'migrations' / 'life_liquidity'
KEYS = {
    'LIFE_AUTO_MIGRATE', 'LIFE_MIGRATION_DSN', 'LIFE_MIGRATION_DIRECTORY',
    'LIFE_MIGRATION_LOCAL_INSECURE', 'LIFE_MIGRATION_SSL_CA_FILE',
    'LIFE_MIGRATION_LOCK_TIMEOUT_SECONDS', 'LIFE_MIGRATION_STATEMENT_TIMEOUT_MS',
    'LIFE_MIGRATION_DDL_LOCK_TIMEOUT_MS',
}


@dataclass(frozen=True)
class MigrationConfig:
    auto_migrate: bool = False
    dsn: str = field(default='', repr=False)
    directory: Path = DEFAULT_DIRECTORY
    local_insecure: bool = False
    ssl_ca_file: Optional[Path] = None
    lock_timeout: float = 10.0
    statement_timeout: int = 30000
    ddl_lock_timeout: int = 5000

    def validate(self):
        if not math.isfinite(self.lock_timeout) or not 0 < self.lock_timeout <= 600:
            raise MigrationError('Invalid migration advisory lock timeout')
        if not 0 < self.ddl_lock_timeout <= self.statement_timeout <= 600000:
            raise MigrationError('Invalid migration SQL timeouts')
        return self


def _dotenv(path, *, keys=KEYS):
    """Read single-line KEY=value, optional export/quotes/comments, literally.

    Only migration keys are consumed. No $ interpolation or shell substitutions.
    Secrets with whitespace or # must be quoted; URL credentials must be encoded.
    """
    try:
        if path.stat().st_size > 65536:
            raise MigrationError('Migration env file exceeds 64 KiB')
        text = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        raise MigrationError('Cannot read migration env file') from None
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[7:].lstrip()
        key, separator, raw = line.partition('=')
        key = key.strip()
        if key not in keys:
            continue  # This loader does not configure other application settings.
        if not separator or key in values:
            raise MigrationError(f'Invalid or duplicate migration key on env line {number}')
        try:
            pieces = shlex.split(raw, comments=True, posix=True)
        except ValueError:
            raise MigrationError(f'Invalid migration env quoting on line {number}') from None
        if len(pieces) > 1:
            raise MigrationError(f'Quote migration env values with spaces on line {number}')
        values[key] = pieces[0] if pieces else ''
    return values


def _boolean(values, key):
    value = values.get(key, 'false').strip().lower()
    if value not in {'true', 'false'}:
        raise MigrationError(f'Invalid boolean setting {key}')
    return value == 'true'


def load_config(*, env_file=None, environ: Optional[Mapping[str, str]] = None, no_env_file=False):
    """OS environment overrides .env; an explicitly requested missing file fails.

    Default .env is repository-relative, independent of startup's working dir.
    LIFE_ENV_FILE='' or no_env_file disables file loading for managed deployments.
    Relative paths inside config are relative to the env file, or repository root.
    """
    environ = os.environ if environ is None else environ
    explicit = env_file is not None or 'LIFE_ENV_FILE' in environ
    selected = str(env_file) if env_file is not None else environ.get('LIFE_ENV_FILE', str(ROOT / '.env'))
    path = Path(selected).resolve() if selected and not no_env_file else None
    values, base = {}, ROOT
    if path is not None:
        base = path.parent
        if path.exists():
            values = _dotenv(path)
        elif explicit:
            raise MigrationError('Explicit migration env file is missing')
    values.update({key: environ[key] for key in KEYS if key in environ})

    def configured_path(key, default):
        raw = values.get(key, '')
        if not raw:
            return default
        result = Path(raw)
        return result if result.is_absolute() else (base / result).resolve()

    try:
        lock_timeout = float(values.get('LIFE_MIGRATION_LOCK_TIMEOUT_SECONDS', '10'))
        statement_timeout = int(values.get('LIFE_MIGRATION_STATEMENT_TIMEOUT_MS', '30000'))
        ddl_lock_timeout = int(values.get('LIFE_MIGRATION_DDL_LOCK_TIMEOUT_MS', '5000'))
    except (TypeError, ValueError, OverflowError):
        raise MigrationError('Invalid numeric migration timeout setting') from None
    return MigrationConfig(
        auto_migrate=_boolean(values, 'LIFE_AUTO_MIGRATE'), dsn=values.get('LIFE_MIGRATION_DSN', ''),
        directory=configured_path('LIFE_MIGRATION_DIRECTORY', DEFAULT_DIRECTORY),
        local_insecure=_boolean(values, 'LIFE_MIGRATION_LOCAL_INSECURE'),
        ssl_ca_file=configured_path('LIFE_MIGRATION_SSL_CA_FILE', None), lock_timeout=lock_timeout,
        statement_timeout=statement_timeout, ddl_lock_timeout=ddl_lock_timeout,
    ).validate()
