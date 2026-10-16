"""Separate privileged signed approval import. No cashflow application or grant."""
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import (
    ApproveSpotCashflow,
    OperatorApproval,
    OperatorVerifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


@dataclass(frozen=True)
class ApprovalImportReceipt:
    operation_id: str
    bill_id: str
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


class PgCashflowApprovalImporter:
    def __init__(self, connection, financial, *, verifier, max_fact_age_ms):
        import asyncpg
        if (not isinstance(connection, asyncpg.Connection) or type(financial) is not PgSpotRepository
                or type(verifier) is not OperatorVerifier or type(max_fact_age_ms) is not int or not 0 < max_fact_age_ms <= 60000):
            raise StorageError('PG_APPROVAL_IMPORT_CONFIG_INVALID')
        self.connection, self.financial, self.verifier = connection, financial, verifier
        self.binding, self.max_fact_age_ms = financial.binding, max_fact_age_ms
        self._mutex = asyncio.Lock()

    async def _owner(self):
        c, cfg = self.connection, self.financial._runtime.config
        if c.is_closed() or c.is_in_transaction():
            raise StorageError('PG_APPROVAL_IMPORT_CONNECTION_INVALID')
        row = await c.fetchrow("""SELECT current_user::text AS principal,
            (SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname='life_financial') AS owner,
            (SELECT lineage FROM life_storage.storage_metadata WHERE singleton) AS lineage,
            current_setting('synchronous_commit') AS sync,current_setting('fsync') AS fsync,
            current_setting('full_page_writes') AS writes,pg_is_in_recovery() AS replica""")
        if (row['principal'] != row['owner'] or row['lineage'] != cfg.database_lineage or row['replica']
                or any(row[k] != 'on' for k in ('sync', 'fsync', 'writes'))):
            raise StorageError('PG_APPROVAL_IMPORT_OWNER_REQUIRED')

    async def _row(self, operation_id):
        if not await self.connection.fetchval('SELECT life_financial.spot_operator_approval_integrity($1)', self.financial._runtime.config.account_id):
            raise StorageUnavailable('PG_APPROVAL_HISTORY_UNVERIFIED')
        row = await self.connection.fetchrow('SELECT CASE WHEN octet_length(body::text)<=1048576 THEN body END AS body,captured_at FROM life_financial.spot_operator_approvals WHERE account_id=$1 AND operation_id=$2',
                                             self.financial._runtime.config.account_id, operation_id)
        if row is not None:
            if row['body'] is None:
                raise StorageUnavailable('PG_APPROVAL_IMPORT_PAYLOAD_BOUND')
            record = json.loads(row['body'])
            if record['approval']['binding'] != self.binding.json() or record['verifier_policy'] != self.verifier.policy():
                raise StorageError('PG_APPROVAL_IMPORT_SCOPE_INVALID')
            self.verifier.audit(record, self.binding)
            approval = OperatorApproval(**{**record['approval'], 'binding': self.binding,
                                           'issued_at': datetime.fromisoformat(record['approval']['issued_at']),
                                           'expires_at': datetime.fromisoformat(record['approval']['expires_at']),
                                           'signature': record['signature']})
            self.verifier.verify(approval, self.binding, 'APPROVE_CASHFLOW', record['payload'],
                                 FinancialCut.from_json(record['cut']), SpotFinancialRecoveryFacts.from_json(record['facts']), row['captured_at'])
            return record
        return None

    async def lookup(self, operation_id):
        identifier(operation_id)
        if self._mutex.locked():
            raise StorageUnavailable('PG_APPROVAL_IMPORT_BACKPRESSURE')
        async with self._mutex, asyncio.timeout(self.financial._runtime.config.transaction_timeout_ms / 1000):
            await self._owner()
            record = await self._row(operation_id)
            if record is None:
                return None
            return ApprovalImportReceipt(operation_id, record['payload']['bill']['bill_id'], True)

    async def _commit(self, transaction):
        await transaction.commit()

    async def approve(self, command, *, approval, cut, facts):
        if (type(command) is not ApproveSpotCashflow or type(approval) is not OperatorApproval
                or type(cut) is not FinancialCut or type(facts) is not SpotFinancialRecoveryFacts
                or cut.checkpoint.binding != self.binding or facts.binding != self.binding):
            raise StorageError('PG_APPROVAL_IMPORT_COMMAND_INVALID')
        c, cfg = self.connection, self.financial._runtime.config
        if self._mutex.locked():
            raise StorageUnavailable('PG_APPROVAL_IMPORT_BACKPRESSURE')
        async with self._mutex:
            async with asyncio.timeout(cfg.transaction_timeout_ms / 1000):
                await self._owner()
            transaction, committing = c.transaction(isolation='read_committed'), False
            try:
                async with asyncio.timeout(cfg.transaction_timeout_ms / 1000):
                    await transaction.start()
                    await c.execute("SELECT set_config('statement_timeout',$1,true)", str(cfg.statement_timeout_ms))
                    await c.execute("SELECT set_config('lock_timeout',$1,true)", str(cfg.statement_timeout_ms))
                    await c.execute("SELECT set_config('idle_in_transaction_session_timeout',$1,true)", str(cfg.idle_transaction_timeout_ms))
                    account_raw = await c.fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a WHERE account_id=$1 FOR UPDATE', cfg.account_id)
                    record = await self._row(command.operation_id)
                    expected = {'payload': command.json(), 'approval': approval.body(), 'signature': approval.signature,
                                'cut': cut.json(), 'facts': facts.json(), 'verifier_policy': self.verifier.policy()}
                    if record is not None:
                        if any(record[k] != v for k, v in expected.items()):
                            raise StorageError('PG_APPROVAL_IMPORT_IDENTITY_CONFLICT')
                        await transaction.rollback()
                        return ApprovalImportReceipt(command.operation_id, command.bill.bill_id, True)
                    observed = await self.financial.observation_cut(self.binding)
                    if observed.cut != cut:
                        raise StorageError('PG_APPROVAL_IMPORT_CUT_STALE')
                    now = await c.fetchval('SELECT clock_timestamp()')
                    if (not all((facts.order_scope_complete, facts.bill_scope_complete, facts.runner_scope_complete))
                            or command.bill.bill_id not in facts.account_cashflow_ids
                            or not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self.max_fact_age_ms):
                        raise StorageError('PG_APPROVAL_IMPORT_FACTS_UNQUALIFIED')
                    policy = await c.fetchval('SELECT body->\'operator_verifier\' FROM life_financial.configurations WHERE account_id=$1 AND config_hash=$2 AND policy_hash=$3',
                                              cfg.account_id, self.binding.config_hash, self.binding.policy_hash)
                    if policy is None or json.loads(policy) != self.verifier.policy():
                        raise StorageError('PG_APPROVAL_IMPORT_KEY_POLICY_MISMATCH')
                    self.verifier.verify(approval, self.binding, 'APPROVE_CASHFLOW', command.json(), cut, facts, now)
                    capital = await c.fetchrow('SELECT opened_at,anchor_bill FROM life_financial.spot_capital_policies WHERE account_id=$1 AND policy_hash=$2', cfg.account_id, self.binding.policy_hash)
                    bill = command.bill
                    if (capital is None or int(bill.bill_id) <= int(capital['anchor_bill'])
                            or not int(capital['opened_at'].timestamp() * 1000) <= bill.transfer_at_ms <= int(now.timestamp() * 1000)):
                        raise StorageError('PG_APPROVAL_IMPORT_BILL_UNQUALIFIED')
                    expected['account'] = json.loads(account_raw)
                    await c.execute('INSERT INTO life_financial.bill_approvals VALUES($1,$2,$3,$4,to_timestamp($5::numeric/1000),$6)',
                                    cfg.account_id, bill.bill_id, bill.currency, bill.signed_amount, bill.transfer_at_ms, bytes.fromhex(approval.signature))
                    body = json.dumps(expected, separators=(',', ':'), sort_keys=True)
                    if len(body.encode('utf-8')) > 1048576:
                        raise StorageError('PG_APPROVAL_IMPORT_PAYLOAD_BOUND')
                    await c.execute('''INSERT INTO life_financial.spot_operator_approvals
                        VALUES($1,$2,$3,$4::jsonb,encode(sha256(convert_to(($4::jsonb)::text,'UTF8')),'hex'),$5,$6)''',
                                    cfg.account_id, command.operation_id, bill.bill_id, body, now, self.max_fact_age_ms)
                    committing = True
                    await self._commit(transaction)
                    return ApprovalImportReceipt(command.operation_id, bill.bill_id, False)
            except BaseException as error:
                import asyncpg
                try:
                    if not c.is_closed() and c.is_in_transaction():
                        await transaction.rollback()
                except Exception:
                    pass
                if isinstance(error, (asyncpg.SerializationError, asyncpg.DeadlockDetectedError)):
                    raise StorageConflict('PG_APPROVAL_IMPORT_RETRY_REQUIRED') from None
                if isinstance(error, asyncpg.PostgresError) and error.sqlstate and (
                        error.sqlstate.startswith('23') or error.sqlstate in ('P0001', '55000', '22023', '22P02')):
                    raise sql_error(error) from None
                if committing:
                    raise CommitUnknown('PG_APPROVAL_IMPORT_COMMIT_UNKNOWN') from None
                if isinstance(error, (StorageError, asyncio.CancelledError)):
                    raise
                raise StorageUnavailable('PG_APPROVAL_IMPORT_ABORTED') from None
