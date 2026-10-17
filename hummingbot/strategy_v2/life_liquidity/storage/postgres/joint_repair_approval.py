"""Privileged Ed25519 import for source-only joint repair; no activation."""
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorApproval, OperatorVerifier
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import PgJointBatchRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_repair import JointRepairFacts, ResolveJointSources
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error


@dataclass(frozen=True)
class JointRepairApprovalReceipt:
    operation_id: str
    pending_operation_id: str
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


class PgJointRepairApprovalImporter:
    def __init__(self, connection, batch, *, verifier, max_fact_age_ms):
        import asyncpg
        if (not isinstance(connection, asyncpg.Connection) or type(batch) is not PgJointBatchRepository
                or type(verifier) is not OperatorVerifier or type(max_fact_age_ms) is not int or not 0 < max_fact_age_ms <= 60000):
            raise StorageError('PG_JOINT_REPAIR_IMPORT_CONFIG_INVALID')
        batch._original()
        self.connection, self.batch, self.verifier = connection, batch, verifier
        self.financial, self.runtime, self.binding, self.owner = batch.financial, batch.runtime, batch.binding, batch.owner
        self.max_fact_age_ms, self._mutex = max_fact_age_ms, asyncio.Lock()

    def _original(self):
        self.batch._original()
        if (self.batch.financial is not self.financial or self.batch.runtime is not self.runtime
                or self.batch.binding != self.binding or self.batch.owner != self.owner):
            raise StorageError('PG_JOINT_REPAIR_CONTEXT_CHANGED')

    async def _owner(self):
        self._original()
        c, cfg = self.connection, self.runtime.config
        if c.is_closed() or c.is_in_transaction():
            raise StorageError('PG_JOINT_REPAIR_IMPORT_CONNECTION_INVALID')
        row = await c.fetchrow("""SELECT current_user::text AS principal,
            (SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname='life_financial') AS owner,
            (SELECT lineage FROM life_storage.storage_metadata WHERE singleton) AS lineage,
            current_setting('synchronous_commit') AS sync,current_setting('fsync') AS fsync,
            current_setting('full_page_writes') AS writes,pg_is_in_recovery() AS replica""")
        if (row['principal'] != row['owner'] or row['lineage'] != cfg.database_lineage or row['replica']
                or any(row[k] != 'on' for k in ('sync', 'fsync', 'writes'))):
            raise StorageError('PG_JOINT_REPAIR_IMPORT_OWNER_REQUIRED')

    async def _row(self, operation_id):
        c, account = self.connection, self.runtime.config.account_id
        if not await c.fetchval('SELECT life_financial.joint_repair_approval_integrity($1)', account):
            raise StorageUnavailable('PG_JOINT_REPAIR_APPROVAL_UNVERIFIED')
        row = await c.fetchrow('''SELECT CASE WHEN octet_length(body::text)<=1048576 THEN body END AS body,captured_at
            FROM life_financial.joint_repair_approvals WHERE account_id=$1 AND operation_id=$2''', account, operation_id)
        if row is None:
            return None
        if row['body'] is None:
            raise StorageUnavailable('PG_JOINT_REPAIR_IMPORT_PAYLOAD_BOUND')
        record = json.loads(row['body'])
        if record['approval']['binding'] != self.binding.json() or record['verifier_policy'] != self.verifier.policy():
            raise StorageError('PG_JOINT_REPAIR_IMPORT_SCOPE_INVALID')
        self.verifier.audit(record, self.binding)
        approval = OperatorApproval(**{**record['approval'], 'binding': self.binding,
                                       'issued_at': datetime.fromisoformat(record['approval']['issued_at']),
                                       'expires_at': datetime.fromisoformat(record['approval']['expires_at']), 'signature': record['signature']})
        command, facts = ResolveJointSources.from_json(record['payload']), JointRepairFacts.from_json(record['facts'])
        self.verifier.verify(approval, self.binding, 'RECOVER', command.json(), FinancialCut.from_json(record['cut']), facts, row['captured_at'])
        return record

    async def lookup(self, operation_id):
        identifier(operation_id)
        if self._mutex.locked():
            raise StorageUnavailable('PG_JOINT_REPAIR_IMPORT_BACKPRESSURE')
        async with self._mutex, asyncio.timeout(self.runtime.config.transaction_timeout_ms / 1000):
            await self._owner()
            record = await self._row(operation_id)
            return None if record is None else JointRepairApprovalReceipt(operation_id, record['payload']['pending_operation_id'], True)

    async def _commit(self, transaction):
        await transaction.commit()

    async def approve(self, command, *, approval, cut, facts):
        self._original()
        if (type(command) is not ResolveJointSources or type(approval) is not OperatorApproval or type(cut) is not FinancialCut
                or type(facts) is not JointRepairFacts or cut.checkpoint.binding != self.binding or facts.binding != self.binding):
            raise StorageError('PG_JOINT_REPAIR_IMPORT_COMMAND_INVALID')
        self.financial._cut(cut, self.owner)
        c, cfg = self.connection, self.runtime.config
        if self._mutex.locked():
            raise StorageUnavailable('PG_JOINT_REPAIR_IMPORT_BACKPRESSURE')
        async with self._mutex:
            async with asyncio.timeout(cfg.transaction_timeout_ms / 1000):
                await self._owner()
            transaction, committing = c.transaction(isolation='read_committed'), False
            try:
                async with asyncio.timeout(cfg.transaction_timeout_ms / 1000):
                    await transaction.start()
                    for name, value in (('statement_timeout', cfg.statement_timeout_ms), ('lock_timeout', cfg.statement_timeout_ms),
                                        ('idle_in_transaction_session_timeout', cfg.idle_transaction_timeout_ms)):
                        await c.execute('SELECT set_config($1,$2,true)', name, str(value))
                    account_raw = await c.fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a WHERE account_id=$1 FOR UPDATE', cfg.account_id)
                    record = await self._row(command.operation_id)
                    expected = dict(payload=command.json(), approval=approval.body(), signature=approval.signature,
                                    cut=cut.json(), facts=facts.json(), verifier_policy=self.verifier.policy())
                    if record is not None:
                        if any(record[k] != v for k, v in expected.items()):
                            raise StorageError('PG_JOINT_REPAIR_IMPORT_IDENTITY_CONFLICT')
                        await transaction.rollback()
                        return JointRepairApprovalReceipt(command.operation_id, command.pending_operation_id, True)
                    observed = await self.financial.observation_cut(self.binding)
                    if observed.cut != cut:
                        raise StorageError('PG_JOINT_REPAIR_IMPORT_CUT_STALE')
                    policy = await c.fetchrow('SELECT * FROM life_financial.joint_repair_policies WHERE account_id=$1 AND policy_hash=$2', cfg.account_id, self.binding.policy_hash)
                    if policy is None or policy['max_fact_age_ms'] != self.max_fact_age_ms:
                        raise StorageError('PG_JOINT_REPAIR_IMPORT_POLICY_MISMATCH')
                    now = await c.fetchval('SELECT clock_timestamp()')
                    if not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self.max_fact_age_ms:
                        raise StorageError('PG_JOINT_REPAIR_IMPORT_FACTS_UNQUALIFIED')
                    key_policy = await c.fetchval("SELECT body->'operator_verifier' FROM life_financial.configurations WHERE account_id=$1 AND config_hash=$2 AND policy_hash=$3",
                                                  cfg.account_id, self.binding.config_hash, self.binding.policy_hash)
                    if key_policy is None or json.loads(key_policy) != self.verifier.policy():
                        raise StorageError('PG_JOINT_REPAIR_IMPORT_KEY_POLICY_MISMATCH')
                    self.verifier.verify(approval, self.binding, 'RECOVER', command.json(), cut, facts, now)
                    if not await c.fetchval('SELECT life_financial.joint_repair_profile($1,$2::jsonb,$3::jsonb,$4)', cfg.account_id,
                                            json.dumps(command.json()), json.dumps(facts.json()), cut.checkpoint.revision):
                        raise StorageError('PG_JOINT_REPAIR_IMPORT_PROFILE_INVALID')
                    raw = await c.fetchval('SELECT command FROM life_financial.joint_financial_batches WHERE account_id=$1 AND operation_id=$2', cfg.account_id, command.pending_operation_id)
                    expected.update(account=json.loads(account_raw), pending_source_hash=fingerprint(json.loads(raw)))
                    body = json.dumps(expected, separators=(',', ':'), sort_keys=True)
                    count, size = await c.fetchrow('SELECT count(*),COALESCE(sum(octet_length(to_jsonb(r)::text)),0) FROM life_financial.joint_repair_approvals r WHERE account_id=$1', cfg.account_id)
                    if len(body.encode()) > 1048576 or count >= policy['max_approvals'] or size + len(body.encode()) > policy['max_total_bytes']:
                        raise StorageError('PG_JOINT_REPAIR_BOUND')
                    await c.execute('''INSERT INTO life_financial.joint_repair_approvals VALUES($1,$2,$3,$4::jsonb,
                        encode(sha256(convert_to(($4::jsonb)::text,'UTF8')),'hex'),$5)''', cfg.account_id, command.operation_id, command.pending_operation_id, body, now)
                    committing = True
                    await self._commit(transaction)
                    self._original()
                    return JointRepairApprovalReceipt(command.operation_id, command.pending_operation_id, False)
            except BaseException as error:
                import asyncpg
                try:
                    if not c.is_closed() and c.is_in_transaction():
                        await transaction.rollback()
                except Exception:
                    pass
                if isinstance(error, (asyncpg.SerializationError, asyncpg.DeadlockDetectedError)):
                    raise StorageConflict('PG_JOINT_REPAIR_IMPORT_RETRY_REQUIRED') from None
                if isinstance(error, asyncpg.PostgresError) and error.sqlstate and (
                        error.sqlstate.startswith('23') or error.sqlstate in ('P0001', '55000', '22023', '22P02')):
                    raise sql_error(error) from None
                if committing:
                    raise CommitUnknown('PG_JOINT_REPAIR_IMPORT_COMMIT_UNKNOWN') from None
                if isinstance(error, (StorageError, asyncio.CancelledError)):
                    raise
                raise StorageUnavailable('PG_JOINT_REPAIR_IMPORT_ABORTED') from None
