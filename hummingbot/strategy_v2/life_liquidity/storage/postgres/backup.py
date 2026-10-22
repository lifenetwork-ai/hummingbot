"""Bounded complete logical data manifests on one primary MVCC snapshot.

This is an offline owner-side audit, not a hot-path cache or trading permission.
The caller retains the dedicated connection; no repository .env is consumed.
"""
import asyncio
import hashlib
import re
from dataclasses import asdict, dataclass
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, digest, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import DEFAULT_DIRECTORY, migrate
from hummingbot.strategy_v2.life_liquidity.storage.postgres.schema_image import (
    SchemaImage,
    capture_schema,
    verify_schema,
)

SCHEMAS = ('life_migrations', 'life_storage', 'life_financial', 'life_recorder')


@dataclass(frozen=True)
class BackupLimits:
    max_tables: int
    max_rows: int
    max_row_bytes: int
    max_total_bytes: int
    timeout_ms: int
    max_schema_objects: int
    max_schema_bytes: int

    def __post_init__(self):
        if (any(type(v) is not int or v < 1 for v in self.__dict__.values())
                or self.max_row_bytes > self.max_total_bytes or self.timeout_ms > 600000):
            raise StorageError('PG_BACKUP_LIMITS_INVALID')


@dataclass(frozen=True)
class TableImage:
    name: str
    rows: int
    bytes: int
    sha256: str

    def __post_init__(self):
        digest(self.sha256)
        if (type(self.name) is not str or re.fullmatch(r'life_(?:migrations|storage|financial|recorder)\.[A-Za-z_][A-Za-z_0-9]{0,62}', self.name) is None
                or any(type(v) is not int or v < 0 for v in (self.rows, self.bytes))):
            raise StorageError('PG_BACKUP_TABLE_IMAGE_INVALID')


@dataclass(frozen=True)
class DatabaseImage:
    lineage: str
    server_major: int
    tables: tuple[TableImage, ...]
    schema: SchemaImage | None = None

    def __post_init__(self):
        try:
            if (type(self.lineage) is not str or not UUID(self.lineage).int or str(UUID(self.lineage)) != self.lineage
                    or type(self.server_major) is not int or self.server_major < 16 or type(self.tables) is not tuple
                    or any(type(t) is not TableImage for t in self.tables)
                    or tuple(t.name for t in self.tables) != tuple(sorted(set(t.name for t in self.tables)))
                    or self.schema is not None and type(self.schema) is not SchemaImage):
                raise ValueError()
        except (ValueError, AttributeError):
            raise StorageError('PG_BACKUP_DATABASE_IMAGE_INVALID') from None

    def json(self):
        value = dict(format='LIFE_PG_LOGICAL_V1' if self.schema is None else 'LIFE_PG_LOGICAL_V2', lineage=self.lineage, server_major=self.server_major,
                     tables=[asdict(t) for t in self.tables])
        if self.schema is not None:
            value['schema'] = self.schema.json()
        return value

    @property
    def content_digest(self):
        return fingerprint(self.json())

    @classmethod
    def from_json(cls, value):
        try:
            if (type(value) is not dict or set(value) != {'format', 'lineage', 'server_major', 'tables'} | ({'schema'} if value.get('format') == 'LIFE_PG_LOGICAL_V2' else set())
                    or value['format'] not in ('LIFE_PG_LOGICAL_V1', 'LIFE_PG_LOGICAL_V2') or type(value['tables']) is not list):
                raise ValueError()
            return cls(value['lineage'], value['server_major'], tuple(TableImage(**row) for row in value['tables']),
                       None if value['format'] == 'LIFE_PG_LOGICAL_V1' else SchemaImage.from_json(value['schema']))
        except (TypeError, ValueError, KeyError):
            raise StorageError('PG_BACKUP_DATABASE_IMAGE_INVALID') from None


async def maintenance_owner(connection):
    import asyncpg
    if not isinstance(connection, asyncpg.Connection) or connection.is_closed() or connection.is_in_transaction():
        raise StorageError('PG_MAINTENANCE_CONNECTION_INVALID')
    row = await connection.fetchrow("""SELECT current_user::text AS principal,session_user::text AS session_principal,
        (SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname='life_financial') AS owner,
        pg_is_in_recovery() AS replica,current_setting('transaction_read_only') AS readonly,
        current_setting('synchronous_commit') AS sync,current_setting('fsync') AS fsync,
        current_setting('full_page_writes') AS writes""")
    if row['principal'] != row['owner'] or row['session_principal'] != row['owner']:
        raise StorageError('PG_MAINTENANCE_OWNER_REQUIRED')
    if row['replica'] or row['readonly'] != 'off' or any(row[k] != 'on' for k in ('sync', 'fsync', 'writes')):
        raise StorageError('PG_MAINTENANCE_PRIMARY_DURABILITY_REQUIRED')


def verify_database_image(expected, actual):
    if (type(expected) is not DatabaseImage or type(actual) is not DatabaseImage
            or expected.lineage != actual.lineage or expected.server_major != actual.server_major or expected.tables != actual.tables):
        raise StorageError('PG_RESTORE_DATA_PARITY_FAILED')
    if expected.schema is None or actual.schema is None:
        raise StorageError('PG_RESTORE_SCHEMA_EVIDENCE_REQUIRED')
    verify_schema(expected.schema, actual.schema)


async def capture_transaction(connection, *, limits):
    """Caller owns repeatable-read or serializable transaction and role check.

    Sorting full retained history is intentional offline work. Limits bound rows
    and returned bytes; SQL deadline bounds server work. No partial image returns.
    """
    c = connection
    if type(limits) is not BackupLimits or not c.is_in_transaction():
        raise StorageError('PG_BACKUP_TRANSACTION_REQUIRED')
    await c.execute("SELECT set_config('statement_timeout',$1,true)", str(limits.timeout_ms))
    await c.execute("""SELECT set_config('TimeZone','UTC',true),set_config('DateStyle','ISO,YMD',true),
        set_config('extra_float_digits','3',true),set_config('search_path','pg_catalog',true)""")
    tables = await c.fetch('''SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=ANY($1::text[]) AND c.relkind IN ('r','p') ORDER BY n.nspname,c.relname LIMIT $2''',
                           list(SCHEMAS), limits.max_tables + 1)
    if len(tables) > limits.max_tables:
        raise StorageError('PG_BACKUP_CAPACITY_EXCEEDED')
    images, total, count = [], 0, 0
    for table in tables:
        # Catalog names are quoted identifiers, never executable SQL or paths.
        name = '.'.join('"' + v.replace('"', '""') + '"' for v in table)
        query = f'''SELECT CASE WHEN octet_length(body)<=$1 THEN body END AS body FROM
            (SELECT to_jsonb(t)::text COLLATE "C" AS body FROM {name} t ORDER BY 1 LIMIT $2) s'''
        sha, size, rows = hashlib.sha256(), 0, 0
        async for row in c.cursor(query, limits.max_row_bytes, limits.max_rows + 1, prefetch=32):
            count += 1
            if row['body'] is None or count > limits.max_rows:
                raise StorageError('PG_BACKUP_CAPACITY_EXCEEDED')
            raw = row['body'].encode('utf-8')
            total += len(raw)
            if total > limits.max_total_bytes:
                raise StorageError('PG_BACKUP_CAPACITY_EXCEEDED')
            sha.update(len(raw).to_bytes(8, 'big'))
            sha.update(raw)
            size, rows = size + len(raw), rows + 1
        images.append(TableImage('.'.join(table), rows, size, sha.hexdigest()))
    lineage = str(await c.fetchval('SELECT lineage FROM life_storage.storage_metadata'))
    major = int(await c.fetchval("SELECT current_setting('server_version_num')::int / 10000"))
    schema = await capture_schema(c, limits=limits)
    return DatabaseImage(lineage, major, tuple(images), schema)


async def capture_database(connection, *, limits, directory=DEFAULT_DIRECTORY):
    """Complete image only; bytes/sets do not assert financial-source validity."""
    if type(limits) is not BackupLimits:
        raise StorageError('PG_BACKUP_LIMITS_INVALID')
    await maintenance_owner(connection)
    status = await migrate(connection, directory, action='status')
    if status['pending']:
        raise StorageError('PG_BACKUP_SCHEMA_INCOMPATIBLE')
    async with asyncio.timeout(limits.timeout_ms / 1000), connection.transaction(isolation='repeatable_read', readonly=True):
        return await capture_transaction(connection, limits=limits)
