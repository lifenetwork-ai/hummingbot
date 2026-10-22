"""Offline custom-format PostgreSQL backup and empty-target restore transport.

Use trusted pg_dump/pg_restore binaries matching the pinned server major. Supply
connection secrets in the child environment, never command arguments. Callers
own database creation, CONNECT exclusion, the dedicated connection and its close.
An archive is data evidence; loading it never publishes a database or arms it.
"""
import asyncio
import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, digest, encode, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.handoff_artifact import read_bytes, sync_dir
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import (
    BackupLimits,
    DatabaseImage,
    capture_database,
    capture_transaction,
    maintenance_owner,
    verify_database_image,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import DEFAULT_DIRECTORY, migrate


async def _owned_io(function, *args):
    """Do not close/reuse a descriptor while an admitted FS thread still owns it.

    Cancellation/deadline closes admission, not the physical thread. Waiting may
    exceed that deadline on a stalled filesystem; the original slot stays held.
    """
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        if not task.cancelled():
            task.exception()  # Retrieve a late physical failure, never print it.
        raise


def _file_hash(path, maximum):
    sha, total = hashlib.sha256(), 0
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise StorageError('PG_ARCHIVE_FILE_INVALID')
        while chunk := handle.read(65536):
            if total == 0 and not chunk.startswith(b'PGDMP'):
                raise StorageError('PG_ARCHIVE_FORMAT_INVALID')
            total += len(chunk)
            if total > maximum:
                raise StorageError('PG_ARCHIVE_CAPACITY_EXCEEDED')
            sha.update(chunk)
    if total == 0:
        raise StorageError('PG_ARCHIVE_FORMAT_INVALID')
    return sha.hexdigest(), total


def _publish(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    sync_dir(path.parent)


@dataclass(frozen=True)
class BackupBundle:
    directory: Path
    image: DatabaseImage
    archive_sha256: str
    archive_bytes: int

    @property
    def archive_path(self):
        return self.directory / 'database.dump'

    def json(self):
        return dict(format='LIFE_PG_ARCHIVE_V1', image=self.image.json(),
                    archive_sha256=self.archive_sha256, archive_bytes=self.archive_bytes)

    @property
    def content_digest(self):
        return fingerprint(self.json())

    async def verify(self, *, limits):
        current = await self.open(self.directory, expected_digest=self.content_digest, limits=limits)
        if current != self:
            raise StorageError('PG_ARCHIVE_MANIFEST_CHANGED')
        return self.image

    @classmethod
    async def open(cls, directory, *, expected_digest, limits):
        digest(expected_digest)
        if type(limits) is not BackupLimits:
            raise StorageError('PG_ARCHIVE_LIMITS_INVALID')

        def read():
            try:
                folder = Path(directory)
                if not folder.is_absolute() or folder.resolve() != folder or folder.is_symlink():
                    raise StorageError('PG_ARCHIVE_DIRECTORY_UNSAFE')
                raw = read_bytes(folder / 'manifest.json', limits.max_total_bytes)
                value = json.loads(raw)
                if (encode(value) != raw or set(value) != {'format', 'image', 'archive_sha256', 'archive_bytes'}
                        or value['format'] != 'LIFE_PG_ARCHIVE_V1'
                        or type(value['image']) is not dict):
                    raise StorageError('PG_ARCHIVE_MANIFEST_INVALID')
                image = DatabaseImage.from_json(value['image'])
                tables = image.tables
                if len(tables) > limits.max_tables or sum(t.rows for t in tables) > limits.max_rows or sum(t.bytes for t in tables) > limits.max_total_bytes:
                    raise StorageError('PG_ARCHIVE_CAPACITY_EXCEEDED')
                if image.schema is not None and (sum(c.objects for c in image.schema.classes) > limits.max_schema_objects or sum(c.bytes for c in image.schema.classes) > limits.max_schema_bytes):
                    raise StorageError('PG_ARCHIVE_SCHEMA_CAPACITY_EXCEEDED')
                bundle = cls(folder, image, value['archive_sha256'], value['archive_bytes'])
                digest(bundle.archive_sha256)
                if type(bundle.archive_bytes) is not int or not 0 < bundle.archive_bytes <= limits.max_total_bytes:
                    raise StorageError('PG_ARCHIVE_CAPACITY_EXCEEDED')
                if bundle.content_digest != expected_digest:
                    raise StorageError('PG_ARCHIVE_MANIFEST_DIGEST_INVALID')
                sha, size = _file_hash(bundle.archive_path, limits.max_total_bytes)
                if (sha, size) != (bundle.archive_sha256, bundle.archive_bytes):
                    raise StorageError('PG_ARCHIVE_DIGEST_INVALID')
                return bundle
            except StorageError:
                raise
            except (ValueError, TypeError, KeyError, OSError):
                raise StorageError('PG_ARCHIVE_UNAVAILABLE_OR_INVALID') from None
        return await _owned_io(read)


class PgArchiveTools:
    """Trusted tool argv prefixes; no shell, raw stderr or command logging.

    Environment is explicit when supplied (e.g. PGPASSFILE/TLS). Password/DSN
    arguments are forbidden. Test Docker prefixes contain no credentials. This
    adapter does not prove an external cloud CLI/binary or credential provider.
    """

    def __init__(self, *, dump_command, restore_command, timeout_ms, environment=None):
        if (any(type(cmd) is not tuple or not cmd or any(type(arg) is not str or not arg or '\x00' in arg for arg in cmd)
                for cmd in (dump_command, restore_command)) or type(timeout_ms) is not int or not 0 < timeout_ms <= 600000
                or any('postgres://' in arg or 'postgresql://' in arg or 'password=' in arg.lower()
                       for cmd in (dump_command, restore_command) for arg in cmd)):
            raise StorageError('PG_ARCHIVE_TOOL_CONFIG_INVALID')
        self._dump, self._restore, self._environment = dump_command, restore_command, None if environment is None else dict(environment)
        self.timeout_ms = timeout_ms

    async def _run(self, command, *, source=None, destination=None, maximum=None):
        process = None
        try:
            async with asyncio.timeout(self.timeout_ms / 1000):
                process = await asyncio.create_subprocess_exec(*command, stdin=source if source is not None else asyncio.subprocess.DEVNULL,
                                                               stdout=asyncio.subprocess.PIPE if destination is not None else asyncio.subprocess.DEVNULL,
                                                               stderr=asyncio.subprocess.DEVNULL, env=self._environment)
                if destination is not None:
                    total = 0
                    while chunk := await process.stdout.read(65536):
                        total += len(chunk)
                        if total > maximum:
                            raise StorageError('PG_ARCHIVE_CAPACITY_EXCEEDED')
                        await _owned_io(destination.write, chunk)
                if await process.wait() != 0:
                    raise StorageError('PG_ARCHIVE_TOOL_FAILED')
        except BaseException as error:
            if process is not None and process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
            if isinstance(error, (StorageError, asyncio.CancelledError)):
                raise
            raise StorageError('PG_ARCHIVE_TOOL_TIMEOUT_OR_UNAVAILABLE') from None

    async def dump(self, snapshot_id, destination, maximum):
        # Snapshot identity originates from pg_export_snapshot, never a shell.
        if type(snapshot_id) is not str or len(snapshot_id) > 128 or type(maximum) is not int or maximum < 1:
            raise StorageError('PG_BACKUP_SNAPSHOT_INVALID')
        await self._run((*self._dump, '--format=custom', '--snapshot=' + snapshot_id), destination=destination, maximum=maximum)

    async def restore(self, source):
        await self._run((*self._restore, '--exit-on-error', '--single-transaction', '--no-owner'), source=source)


class PgLogicalBackup:
    def __init__(self, connection, *, tools, limits, directory=DEFAULT_DIRECTORY):
        if type(tools) is not PgArchiveTools or type(limits) is not BackupLimits:
            raise StorageError('PG_BACKUP_CONFIG_INVALID')
        self.connection, self.tools, self.limits, self.directory = connection, tools, limits, directory
        self._task, self.bundle = None, None

    async def create(self, destination):
        if self._task is not None and not self._task.done():
            raise StorageError('PG_BACKUP_BACKPRESSURE')
        self._task = asyncio.create_task(self._physical(Path(destination)))
        self._task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        return await asyncio.shield(self._task)

    async def close(self):
        if self._task is not None:
            await asyncio.shield(self._task)

    async def _physical(self, folder):
        c = self.connection
        await maintenance_owner(c)
        status = await migrate(c, self.directory, action='status')
        if status['pending']:
            raise StorageError('PG_BACKUP_SCHEMA_INCOMPATIBLE')
        if not folder.is_absolute() or folder.resolve() != folder:
            raise StorageError('PG_ARCHIVE_DIRECTORY_UNSAFE')
        try:
            folder.mkdir(mode=0o700, exist_ok=False)
        except FileExistsError:
            raise StorageError('PG_ARCHIVE_DIRECTORY_EXISTS') from None
        except OSError:
            raise StorageError('PG_ARCHIVE_DIRECTORY_UNAVAILABLE') from None
        await _owned_io(sync_dir, folder.parent)
        # A partial directory is retained on failure, never overwritten/adopted.
        async with c.transaction(isolation='repeatable_read', readonly=True):
            async with asyncio.timeout((self.limits.timeout_ms + self.tools.timeout_ms) / 1000):
                image = await capture_transaction(c, limits=self.limits)
                snapshot_id = await c.fetchval('SELECT pg_export_snapshot()')
                fd = os.open(folder / 'database.dump', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, 'wb') as handle:
                    await self.tools.dump(snapshot_id, handle, self.limits.max_total_bytes)
                    handle.flush()
                    await _owned_io(os.fsync, handle.fileno())
                sha, size = await _owned_io(_file_hash, folder / 'database.dump', self.limits.max_total_bytes)
                bundle = BackupBundle(folder, image, sha, size)
                # Publish the manifest last, while the exported snapshot is alive.
                await _owned_io(_publish, folder / 'manifest.json', encode(bundle.json()))
        self.bundle = bundle
        return bundle


async def restore_archive(connection, *, tools, bundle, limits):
    """Load only an empty, owner-only target. Never expose it to a worker.

    Caller must retain the exact input directory immutable throughout this
    privileged offline operation. No automatic retries after ambiguous transport.
    Following this step, disable target connections from another admin database,
    compare the complete image and invoke PgRestoreQuarantine before publication.
    """
    c = connection
    if type(tools) is not PgArchiveTools or type(bundle) is not BackupBundle or c.is_in_transaction():
        raise StorageError('PG_ARCHIVE_RESTORE_CONFIG_INVALID')
    await bundle.verify(limits=limits)
    row = await c.fetchrow("""SELECT current_user::text AS principal,session_user::text AS login,
        d.datdba::regrole::text AS owner,pg_is_in_recovery() AS replica,
        EXISTS(SELECT 1 FROM aclexplode(COALESCE(d.datacl,acldefault('d',d.datdba))) x
            WHERE x.privilege_type='CONNECT' AND x.grantee<>d.datdba) AS exposed,
        EXISTS(SELECT 1 FROM pg_roles r WHERE r.rolcanlogin AND NOT r.rolsuper AND r.oid<>d.datdba
            AND pg_has_role(r.oid,d.datdba,'MEMBER')) AS inherited_owner,
        current_setting('server_version_num')::int / 10000 AS major,
        EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=ANY($1::text[])) AS occupied
        FROM pg_database d WHERE datname=current_database()""", ['life_storage', 'life_migrations', 'life_financial', 'life_recorder'])
    if (row['principal'] != row['owner'] or row['login'] != row['owner'] or row['exposed'] or row['occupied'] or row['replica']
            or row['inherited_owner'] or row['major'] != bundle.image.server_major):
        raise StorageError('PG_ARCHIVE_RESTORE_EMPTY_ISOLATED_OWNER_REQUIRED')
    # Open with O_NOFOLLOW and hash the exact descriptor passed to pg_restore,
    # catching file replacement between the preceding manifest check and open.
    fd = os.open(bundle.archive_path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    with os.fdopen(fd, 'rb') as source:
        sha = hashlib.sha256()
        total = 0
        while chunk := await _owned_io(source.read, 65536):
            total += len(chunk)
            if total > limits.max_total_bytes:
                raise StorageError('PG_ARCHIVE_CAPACITY_EXCEEDED')
            sha.update(chunk)
        if (sha.hexdigest(), total) != (bundle.archive_sha256, bundle.archive_bytes):
            raise StorageError('PG_ARCHIVE_DIGEST_INVALID')
        source.seek(0)
        # Cancellation does not prove whether the remote tool committed. The
        # target stays inaccessible to runtime, and cannot be blindly retried.
        await tools.restore(source)
    actual = await capture_database(c, limits=limits)
    verify_database_image(bundle.image, actual)
    return actual
