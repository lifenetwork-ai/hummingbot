"""Owner-only resumable stopped File transport; no native financial activation.

The trusted artifact digest is supplied from the original frozen publication.
Chunks/SEALED state do not prove current source retirement or credential exclusion.
Native translation, exact parity and separately authorized cutover must follow.
"""
import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    StateBinding,
    StorageError,
    digest,
    encode,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import maintenance_owner
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import DEFAULT_DIRECTORY, migrate
from hummingbot.strategy_v2.life_liquidity.storage.stopped_artifact import unpack_stopped_source


@dataclass(frozen=True)
class FileStageRequest:
    operation_id: str
    account_id: UUID
    database_lineage: UUID
    target_binding: StateBinding
    source_sha256: str
    max_bytes: int
    max_rows: int
    chunk_bytes: int

    def __post_init__(self):
        identifier(self.operation_id)
        digest(self.source_sha256)
        if (len(self.operation_id) > 96 or any(type(v) is not UUID or not v.int for v in (self.account_id, self.database_lineage))
                or type(self.target_binding) is not StateBinding or self.target_binding.environment != 'OFFLINE_SIMULATION'
                or any(type(v) is not int or v < 1 for v in (self.max_bytes, self.max_rows, self.chunk_bytes))
                or self.max_bytes > 64 * 2**20 or self.max_rows > 1000000 or self.chunk_bytes > 2**20):
            raise StorageError('PG_FILE_STAGE_REQUEST_INVALID')

    def json(self):
        return {**self.__dict__, 'account_id': str(self.account_id), 'database_lineage': str(self.database_lineage),
                'target_binding': self.target_binding.json()}

    @property
    def scope(self):
        return fingerprint({k: v for k, v in self.target_binding.json().items()
                            if k in ('environment', 'exchange', 'account_uid', 'risk_pool')})


@dataclass(frozen=True)
class FileStageReceipt:
    operation_id: str
    state: str
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


class PgFileStaging:
    def __init__(self, connection, *, timeout_ms, directory=DEFAULT_DIRECTORY):
        if type(timeout_ms) is not int or not 0 < timeout_ms <= 600000:
            raise StorageError('PG_FILE_STAGE_TIMEOUT_INVALID')
        self.connection, self.timeout_ms, self.directory = connection, timeout_ms, directory
        self._task = None

    async def _owned(self, command, work, *args):
        if type(command) is not FileStageRequest:
            raise StorageError('PG_FILE_STAGE_REQUEST_INVALID')
        if self._task is not None and not self._task.done():
            raise StorageError('PG_FILE_STAGE_BACKPRESSURE')
        self._task = asyncio.create_task(self._physical(command, work, args))
        self._task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return await asyncio.shield(self._task)

    async def close(self):
        if self._task is not None:
            await asyncio.shield(self._task)

    async def _physical(self, command, work, args):
        async with asyncio.timeout(self.timeout_ms / 1000):
            await maintenance_owner(self.connection)
            if (await migrate(self.connection, self.directory, action='status'))['pending']:
                raise StorageError('PG_FILE_STAGE_SCHEMA_INCOMPATIBLE')
            return await work(command, *args)

    async def _decoded(self, command, raw):
        return await self._in_thread(self._decode, command, raw)

    @staticmethod
    async def _in_thread(function, *args, **kwargs):
        # A deadline/caller cancellation cannot release the physical slot while
        # the bounded CPU decoder is still running in its original thread.
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(task)
            finally:
                raise

    async def stage(self, command, raw):
        return await self._owned(command, self._stage, raw)

    async def read(self, command):
        return await self._owned(command, self._read)

    async def discard(self, command):
        return await self._owned(command, self._discard)

    async def capital_projection(self, command):
        """Reconstruct original capital from a sealed cut; never activate it."""
        return await self._owned(command, self._capital_projection)

    async def _capital_projection(self, command):
        from hummingbot.strategy_v2.life_liquidity.storage.stopped_capital import project_stopped_capital
        source = await self._read(command)
        return await self._in_thread(project_stopped_capital, source, max_rows=command.max_rows)

    async def verify_retirement(self, command, raw, *, expected_sha256):
        """Audit the pinned post-consumption File root; never activate a target."""
        return await self._owned(command, self._verify_retirement, raw, expected_sha256)

    async def _verify_retirement(self, command, raw, expected_sha256):
        from hummingbot.strategy_v2.life_liquidity.storage.retirement_checkpoint import verify_retirement_checkpoint
        source = await self._read(command)
        return await self._in_thread(verify_retirement_checkpoint, source, raw, expected_sha256=expected_sha256,
                                     target_binding=command.target_binding, target_account_id=command.account_id,
                                     database_lineage=command.database_lineage, target_manifest_digest=fingerprint(command.json()),
                                     max_bytes=command.max_bytes)

    @staticmethod
    def _decode(command, raw):
        source = unpack_stopped_source(raw, expected_sha256=command.source_sha256,
                                       max_bytes=command.max_bytes, max_rows=command.max_rows)
        old, target = source.base.core.cut.checkpoint.binding, command.target_binding
        if (any(getattr(old, k) != getattr(target, k) for k in (
                'environment', 'exchange', 'account_uid', 'risk_pool', 'schema_version', 'policy_hash'))
                or target.lineage == old.lineage or target.config_hash != source.base.core.pending.target_hash
                or source.base.core.unresolved_intents):
            raise StorageError('PG_FILE_STAGE_SOURCE_SCOPE_INVALID')
        return source

    async def _scope(self, command):
        c = self.connection
        meta = await c.fetchrow('SELECT lineage,restore_operation_id FROM life_storage.storage_metadata FOR UPDATE')
        if meta['lineage'] != command.database_lineage or meta['restore_operation_id'] is not None:
            raise StorageError('PG_FILE_STAGE_DATABASE_SCOPE_INVALID')
        if await c.fetchval('''SELECT EXISTS(SELECT 1 FROM life_financial.accounts WHERE account_id=$1
            OR (environment=$2 AND exchange=$3 AND account_uid=$4 AND risk_pool=$5))''',
                            command.account_id, command.target_binding.environment, command.target_binding.exchange,
                            command.target_binding.account_uid, command.target_binding.risk_pool):
            raise StorageError('PG_FILE_STAGE_TARGET_NOT_EMPTY')

    async def _row(self, command):
        row = await self.connection.fetchrow('SELECT * FROM life_storage.file_imports WHERE operation_id=$1 FOR UPDATE', command.operation_id)
        if row is None or json.loads(row['request']) != command.json():
            raise StorageError('PG_FILE_STAGE_OPERATION_CONFLICT')
        if (row['account_id'] != command.account_id or row['database_lineage'] != command.database_lineage
                or row['source_sha256'] != command.source_sha256 or row['scope_sha256'] != command.scope
                or row['chunk_bytes'] != command.chunk_bytes or row['artifact_bytes'] > 4 * command.max_bytes + 65536
                or (row['artifact_bytes'] + command.chunk_bytes - 1) // command.chunk_bytes > command.max_rows):
            raise StorageError('PG_FILE_STAGE_METADATA_INVALID')
        if row['state'] == 'DISCARDED':
            raise StorageError('PG_FILE_STAGE_DISCARDED')
        return row

    async def _transaction(self, command, work, *, seal=False):
        c, committing = self.connection, False
        tx = c.transaction(isolation='serializable')
        await tx.start()
        try:
            await c.execute("SELECT set_config('statement_timeout',$1,true),set_config('lock_timeout',$1,true)", str(self.timeout_ms))
            await self._scope(command)
            result = await work()
            committing = True
            await (self._commit(tx) if seal else tx.commit())
            return result
        except BaseException:
            if c.is_in_transaction():
                await tx.rollback()
            if committing:
                raise CommitUnknown('PG_FILE_STAGE_COMMIT_UNKNOWN') from None
            raise

    async def _commit(self, transaction):
        await transaction.commit()

    async def _begin(self, command, length):
        if (length > 4 * command.max_bytes + 65536
                or (length + command.chunk_bytes - 1) // command.chunk_bytes > command.max_rows):
            raise StorageError('PG_FILE_STAGE_CAPACITY_EXCEEDED')

        async def work():
            c = self.connection
            previous = await c.fetchrow('SELECT * FROM life_storage.file_imports WHERE operation_id=$1 OR account_id=$2 OR scope_sha256=$3 OR source_sha256=$4',
                                        command.operation_id, command.account_id, command.scope, command.source_sha256)
            if previous is not None:
                row = await self._row(command)
                if row['artifact_bytes'] != length:
                    raise StorageError('PG_FILE_STAGE_OPERATION_CONFLICT')
                return row['state']
            await c.execute('''INSERT INTO life_storage.file_imports
                (operation_id,account_id,database_lineage,source_sha256,scope_sha256,request,artifact_bytes,chunk_bytes,state)
                VALUES($1,$2,$3,$4,$5,$6::jsonb,$7,$8,'IMPORTING')''', command.operation_id, command.account_id,
                            command.database_lineage, command.source_sha256, command.scope, encode(command.json()).decode(), length, command.chunk_bytes)
            return 'IMPORTING'
        return await self._transaction(command, work)

    async def _write_chunk(self, command, sequence, raw):
        async def work():
            row = await self._row(command)
            start = sequence * command.chunk_bytes
            if row['state'] != 'IMPORTING' or len(raw) != min(command.chunk_bytes, row['artifact_bytes'] - start) or start >= row['artifact_bytes']:
                raise StorageError('PG_FILE_STAGE_CHUNK_INVALID')
            old = await self.connection.fetchval('SELECT data FROM life_storage.file_import_chunks WHERE operation_id=$1 AND sequence=$2', command.operation_id, sequence)
            if old is not None:
                if old != raw:
                    raise StorageError('PG_FILE_STAGE_CHUNK_CONFLICT')
                return
            await self.connection.execute('INSERT INTO life_storage.file_import_chunks VALUES($1,$2,$3,$4)', command.operation_id,
                                          sequence, raw, hashlib.sha256(raw).hexdigest())
        await self._transaction(command, work)

    async def _bytes(self, command, row):
        count = (row['artifact_bytes'] + command.chunk_bytes - 1) // command.chunk_bytes
        chunks = await self.connection.fetch('SELECT sequence,data,sha256 FROM life_storage.file_import_chunks WHERE operation_id=$1 ORDER BY sequence LIMIT $2',
                                             command.operation_id, count + 1)
        if (len(chunks) != count or any(r['sequence'] != i or hashlib.sha256(r['data']).hexdigest() != r['sha256']
                                        or len(r['data']) != min(command.chunk_bytes, row['artifact_bytes'] - i * command.chunk_bytes)
                                        for i, r in enumerate(chunks))):
            raise StorageError('PG_FILE_STAGE_CHUNKS_INCOMPLETE')
        raw = b''.join(r['data'] for r in chunks)
        if len(raw) != row['artifact_bytes'] or hashlib.sha256(raw).hexdigest() != command.source_sha256:
            raise StorageError('PG_FILE_STAGE_DIGEST_INVALID')
        return raw

    async def _stage(self, command, raw):
        await self._decoded(command, raw)
        state = await self._begin(command, len(raw))
        if state != 'SEALED':
            for sequence, offset in enumerate(range(0, len(raw), command.chunk_bytes)):
                await self._write_chunk(command, sequence, raw[offset:offset + command.chunk_bytes])

        async def seal():
            row = await self._row(command)
            await self._bytes(command, row)
            replayed = row['state'] == 'SEALED'
            if not replayed:
                await self.connection.execute("UPDATE life_storage.file_imports SET state='SEALED' WHERE operation_id=$1", command.operation_id)
            return FileStageReceipt(command.operation_id, 'SEALED', replayed)
        return await self._transaction(command, seal, seal=True)

    async def _read(self, command):
        async def read():
            row = await self._row(command)
            if row['state'] != 'SEALED':
                raise StorageError('PG_FILE_STAGE_INCOMPLETE')
            return await self._bytes(command, row)
        raw = await self._transaction(command, read)
        return await self._decoded(command, raw)

    async def _discard(self, command):
        async def discard():
            row = await self.connection.fetchrow('SELECT request FROM life_storage.file_imports WHERE operation_id=$1 FOR UPDATE', command.operation_id)
            if row is None or json.loads(row['request']) != command.json():
                raise StorageError('PG_FILE_STAGE_OPERATION_CONFLICT')
            await self.connection.execute("UPDATE life_storage.file_imports SET state='DISCARDED' WHERE operation_id=$1", command.operation_id)
            return 'DISCARDED'
        return await self._transaction(command, discard)
