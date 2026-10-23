"""Signed owner-side restore bridge. No activation or private lease is issued.

The target stays connection-excluded through physical COMMIT. Complete cold
quarantine verification precedes an atomic original-owner RECOVER, signed
restore audit and principal rotation. The replacement actor must still register
and claim its own private observation capability through the native workflow.
"""
import asyncio
import inspect
import json
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    OwnerToken,
    StateBinding,
    StorageError,
    StorageUnavailable,
    fingerprint,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorApproval, OperatorVerifier
from hummingbot.strategy_v2.life_liquidity.storage.generation_exclusion import (
    CredentialExclusion,
    CredentialExclusionVerifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.access import provision_access
from hummingbot.strategy_v2.life_liquidity.storage.postgres.backup import DatabaseImage, maintenance_owner
from hummingbot.strategy_v2.life_liquidity.storage.postgres.ownership import ProcessRegistration
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore import RestoreRequest
from hummingbot.strategy_v2.life_liquidity.storage.postgres.restore_recovery import PgRestoreRecoveryContext
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


@dataclass(frozen=True)
class RestoreRecoveryReceipt:
    restore_operation: str
    account_id: UUID
    control_operation: str
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


class PgRestoreRecoveryImporter(PgRestoreRecoveryContext):
    def __init__(self, connection, *, limits, operator_verifier, credential_verifier):
        super().__init__(connection, limits=limits)
        if type(operator_verifier) is not OperatorVerifier or type(credential_verifier) is not CredentialExclusionVerifier:
            raise StorageError('PG_RESTORE_RECOVERY_VERIFIER_REQUIRED')
        if credential_verifier.public_key in operator_verifier.keys.values():
            raise StorageError('PG_RESTORE_RECOVERY_INDEPENDENT_AUTHORITY_REQUIRED')
        self.verifier, self.credential_verifier = operator_verifier, credential_verifier
        self._verifiers = operator_verifier, credential_verifier

    @staticmethod
    def request(preview, registration, certificate):
        if type(registration) is not ProcessRegistration or type(certificate) is not CredentialExclusion:
            raise StorageError('PG_RESTORE_RECOVERY_REGISTRATION_INVALID')
        return dict(kind='RESTORE_RECOVERY_V1', context_digest=preview.context.content_digest,
                    restore=preview.context.json()['restore'], account_id=str(registration.account_id),
                    recover=preview.command.json(), registration=dict(account_id=str(registration.account_id),
                                                                      database_lineage=str(registration.database_lineage), principal=registration.principal,
                                                                      owner=registration.owner.json(), capability_hash=registration.capability_hash),
                    exclusion_digest=fingerprint(dict(body=certificate.body(), signature=certificate.signature)))

    async def import_recovery(self, command, *, manifest, account_id, registration, certificate, authorize):
        self._validate(command, manifest, account_id)
        if type(registration) is not ProcessRegistration or type(certificate) is not CredentialExclusion or not callable(authorize):
            raise StorageError('PG_RESTORE_RECOVERY_REGISTRATION_INVALID')
        return await self._submit_import(self._import(command, manifest, account_id, registration, certificate, authorize))

    async def lookup(self, command, *, manifest, account_id):
        self._validate(command, manifest, account_id)
        return await self._submit_import(self._lookup(command, manifest, account_id))

    def _validate(self, command, manifest, account_id):
        if (self._verifiers != (self.verifier, self.credential_verifier)
                or type(command) is not RestoreRequest or type(manifest) is not DatabaseImage
                or command.manifest_sha256 != manifest.content_digest or str(command.source_lineage) != manifest.lineage
                or type(account_id) is not UUID or not account_id.int):
            raise StorageError('PG_RESTORE_RECOVERY_REQUEST_INVALID')

    async def _submit_import(self, coroutine):
        if self._task is not None and not self._task.done():
            coroutine.close()
            raise StorageError('PG_RESTORE_RECOVERY_BACKPRESSURE')
        self._task = asyncio.create_task(self._bounded(coroutine))
        self._task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return await asyncio.shield(self._task)

    async def _bounded(self, coroutine):
        try:
            async with asyncio.timeout(self.limits.timeout_ms / 1000):
                await maintenance_owner(self.connection)
                return await coroutine
        except TimeoutError:
            raise StorageError('PG_RESTORE_RECOVERY_TIMEOUT') from None
        finally:
            coroutine.close()

    async def _import(self, command, manifest, account_id, registration, certificate, authorize):
        prior = await self._lookup(command, manifest, account_id)
        if prior is not None:
            # Public immutable readback is never private actor admission.
            raise StorageError('PG_RESTORE_RECOVERY_ALREADY_IMPORTED_USE_LOOKUP')
        context = await self._context_physical(command, manifest, account_id, False)
        # This release protocol is one complete managed account per namespace.
        # Do not partially clear a global barrier for a multi-account restore;
        # that requires its own all-account signed recovery protocol.
        if await self.connection.fetchval('SELECT count(*) FROM life_financial.accounts') != 1:
            raise StorageError('PG_RESTORE_RECOVERY_SINGLE_ACCOUNT_REQUIRED')

        async def consume(preview, transaction):
            return await self._consume(preview, transaction, registration, certificate, authorize)
        return await self._tentative(context, consume)

    async def _policies(self, account_id, binding):
        c = self.connection
        policy = await c.fetchrow('SELECT * FROM life_financial.control_policies WHERE account_id=$1 AND policy_hash=$2', account_id, binding.policy_hash)
        rows = await c.fetch('SELECT body FROM life_financial.control_records WHERE account_id=$1 ORDER BY sequence', account_id)
        size = sum(len(r['body'].encode()) for r in rows)
        if policy is None or len(rows) >= policy['max_records'] or size > policy['max_total_bytes']:
            raise StorageError('PG_RESTORE_RECOVERY_CONTROL_BOUND')
        if not await c.fetchval('SELECT life_financial.control_verified($1)', account_id):
            raise StorageUnavailable('PG_RESTORE_RECOVERY_CONTROL_UNVERIFIED')
        for row in rows:
            body = json.loads(row['body'])
            if body['verifier_policy'] != self.verifier.policy():
                raise StorageError('PG_RESTORE_RECOVERY_POLICY_CHANGED')
            self.verifier.audit(body, binding)
        return policy, size

    async def _consume(self, preview, tx, registration, certificate, authorize):
        c, context = self.connection, preview.context.json()
        account_id, binding = UUID(context['account_id']), preview.cut.checkpoint.binding
        predecessor = context['predecessor']
        owner = preview.command.token
        if (context['operator_verifier'] != self.verifier.policy() or context['credential_authority'] != self.credential_verifier.policy()
                or registration.account_id != account_id or str(registration.database_lineage) != context['restore']['target_lineage']
                or registration.principal == predecessor['principal'] or registration.owner.epoch != owner.epoch
                or registration.owner.session_id != owner.session_id):
            raise StorageError('PG_RESTORE_RECOVERY_POLICY_OR_REGISTRATION_INVALID')
        policy, size = await self._policies(account_id, binding)
        now = await c.fetchval('SELECT clock_timestamp()')
        self.credential_verifier.verify(certificate, binding=binding, database_lineage=registration.database_lineage,
                                        predecessor_grant=UUID(predecessor['grant_id']), predecessor_epoch=predecessor['lease_epoch'],
                                        predecessor_owner=OwnerToken.from_json(predecessor['owner']), target_registration=registration, now=now)
        identity = await c.fetchval('SELECT life_financial.process_generation_identity($1,$2)', account_id, UUID(predecessor['grant_id']))
        if predecessor['lease_epoch'] > 1 and (identity is None or json.loads(identity) != certificate.source.json()):
            raise StorageError('PG_RESTORE_RECOVERY_PREDECESSOR_IDENTITY_CHANGED')
        if (registration.owner.expires_at - now).total_seconds() * 1000 > policy['successor_duration_ms']:
            raise StorageError('PG_RESTORE_RECOVERY_OWNER_WINDOW_INVALID')
        # Existing safe role only; no CREATE ROLE/account/balance/grant. Grant
        # work precedes the fresh external observation/signature callback.
        await provision_access(c, principal=registration.principal, account_id=account_id, write=True)
        await c.execute('''UPDATE life_financial.access_bindings SET can_write=false
            WHERE account_id=$1 AND principal NOT IN (current_user,$2)''', account_id, registration.principal)
        value = authorize(preview)
        if not inspect.isawaitable(value):
            raise StorageError('PG_RESTORE_RECOVERY_ASYNC_AUTHORIZATION_REQUIRED')
        value = await value
        if type(value) is not tuple or len(value) != 3:
            raise StorageError('PG_RESTORE_RECOVERY_AUTHORIZATION_INVALID')
        facts, recover, approval = value
        if (type(facts) is not SpotFinancialRecoveryFacts or type(recover) is not OperatorApproval or type(approval) is not OperatorApproval
                or facts.binding != binding):
            raise StorageError('PG_RESTORE_RECOVERY_AUTHORIZATION_INVALID')
        now = await c.fetchval('SELECT clock_timestamp()')
        if not certificate.observed_at <= facts.observed_at <= now or (now - facts.observed_at).total_seconds() * 1000 > policy['max_fact_age_ms']:
            raise StorageError('PG_RESTORE_RECOVERY_FACTS_STALE')
        self.credential_verifier.verify(certificate, binding=binding, database_lineage=registration.database_lineage,
                                        predecessor_grant=UUID(predecessor['grant_id']), predecessor_epoch=predecessor['lease_epoch'],
                                        predecessor_owner=OwnerToken.from_json(predecessor['owner']), target_registration=registration, now=now)
        payload = self.request(preview, registration, certificate)
        self.verifier.verify(recover, binding, 'RECOVER', preview.command.json(), preview.cut, facts, now)
        self.verifier.verify(approval, binding, 'HANDOFF', payload, preview.cut, facts, now)
        before = json.loads(await c.fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a WHERE account_id=$1', account_id))
        view = await self._native_view(account_id)
        if (view['account'] != before or view['digest'] != preview.cut.checkpoint.state_digest):
            raise StorageError('PG_RESTORE_RECOVERY_CUT_CHANGED')
        await c.execute("UPDATE life_financial.accounts SET mode='RECOVERY_ONLY',version=version+1 WHERE account_id=$1", account_id)
        after = json.loads(await c.fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a WHERE account_id=$1', account_id))
        op = preview.command.operation_id
        native_receipt = dict(kind='NATIVE_CONTROL', operation_id=op, mode='RECOVERY_ONLY', token=owner.json(),
                              version=after['version'], replayed=False, trading_permission=False)
        expected = dict(payload=preview.command.json(), approval=recover.body(), signature=recover.signature,
                        cut=preview.cut.json(), facts=facts.json(), verifier_policy=self.verifier.policy())
        record = dict(**expected, before_account=before, after_account=after, receipt=native_receipt, grant_id=None)
        raw = json.dumps(record)
        if size + len(raw.encode()) > policy['max_total_bytes']:
            raise StorageError('PG_RESTORE_RECOVERY_CONTROL_BOUND')
        source_hash = await c.fetchval("SELECT encode(sha256(convert_to(($1::jsonb)::text,'UTF8')),'hex')", raw)
        await c.execute('INSERT INTO life_financial.control_records(account_id,operation_id,body,source_hash,applied_at) VALUES($1,$2,$3::jsonb,$4,$5)',
                        account_id, op, raw, source_hash, now)
        await c.execute('INSERT INTO life_financial.operations VALUES($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7)', account_id, op,
                        fingerprint(expected), json.dumps(dict(kind='NATIVE_CONTROL', record_hash=source_hash)), json.dumps(native_receipt), after['version'], now)
        receipt = dict(restore_operation=context['restore']['operation_id'], account_id=str(account_id), control_operation=op,
                       replayed=False, trading_permission=False)
        audit = dict(context=context, request=payload, approval=approval.body(), signature=approval.signature,
                     cut=preview.cut.json(), facts=facts.json(), operator_policy=self.verifier.policy(), credential_policy=self.credential_verifier.policy(),
                     exclusion=dict(body=certificate.body(), signature=certificate.signature), receipt=receipt)
        body = json.dumps(audit)
        if len(body.encode()) > 131072:
            raise StorageError('PG_RESTORE_RECOVERY_RECORD_BOUND')
        await c.execute('''INSERT INTO life_storage.restore_recovery_imports VALUES($1,$2,$3,$4::jsonb,
            encode(sha256(convert_to(($4::jsonb)::text,'UTF8')),'hex'),$5)''', context['restore']['operation_id'], account_id, op, body, now)
        try:
            await self._commit(tx)
        except BaseException as error:
            import asyncpg
            if isinstance(error, asyncpg.PostgresError) and error.sqlstate and (error.sqlstate.startswith('23') or error.sqlstate == 'P0001'):
                raise sql_error(error) from None
            raise CommitUnknown('PG_RESTORE_RECOVERY_COMMIT_UNKNOWN') from None
        return RestoreRecoveryReceipt(receipt['restore_operation'], account_id, op, False)

    async def _lookup(self, command, manifest, account_id):
        c = self.connection
        row = await c.fetchrow('''SELECT body,registered_at,source_hash,
            encode(sha256(convert_to(body::text,'UTF8')),'hex') AS actual FROM life_storage.restore_recovery_imports
            WHERE restore_operation=$1 AND account_id=$2''', command.operation_id, account_id)
        if row is None:
            return None
        b = json.loads(row['body'])
        namespace = await c.fetchrow('SELECT lineage,restore_operation_id FROM life_storage.storage_metadata')
        history = await c.fetchrow('SELECT * FROM life_storage.restore_history WHERE operation_id=$1', command.operation_id)
        if (row['source_hash'] != row['actual'] or b['context']['restore'] != command.json()
                or b['context']['source_manifest_digest'] != manifest.content_digest or b['context']['account_id'] != str(account_id)
                or b['operator_policy'] != self.verifier.policy() or b['credential_policy'] != self.credential_verifier.policy()
                or namespace['lineage'] != command.target_lineage or namespace['restore_operation_id'] is not None
                or history is None or any(str(history[k]) != v for k, v in command.json().items())
                or b['context']['source_account'] not in json.loads(history['before_accounts'])
                or not await c.fetchval('SELECT life_financial.control_verified($1)', account_id)):
            raise StorageError('PG_RESTORE_RECOVERY_READBACK_UNVERIFIED')
        binding = StateBinding.from_json(b['context']['binding'])
        # Historical verification checks the original signatures at the consumed
        # cut; current certificate expiry does not undo committed observation.
        self.verifier.audit(dict(payload=b['request'], approval=b['approval'], signature=b['signature'], cut=b['cut'], facts=b['facts']), binding)
        native = await c.fetchval('SELECT body FROM life_financial.control_records WHERE account_id=$1 AND operation_id=$2', account_id, b['receipt']['control_operation'])
        if native is None:
            raise StorageError('PG_RESTORE_RECOVERY_READBACK_UNVERIFIED')
        native = json.loads(native)
        self.verifier.audit(native, binding)
        if (native['payload'] != b['request']['recover'] or native['cut'] != b['cut'] or native['facts'] != b['facts']
                or b['request']['context_digest'] != fingerprint(b['context'])
                or b['request']['exclusion_digest'] != fingerprint(b['exclusion'])
                or b['receipt']['restore_operation'] != command.operation_id or b['receipt']['account_id'] != str(account_id)
                or b['receipt']['trading_permission'] is not False):
            raise StorageError('PG_RESTORE_RECOVERY_READBACK_UNVERIFIED')
        credential = b['exclusion']['body']
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode
        try:
            Ed25519PublicKey.from_public_bytes(self.credential_verifier.public_key).verify(bytes.fromhex(b['exclusion']['signature']), encode(credential))
        except (InvalidSignature, ValueError, TypeError):
            raise StorageError('PG_RESTORE_RECOVERY_READBACK_UNVERIFIED') from None
        if not datetime.fromisoformat(credential['observed_at']) <= row['registered_at'] < datetime.fromisoformat(credential['expires_at']):
            raise StorageError('PG_RESTORE_RECOVERY_READBACK_UNVERIFIED')
        return RestoreRecoveryReceipt(command.operation_id, account_id, b['receipt']['control_operation'], True)
