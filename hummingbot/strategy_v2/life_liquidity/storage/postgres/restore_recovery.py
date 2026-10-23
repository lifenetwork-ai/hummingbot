"""Verified read-only ancestry for a future controlled restore recovery.

This context is not an operator approval, replacement process registration or
release. It leaves the global restore barrier and every account unchanged.
"""
import asyncio
import json
from dataclasses import dataclass, field
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerMode,
    OwnerToken,
    ResourceVersion,
    StateBinding,
    StateCut,
    StorageError,
    digest,
    encode,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OwnerTransition
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import DatabaseImage, maintenance_owner
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import PgRestoreQuarantine, RestoreRequest


@dataclass(frozen=True)
class RestoreRecoveryContext:
    body: bytes
    trading_permission: bool = field(default=False, init=False)

    def json(self):
        return json.loads(self.body)

    @property
    def content_digest(self):
        return fingerprint(self.json())


@dataclass(frozen=True)
class RestoreRecoveryPreview:
    context: RestoreRecoveryContext
    cut: FinancialCut
    command: OwnerTransition
    trading_permission: bool = field(default=False, init=False)


class PgRestoreRecoveryContext(PgRestoreQuarantine):
    async def challenge(self, command, *, manifest, account_id):
        return await self._submit_context(command, manifest, account_id, False)

    async def preview(self, command, *, manifest, account_id):
        """Calculate a native recovery cut in an isolated, always-aborted tx.

        The old observation owner is temporarily reconstructed only inside
        that transaction; no permission, marker or owner change is committed.
        """
        return await self._submit_context(command, manifest, account_id, True)

    async def _submit_context(self, command, manifest, account_id, preview):
        if (type(command) is not RestoreRequest or type(manifest) is not DatabaseImage
                or command.manifest_sha256 != manifest.content_digest or str(command.source_lineage) != manifest.lineage
                or type(account_id) is not UUID or not account_id.int):
            raise StorageError('PG_RESTORE_RECOVERY_REQUEST_INVALID')
        if self._task is not None and not self._task.done():
            raise StorageError('PG_RESTORE_RECOVERY_BACKPRESSURE')
        self._task = asyncio.create_task(self._context_physical(command, manifest, account_id, preview))
        self._task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return await asyncio.shield(self._task)

    async def _context_physical(self, command, manifest, account_id, preview):
        try:
            async with asyncio.timeout(self.limits.timeout_ms / 1000):
                await maintenance_owner(self.connection)
                # Readback must not perform a first quarantine as a side effect.
                exists = await self.connection.fetchval('SELECT EXISTS(SELECT 1 FROM life_storage.restore_history WHERE operation_id=$1)', command.operation_id)
                if not exists:
                    raise StorageError('PG_RESTORE_RECOVERY_NOT_QUARANTINED')
                # Reuse the independent complete row/schema/account readback;
                # checking only the marker or audit checksum is insufficient.
                receipt = await self._transaction(command, manifest)
                if not receipt.replayed:
                    raise StorageError('PG_RESTORE_RECOVERY_NOT_QUARANTINED')
                context = await self._context(command, manifest, account_id)
                return await self._preview(context) if preview else context
        except TimeoutError:
            raise StorageError('PG_RESTORE_RECOVERY_TIMEOUT') from None

    @staticmethod
    def _owner(account):
        if account['owner_process'] is None:
            if any(account[k] is not None for k in ('owner_generation', 'current_session', 'owner_since', 'owner_until')):
                raise StorageError('PG_RESTORE_RECOVERY_OWNER_UNVERIFIED')
            return None
        return OwnerToken.from_json(dict(process_id=account['owner_process'], generation=account['owner_generation'],
                                         epoch=account['owner_epoch'], session_id=account['current_session'],
                                         valid_from=account['owner_since'], expires_at=account['owner_until'])).json()

    @staticmethod
    def _authorities(config):
        operator, credential = config.get('operator_verifier'), config.get('credential_authority')
        try:
            if type(operator) is not dict or not operator or type(credential) is not dict:
                raise ValueError()
            roles = set()
            for name, policy in operator.items():
                identifier(name)
                digest(policy['key_sha256'])
                if type(policy['roles']) is not list or any(type(role) is not str for role in policy['roles']):
                    raise ValueError()
                roles.update(policy['roles'])
            if not {'HANDOFF', 'RECOVER'} <= roles or credential.get('protocol') != 'OFFLINE_FAKE_V1':
                raise ValueError()
            identifier(credential['provider_id'])
            digest(credential['public_key_sha256'])
        except (ValueError, TypeError, KeyError, StorageError):
            raise StorageError('PG_RESTORE_RECOVERY_AUTHORITY_REQUIRED') from None
        return operator, credential

    async def _context(self, command, manifest, account_id):
        c = self.connection
        async with c.transaction(isolation='serializable'):
            await c.execute("SELECT set_config('lock_timeout',$1,true),set_config('statement_timeout',$1,true)", str(self.limits.timeout_ms))
            # Admission remains excluded across both verification and context
            # capture. A separate existing session is refused, not trusted.
            if (await c.fetchval('SELECT datallowconn FROM pg_database WHERE datname=current_database()')
                    or await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datid=(SELECT oid FROM pg_database WHERE datname=current_database()) AND pid<>pg_backend_pid())')
                    or await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_prepared_xacts WHERE database=current_database())')):
                raise StorageError('PG_RESTORE_TARGET_NOT_ISOLATED')
            meta = await c.fetchrow('SELECT lineage,restore_operation_id FROM life_storage.storage_metadata FOR UPDATE')
            if meta['lineage'] != command.target_lineage or meta['restore_operation_id'] != command.operation_id:
                raise StorageError('PG_RESTORE_RECOVERY_SCOPE_INVALID')
            audit = await c.fetchrow('SELECT * FROM life_storage.restore_history WHERE operation_id=$1', command.operation_id)
            if audit is None or any(str(audit[k]) != v for k, v in command.json().items()):
                raise StorageError('PG_RESTORE_RECOVERY_SCOPE_INVALID')
            before = {row['account_id']: row for row in json.loads(audit['before_accounts'])}
            source = before.get(str(account_id))
            current = await c.fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a WHERE account_id=$1 FOR UPDATE', account_id)
            if source is None or current is None:
                raise StorageError('PG_RESTORE_RECOVERY_ACCOUNT_INVALID')
            account = json.loads(current)
            if (account['environment'] != 'OFFLINE_SIMULATION' or account['mode'] != 'UNARMED' or not account['halted']
                    or any(account[k] is not None for k in ('owner_process', 'owner_generation', 'current_session', 'owner_since', 'owner_until'))
                    or account['risk_epoch'] != source['risk_epoch'] + 1):
                raise StorageError('PG_RESTORE_RECOVERY_ACCOUNT_INVALID')
            binding = StateBinding.from_json({k: account[k] for k in (
                'environment', 'exchange', 'account_uid', 'risk_pool', 'lineage', 'schema_version', 'policy_hash', 'config_hash')})
            config = await c.fetchval('SELECT body FROM life_financial.configurations WHERE account_id=$1 AND config_hash=$2 AND policy_hash=$3',
                                      account_id, binding.config_hash, binding.policy_hash)
            operator, credential = self._authorities({} if config is None else json.loads(config))
            head = await c.fetchrow('''SELECT h.grant_id,h.lease_epoch,r.principal,r.owner FROM life_financial.process_leases h
                JOIN life_financial.process_lease_registrations r USING(account_id,grant_id) WHERE h.account_id=$1''', account_id)
            if head is None:
                raise StorageError('PG_RESTORE_RECOVERY_PREDECESSOR_REQUIRED')
            predecessor = dict(grant_id=str(head['grant_id']), lease_epoch=head['lease_epoch'], principal=head['principal'],
                               owner=OwnerToken.from_json(json.loads(head['owner'])).json())
            body = encode(dict(format='LIFE_RESTORE_RECOVERY_CONTEXT_V1', restore=command.json(),
                               source_manifest_digest=manifest.content_digest, account_id=str(account_id), binding=binding.json(),
                               source_owner=self._owner(source), source_account=source, target_account=account, predecessor=predecessor,
                               operator_verifier=operator, credential_authority=credential))
            if len(body) > self.limits.max_row_bytes:
                raise StorageError('PG_RESTORE_RECOVERY_CAPACITY')
            return RestoreRecoveryContext(body)

    async def _native_view(self, account_id):
        value = await self.connection.fetchval('SELECT life_financial.spot_state($1)', account_id)
        return json.loads(value)

    async def _preview(self, context):
        return await self._tentative(context)

    async def _tentative(self, context, consume=None):
        c, body = self.connection, context.json()
        if body['source_owner'] is None:
            raise StorageError('PG_RESTORE_RECOVERY_LOGICAL_OWNER_REQUIRED')
        owner = OwnerToken.from_json(body['source_owner'])
        account_id = UUID(body['account_id'])
        tx = c.transaction(isolation='serializable')
        await tx.start()
        try:
            await c.execute("SELECT set_config('lock_timeout',$1,true),set_config('statement_timeout',$1,true)", str(self.limits.timeout_ms))
            if (await c.fetchval('SELECT datallowconn FROM pg_database WHERE datname=current_database()')
                    or await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE datid=(SELECT oid FROM pg_database WHERE datname=current_database()) AND pid<>pg_backend_pid())')
                    or await c.fetchval('SELECT EXISTS(SELECT 1 FROM pg_prepared_xacts WHERE database=current_database())')):
                raise StorageError('PG_RESTORE_TARGET_NOT_ISOLATED')
            meta = await c.fetchrow('SELECT lineage,restore_operation_id FROM life_storage.storage_metadata FOR UPDATE')
            current = await c.fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a WHERE account_id=$1 FOR UPDATE', account_id)
            if (str(meta['lineage']) != body['restore']['target_lineage'] or meta['restore_operation_id'] != body['restore']['operation_id']
                    or current is None or json.loads(current) != body['target_account']):
                raise StorageError('PG_RESTORE_RECOVERY_SCOPE_INVALID')
            # These two changes exist only in this isolated transaction. They
            # let the original native projection verify its historical owner
            # chain and compute the cut a future signed RECOVER must bind.
            # Public preview always rolls this reconstruction back. Only the
            # explicit signed importer can consume it in the same transaction.
            await c.execute('UPDATE life_storage.storage_metadata SET restore_operation_id=NULL')
            await c.execute('''UPDATE life_financial.accounts SET mode=$2,halted=$3,owner_process=$4,owner_generation=$5,
                owner_epoch=$6,current_session=$7,owner_since=$8,owner_until=$9 WHERE account_id=$1''',
                            account_id, body['source_account']['mode'], body['source_account']['halted'],
                            owner.process_id, owner.generation, owner.epoch, owner.session_id, owner.valid_from, owner.expires_at)
            view = await self._native_view(account_id)
            if (view['account']['version'] != body['target_account']['version']
                    or view['account']['database_lineage'] != body['restore']['target_lineage']
                    or view['account']['risk_epoch'] != body['target_account']['risk_epoch']):
                raise StorageError('PG_RESTORE_RECOVERY_PREVIEW_INVALID')
            version, state_digest = view['account']['version'], view['digest']
            binding = StateBinding.from_json(body['binding'])
            cut = FinancialCut(StateCut(binding, version, version, (ResourceVersion('account', version),), state_digest),
                               (('postgres_account', state_digest),))
            operation_id = 'restore-rec:' + fingerprint({'restore': body['restore'], 'account_id': body['account_id']})
            preview = RestoreRecoveryPreview(context, cut, OwnerTransition(operation_id, OwnerMode.RECOVERY_ONLY, owner))
            return preview if consume is None else await consume(preview, tx)
        finally:
            # Preview and every uncommitted import path abort reconstruction.
            # A disconnected backend also aborts the transaction.
            if not c.is_closed() and c.is_in_transaction():
                await tx.rollback()
