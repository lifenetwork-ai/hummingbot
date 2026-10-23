"""Independent offline maintenance attestations; no worker private signing key.

AUDIT records a verified native snapshot plus complete bounded DB image.
BACKUP records a real exported-snapshot archive whose bytes were reread.
RESTORE records exact restore/quarantine parity, not post-cut reconciliation or
permission to trade. Receipt ages are observation ages, not file mtimes.
"""
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.handoff_artifact import read_bytes
from hummingbot.strategy_v2.life_liquidity.storage.integrity_receipts import IntegrityPolicy, IntegrityReceipt
from hummingbot.strategy_v2.life_liquidity.storage.postgres.archive_tools import (
    BackupBundle,
    PgArchiveTools,
    PgLogicalBackup,
    _owned_io,
    _publish,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import (
    BackupLimits,
    capture_transaction,
    maintenance_owner,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import DEFAULT_DIRECTORY, migrate
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine, RestoreRequest


class PgIntegrityPublisher:
    def __init__(self, connection, *, policy, signing_key, limits, directory=DEFAULT_DIRECTORY):
        if (type(policy) is not IntegrityPolicy or not isinstance(signing_key, Ed25519PrivateKey)
                or signing_key.public_key().public_bytes_raw() != policy.public_key or type(limits) is not BackupLimits):
            raise ValueError('INTEGRITY_PUBLISHER_CONFIG_INVALID')
        self.connection, self.policy, self._key, self.limits, self.directory = connection, policy, signing_key, limits, directory
        self._task = None

    async def close(self):
        if self._task is not None:
            await asyncio.shield(self._task)

    async def _run(self, operation, *args):
        if self._task is not None and not self._task.done():
            raise StorageError('INTEGRITY_PUBLISHER_BACKPRESSURE')
        self._task = asyncio.create_task(operation(*args))
        self._task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return await asyncio.shield(self._task)

    async def _scope(self, connection, *, lineage=None, restore_operation=None):
        p, c = self.policy, connection
        account = await c.fetchrow('SELECT * FROM life_financial.accounts WHERE account_id=$1', p.account_id)
        meta = await c.fetchrow('SELECT lineage,restore_operation_id FROM life_storage.storage_metadata')
        expected = p.database_lineage if lineage is None else lineage
        if (account is None or meta is None or meta['lineage'] != expected or account['database_lineage'] != expected
                or meta['restore_operation_id'] != restore_operation
                or any(str(account[k]) != str(v) for k, v in p.binding.json().items())):
            raise StorageError('INTEGRITY_PUBLISHER_SCOPE_INVALID')

    async def _preflight(self, connection):
        await maintenance_owner(connection)
        status = await migrate(connection, self.directory, action='status')
        if status['pending']:
            raise StorageError('INTEGRITY_SCHEMA_INCOMPATIBLE')

    def _receipt(self, kind, observed_at, artifact_digest):
        p = self.policy
        body = dict(protocol='LIFE_OFFLINE_INTEGRITY_V1', publisher_id=p.publisher_id,
                    account_id=str(p.account_id), database_lineage=str(p.database_lineage), binding=p.binding.json(),
                    kind=kind, observed_at=observed_at.isoformat(), result='PASSED', artifact_digest=artifact_digest)
        raw = encode(body)
        receipt = IntegrityReceipt(raw.decode(), self._key.sign(raw).hex())
        # A slow audit/backup cannot return an already stale success certificate.
        p.verify(receipt, now=datetime.now(timezone.utc))
        return receipt

    async def audit(self, *, reader):
        return await self._run(self._audit, reader)

    async def _audit(self, reader):
        import asyncpg
        c = self.connection
        await self._preflight(c)
        if (not isinstance(reader, asyncpg.Connection) or reader is c or reader.is_closed() or reader.is_in_transaction()
                or await reader.fetchval('SELECT life_financial.can_access($1,true)', self.policy.account_id)
                or not await reader.fetchval('SELECT life_financial.can_access($1,false)', self.policy.account_id)):
            raise StorageError('INTEGRITY_READONLY_AUDITOR_REQUIRED')
        async with asyncio.timeout(self.limits.timeout_ms / 1000), c.transaction(isolation='repeatable_read', readonly=True):
            await self._scope(c)
            # Native preparation-only namespaces can exist without a capital
            # basis. They are useful fixtures, not auditable money history.
            # Never certify their mutable wallet as an opening balance.
            if (not await c.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.spot_capital_policies WHERE account_id=$1)', self.policy.account_id)
                    or not await c.fetchval('SELECT life_financial.spot_capital_verified($1)', self.policy.account_id)):
                raise StorageError('INTEGRITY_CAPITAL_BASIS_UNVERIFIED')
            observed = await c.fetchval('SELECT transaction_timestamp()')
            snapshot = await c.fetchval('SELECT pg_export_snapshot()')
            if type(snapshot) is not str or re.fullmatch('[0-9A-Fa-f-]{1,64}', snapshot) is None:
                raise StorageError('INTEGRITY_SNAPSHOT_INVALID')
            # Run the original snapshot verifier; pending recovery/unknown
            # attempts may be valid retained history, but inconsistency cannot
            # receive a successful audit. The reporting principal imports the
            # same MVCC snapshot. Never grant an owner a runtime access binding
            # or substitute two sequential snapshots that can observe a race.
            async with reader.transaction(isolation='repeatable_read', readonly=True):
                await reader.execute("SET TRANSACTION SNAPSHOT '" + snapshot + "'")
                await reader.execute("SELECT set_config('statement_timeout',$1,true)", str(self.limits.timeout_ms))
                await self._scope(reader)
                valid = await reader.fetchval("SELECT (life_financial.spot_state($1)->>'unverified')::boolean IS FALSE", self.policy.account_id)
                if valid is not True:
                    raise StorageError('INTEGRITY_NATIVE_HISTORY_UNVERIFIED')
                image = await capture_transaction(c, limits=self.limits)
        return self._receipt('AUDIT', observed, image.content_digest)

    async def backup(self, *, tools, destination):
        if type(tools) is not PgArchiveTools:
            raise StorageError('INTEGRITY_BACKUP_TOOLS_INVALID')
        return await self._run(self._backup, tools, destination)

    async def _backup(self, tools, destination):
        c = self.connection
        await self._preflight(c)
        await self._scope(c)
        # Conservative start timestamp, never the later successful poll time.
        observed = await c.fetchval('SELECT clock_timestamp()')
        writer = PgLogicalBackup(c, tools=tools, limits=self.limits, directory=self.directory)
        try:
            bundle = await writer.create(destination)
        finally:
            await writer.close()
        await bundle.verify(limits=self.limits)
        await self._scope(c)
        if bundle.image.lineage != str(self.policy.database_lineage):
            raise StorageError('INTEGRITY_BACKUP_SCOPE_INVALID')
        return bundle, self._receipt('BACKUP', observed, bundle.content_digest)

    async def restored(self, *, restore, command, bundle, backup_receipt):
        if type(restore) is not PgRestoreQuarantine or type(command) is not RestoreRequest or type(bundle) is not BackupBundle:
            raise StorageError('INTEGRITY_RESTORE_CONFIG_INVALID')
        body = self.policy.verify(backup_receipt, now=datetime.now(timezone.utc))
        if (body['kind'] != 'BACKUP' or body['artifact_digest'] != bundle.content_digest
                or command.source_lineage != self.policy.database_lineage or command.backup_sha256 != bundle.archive_sha256
                or command.manifest_sha256 != bundle.image.content_digest or restore.limits != self.limits):
            raise StorageError('INTEGRITY_RESTORE_SOURCE_INVALID')
        return await self._run(self._restored, restore, command, bundle)

    async def _restored(self, restore, command, bundle):
        await bundle.verify(limits=self.limits)
        receipt = await restore.quarantine(command, manifest=bundle.image)
        await self._scope(restore.connection, lineage=command.target_lineage, restore_operation=command.operation_id)
        if receipt.trading_permission is not False:
            raise StorageError('INTEGRITY_RESTORE_PERMISSION_INVALID')
        observed = await restore.connection.fetchval('SELECT clock_timestamp()')
        proof = fingerprint(dict(backup=bundle.content_digest, request=command.json()))
        return self._receipt('RESTORE', observed, proof)


async def publish_receipt(path, receipt, *, policy):
    if type(policy) is not IntegrityPolicy:
        raise ValueError('INTEGRITY_POLICY_INVALID')
    policy.verify(receipt, now=datetime.now(timezone.utc))
    path = Path(path)
    if not path.is_absolute() or path.parent.resolve() != path.parent:
        raise ValueError('INTEGRITY_PUBLICATION_PATH_INVALID')
    # Exclusive immutable publication, not an in-place latest-file rewrite.
    await _owned_io(_publish, path, encode(dict(body_json=receipt.body_json, signature=receipt.signature)))


class FileIntegrityProvider:
    """Explicit local paths for offline rehearsal; a mount is not an IAM proof."""

    def __init__(self, paths):
        if (type(paths) is not tuple or not 1 <= len(paths) <= 3 or any(not isinstance(p, Path) or not p.is_absolute() for p in paths)
                or len(set(paths)) != len(paths)):
            raise ValueError('INTEGRITY_PROVIDER_PATHS_INVALID')
        self.paths = paths

    async def __call__(self):
        def read():
            result = []
            for path in self.paths:
                raw = read_bytes(path, 16384)
                value = json.loads(raw)
                if type(value) is not dict or set(value) != {'body_json', 'signature'} or encode(value) != raw:
                    raise ValueError('INTEGRITY_PUBLICATION_INVALID')
                result.append(IntegrityReceipt(**value))
            return tuple(result)
        return await _owned_io(read)
