"""Isolated, owner-only logical restore quarantine. Never rearm a restored cut.

The restored database must already match a trusted complete backup manifest.
Original account ownership is retained in immutable audit, not executable state.
Release of quarantine/PITR reconciliation is deliberately a separate operation.
"""
import asyncio
import json
from dataclasses import dataclass, field
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import CommitUnknown, StorageError, digest, identifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import (
    BackupLimits,
    DatabaseImage,
    capture_transaction,
    maintenance_owner,
    verify_database_image,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import DEFAULT_DIRECTORY, migrate
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.schema_image import verify_schema


@dataclass(frozen=True)
class RestoreRequest:
    operation_id: str
    source_lineage: UUID
    target_lineage: UUID
    backup_sha256: str
    manifest_sha256: str

    def __post_init__(self):
        identifier(self.operation_id)
        digest(self.backup_sha256)
        digest(self.manifest_sha256)
        if (len(self.operation_id) > 96 or any(type(v) is not UUID or not v.int for v in (self.source_lineage, self.target_lineage))
                or self.source_lineage == self.target_lineage):
            raise StorageError('PG_RESTORE_REQUEST_INVALID')

    def json(self):
        return dict(operation_id=self.operation_id, source_lineage=str(self.source_lineage),
                    target_lineage=str(self.target_lineage), backup_sha256=self.backup_sha256, manifest_sha256=self.manifest_sha256)


@dataclass(frozen=True)
class RestoreReceipt:
    operation_id: str
    target_lineage: UUID
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


class PgRestoreQuarantine:
    def __init__(self, connection, *, limits, directory=DEFAULT_DIRECTORY):
        if type(limits) is not BackupLimits:
            raise StorageError('PG_RESTORE_LIMITS_INVALID')
        self.connection, self.limits, self.directory = connection, limits, directory
        self._task = None

    async def close(self):
        if self._task is not None:
            await asyncio.shield(self._task)

    async def quarantine(self, command, *, manifest):
        if (type(command) is not RestoreRequest or type(manifest) is not DatabaseImage
                or command.manifest_sha256 != manifest.content_digest or str(command.source_lineage) != manifest.lineage):
            raise StorageError('PG_RESTORE_MANIFEST_INVALID')
        if self._task is not None and not self._task.done():
            raise StorageError('PG_RESTORE_BACKPRESSURE')
        # Cancellation cannot abandon a still-running COMMIT or free its physical
        # connection. Callers drain close() before closing that connection.
        self._task = asyncio.create_task(self._physical(command, manifest))
        self._task.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        return await asyncio.shield(self._task)

    async def _commit(self, transaction):
        await transaction.commit()

    async def _physical(self, command, manifest):
        try:
            async with asyncio.timeout(self.limits.timeout_ms / 1000):
                return await self._transaction(command, manifest)
        except TimeoutError:
            raise StorageError('PG_RESTORE_TIMEOUT') from None

    async def _transaction(self, command, manifest):
        c, committing = self.connection, False
        await maintenance_owner(c)
        status = await migrate(c, self.directory, action='status')
        if status['pending']:
            raise StorageError('PG_RESTORE_SCHEMA_INCOMPATIBLE')
        tx = c.transaction(isolation='serializable')
        await tx.start()
        try:
            await c.execute("SELECT set_config('lock_timeout',$1,true),set_config('statement_timeout',$1,true)", str(self.limits.timeout_ms))
            # Datallowconn is a cluster-level admission fence. Cooperative locks
            # or a lease cannot prevent an old process from reconnecting here.
            if (await c.fetchval('SELECT datallowconn FROM pg_database WHERE datname=current_database()')
                    or await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datid=(SELECT oid FROM pg_database WHERE datname=current_database()) AND pid<>pg_backend_pid())')
                    or await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_prepared_xacts WHERE database=current_database())')):
                raise StorageError('PG_RESTORE_TARGET_NOT_ISOLATED')
            await c.execute('LOCK TABLE life_storage.storage_metadata,life_financial.accounts IN ACCESS EXCLUSIVE MODE')
            old = await c.fetchrow('SELECT * FROM life_storage.restore_history WHERE operation_id=$1', command.operation_id)
            meta = await c.fetchrow('SELECT lineage,restore_operation_id FROM life_storage.storage_metadata')
            if old is not None:
                if (any(str(old[k]) != v for k, v in command.json().items())
                        or meta['restore_operation_id'] != command.operation_id or str(meta['lineage']) != str(command.target_lineage)
                        or await c.fetchval("SELECT EXISTS(SELECT 1 FROM life_financial.accounts WHERE mode<>'UNARMED' OR NOT halted OR database_lineage<>$1)", command.target_lineage)):
                    raise StorageError('PG_RESTORE_OPERATION_CONFLICT')
                actual = await capture_transaction(c, limits=self.limits)
                changed = {'life_financial.accounts', 'life_storage.storage_metadata', 'life_storage.restore_history'}
                if tuple(t for t in actual.tables if t.name not in changed) != tuple(t for t in manifest.tables if t.name not in changed):
                    raise StorageError('PG_RESTORE_DATA_PARITY_FAILED')
                verify_schema(manifest.schema, actual.schema)
                expected = json.loads(old['before_accounts'])
                for row in expected:
                    row.update(database_lineage=str(command.target_lineage), mode='UNARMED', halted=True,
                               owner_process=None, owner_generation=None, current_session=None, owner_since=None, owner_until=None,
                               risk_epoch=row['risk_epoch'] + 1)
                current = json.loads(await c.fetchval("SELECT COALESCE(jsonb_agg(to_jsonb(a) ORDER BY account_id),'[]'::jsonb) FROM life_financial.accounts a"))
                if current != expected:
                    raise StorageError('PG_RESTORE_ACCOUNT_PARITY_FAILED')
                await tx.rollback()
                return RestoreReceipt(command.operation_id, command.target_lineage, True)
            if str(meta['lineage']) != str(command.source_lineage):
                raise StorageError('PG_RESTORE_LINEAGE_CONFLICT')
            if await c.fetchval('SELECT EXISTS(SELECT 1 FROM life_storage.restore_history WHERE source_lineage=$1 OR target_lineage=$1)', command.target_lineage):
                raise StorageError('PG_RESTORE_LINEAGE_REUSE_DENIED')
            actual = await capture_transaction(c, limits=self.limits)
            verify_database_image(manifest, actual)
            await c.execute('''INSERT INTO life_storage.restore_history(operation_id,source_lineage,target_lineage,
                backup_sha256,manifest_sha256,before_accounts) SELECT $1,$2,$3,$4,$5,
                COALESCE(jsonb_agg(to_jsonb(a) ORDER BY account_id),'[]'::jsonb) FROM life_financial.accounts a''',
                            command.operation_id, command.source_lineage, command.target_lineage, command.backup_sha256, command.manifest_sha256)
            await c.execute('SET CONSTRAINTS life_financial.accounts_database_lineage_fkey DEFERRED')
            # Trusted schema-owner maintenance, with connections physically
            # excluded. Disable only the immutable-identity trigger inside this
            # transaction. Failure/kill rolls back DDL and restores it too.
            await c.execute('ALTER TABLE life_financial.accounts DISABLE TRIGGER account_identity')
            await c.execute('UPDATE life_storage.storage_metadata SET lineage=$1,restore_operation_id=$2', command.target_lineage, command.operation_id)
            await c.execute('''UPDATE life_financial.accounts SET database_lineage=$1,mode='UNARMED',halted=true,
                owner_process=NULL,owner_generation=NULL,current_session=NULL,owner_since=NULL,owner_until=NULL,risk_epoch=risk_epoch+1''', command.target_lineage)
            await c.execute('SET CONSTRAINTS ALL IMMEDIATE')
            await c.execute('ALTER TABLE life_financial.accounts ENABLE TRIGGER account_identity')
            committing = True
            await self._commit(tx)
            return RestoreReceipt(command.operation_id, command.target_lineage, False)
        except BaseException as error:
            if c.is_in_transaction():
                await tx.rollback()
            if committing:
                raise CommitUnknown('PG_RESTORE_COMMIT_UNKNOWN') from None
            if isinstance(error, asyncio.CancelledError):
                raise
            if isinstance(error, StorageError):
                raise
            raise sql_error(error) from None
