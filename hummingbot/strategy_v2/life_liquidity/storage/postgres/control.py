"""Privileged signed logical ownership control, separate from PG4 sender fencing.

Only externally supplied public verifier keys are retained. Native effects and
original approval/receipt commit together; replay never restores local admission.
"""
import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    OwnerMode,
    OwnerToken,
    StateBinding,
    StorageError,
    StorageUnavailable,
    digest,
    fingerprint,
    identifier,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import (
    ControlReceipt,
    OperatorApproval,
    OperatorVerifier,
    OwnerTransition,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut, decimal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.session import SessionTick


@dataclass(frozen=True)
class NativeControlReceipt(ControlReceipt):
    trading_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class NativeExecutionMode:
    operation_id: str
    mode: str
    campaign_id: str
    policy_digest: str
    observer_id: UUID

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.campaign_id)
        digest(self.policy_digest)
        if len(self.operation_id) > 96 or self.mode not in ('MM', 'INVENTORY', 'EXIT') or type(self.observer_id) is not UUID or self.observer_id.int == 0:
            raise StorageError('PG_EXECUTION_MODE_COMMAND_INVALID')

    @property
    def role(self):
        return 'INVENTORY'

    @property
    def token(self):
        return None

    def json(self):
        return dict(kind='EXECUTION_MODE', operation_id=self.operation_id, mode=self.mode, campaign_id=self.campaign_id,
                    policy_digest=self.policy_digest, observer_id=str(self.observer_id))


@dataclass(frozen=True)
class NativeExecutionModeReceipt:
    binding: StateBinding
    operation_id: str
    mode: str
    observer_id: UUID
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ReconcileSessionSuccessor:
    operation_id: str
    owner: OwnerToken
    market_anchor_usdt: Decimal
    reference_observed_at: datetime
    observer_id: UUID

    def __post_init__(self):
        identifier(self.operation_id)
        utc(self.reference_observed_at)
        if (len(self.operation_id) > 96 or type(self.owner) is not OwnerToken
                or type(self.observer_id) is not UUID or self.observer_id.int == 0):
            raise StorageError('PG_SUCCESSOR_COMMAND_INVALID')
        object.__setattr__(self, 'market_anchor_usdt', decimal(self.market_anchor_usdt, positive=True))

    @property
    def role(self):
        return 'OBSERVE_SESSION'

    def json(self):
        return dict(kind='SESSION_SUCCESSOR', operation_id=self.operation_id, owner=self.owner.json(),
                    market_anchor_usdt=str(self.market_anchor_usdt), reference_observed_at=self.reference_observed_at.isoformat(),
                    observer_id=str(self.observer_id))


@dataclass(frozen=True)
class NativeSuccessorReceipt:
    binding: StateBinding
    operation_id: str
    parent_session_id: str
    session_id: str
    epoch: int
    started_at: datetime
    expires_at: datetime
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class NativeSuccessorView:
    receipt: NativeSuccessorReceipt
    state: str
    reason_code: str
    observed_at: datetime
    trading_permission: bool = field(default=False, init=False)


def _owner(row):
    if row['owner_process'] is None:
        return None
    return OwnerToken(UUID(row['owner_process']), UUID(row['owner_generation']), row['owner_epoch'], UUID(row['current_session']),
                      datetime.fromisoformat(row['owner_since']), datetime.fromisoformat(row['owner_until']))


class PgControlRepository:
    def __init__(self, connection, financial, *, verifier):
        import asyncpg
        if not isinstance(connection, asyncpg.Connection) or type(financial) is not PgSpotRepository or type(verifier) is not OperatorVerifier:
            raise StorageError('PG_CONTROL_CONTEXT_INVALID')
        self.connection, self.financial, self.verifier = connection, financial, verifier
        self.runtime, self.binding = financial._runtime, financial.binding
        self._context = (connection, financial, verifier, self.runtime, self.binding)
        self._mutex, self._tasks = asyncio.Lock(), set()

    def _original(self):
        if (self._context != (self.connection, self.financial, self.verifier, self.runtime, self.binding)
                or self.financial._runtime is not self.runtime or self.financial.binding != self.binding or self.runtime._closing):
            raise StorageError('PG_CONTROL_CONTEXT_CHANGED')

    async def _primary(self):
        self._original()
        c, cfg = self.connection, self.runtime.config
        if c.is_closed() or c.is_in_transaction():
            raise StorageError('PG_CONTROL_CONNECTION_INVALID')
        row = await c.fetchrow("""SELECT current_user::text AS principal,
            (SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname='life_financial') AS owner,
            (SELECT lineage FROM life_storage.storage_metadata WHERE singleton) AS lineage,
            current_setting('synchronous_commit') AS sync,current_setting('fsync') AS fsync,
            current_setting('full_page_writes') AS writes,pg_is_in_recovery() AS replica""")
        if (row['principal'] != row['owner'] or row['lineage'] != cfg.database_lineage or row['replica']
                or any(row[k] != 'on' for k in ('sync', 'fsync', 'writes'))):
            raise StorageError('PG_CONTROL_OWNER_REQUIRED')

    @staticmethod
    def _receipt(binding, value, *, replayed):
        if value.get('kind') == 'EXECUTION_MODE' and value.get('trading_permission') is False:
            return NativeExecutionModeReceipt(binding, value['operation_id'], value['mode'], UUID(value['observer_id']), replayed)
        if value.get('kind') == 'SESSION_SUCCESSOR' and value.get('trading_permission') is False:
            return NativeSuccessorReceipt(binding, value['operation_id'], value['parent_session_id'], value['session_id'],
                                          value['epoch'], datetime.fromisoformat(value['started_at']),
                                          datetime.fromisoformat(value['expires_at']), replayed)
        if value.get('kind') != 'NATIVE_CONTROL' or value.get('trading_permission') is not False:
            raise StorageUnavailable('PG_CONTROL_RECEIPT_INVALID')
        return NativeControlReceipt(binding, value['operation_id'], OwnerMode(value['mode']),
                                    None if value['token'] is None else OwnerToken.from_json(value['token']), replayed)

    async def _history(self):
        c, cfg = self.connection, self.runtime.config
        p = await c.fetchrow('SELECT * FROM life_financial.control_policies WHERE account_id=$1 AND policy_hash=$2', cfg.account_id, self.binding.policy_hash)
        if p is None:
            raise StorageError('PG_CONTROL_POLICY_REQUIRED')
        n, size = await c.fetchrow('SELECT count(*),COALESCE(sum(octet_length(body::text)),0) FROM life_financial.control_records WHERE account_id=$1', cfg.account_id)
        if n > p['max_records'] or size > p['max_total_bytes']:
            raise StorageUnavailable('PG_CONTROL_BOUND')
        if not await c.fetchval('SELECT life_financial.control_verified($1)', cfg.account_id):
            raise StorageUnavailable('PG_CONTROL_UNVERIFIED')
        key_policy = await c.fetchval("SELECT body->'operator_verifier' FROM life_financial.configurations WHERE account_id=$1 AND config_hash=$2 AND policy_hash=$3",
                                      cfg.account_id, self.binding.config_hash, self.binding.policy_hash)
        if key_policy is None or json.loads(key_policy) != self.verifier.policy():
            raise StorageError('PG_CONTROL_KEY_POLICY_MISMATCH')
        rows = await c.fetch('SELECT operation_id,body FROM life_financial.control_records WHERE account_id=$1 ORDER BY sequence', cfg.account_id)
        result = {}
        for row in rows:
            record = json.loads(row['body'])
            if record['verifier_policy'] != self.verifier.policy():
                raise StorageUnavailable('PG_CONTROL_UNVERIFIED')
            self.verifier.audit(record, self.binding)
            result[row['operation_id']] = record
        return p, result, size

    async def lookup(self, operation_id):
        identifier(operation_id)
        if self._mutex.locked():
            raise StorageUnavailable('PG_CONTROL_BACKPRESSURE')
        async with self._mutex, asyncio.timeout(self.runtime.config.transaction_timeout_ms / 1000):
            await self._primary()
            _, records, _ = await self._history()
            record = records.get(operation_id)
            return None if record is None else self._receipt(self.binding, record['receipt'], replayed=True)

    async def _commit(self, transaction):
        await transaction.commit()

    async def transition_owner(self, command, *, approval, facts, cut):
        if type(command) is not OwnerTransition:
            raise StorageError('PG_CONTROL_COMMAND_INVALID')
        return await self._submit(command, approval=approval, facts=facts, cut=cut)

    async def set_execution_mode(self, command, *, approval, facts, cut):
        if type(command) is not NativeExecutionMode:
            raise StorageError('PG_EXECUTION_MODE_COMMAND_INVALID')
        return await self._submit(command, approval=approval, facts=facts, cut=cut)

    async def reconcile_successor(self, command, *, approval, facts, cut):
        if type(command) is not ReconcileSessionSuccessor:
            raise StorageError('PG_SUCCESSOR_COMMAND_INVALID')
        return await self._submit(command, approval=approval, facts=facts, cut=cut)

    async def _submit(self, command, *, approval, facts, cut):
        self._original()
        if (type(command) not in (OwnerTransition, ReconcileSessionSuccessor, NativeExecutionMode) or type(approval) is not OperatorApproval
                or type(facts) is not SpotFinancialRecoveryFacts or type(cut) is not FinancialCut
                or facts.binding != self.binding or cut.checkpoint.binding != self.binding):
            raise StorageError('PG_CONTROL_COMMAND_INVALID')
        if self._mutex.locked() or self._tasks:
            raise StorageUnavailable('PG_CONTROL_BACKPRESSURE')
        # No signed control result or historical receipt may restore process-local
        # send readiness. Capture invalidation occurs before the first await.
        if type(command) is not NativeExecutionMode:
            self.runtime._healthy = False
        self.runtime._ingress_epoch += 1
        task = asyncio.create_task(self._apply(command, approval, facts, cut))
        self._tasks.add(task)

        def complete(done):
            self._tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(complete)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            self.runtime._healthy = False
            raise

    async def _apply(self, command, approval, facts, cut):
        import asyncpg
        c, cfg = self.connection, self.runtime.config
        async with self._mutex:
            transaction, committing = None, False
            try:
                async with asyncio.timeout(cfg.transaction_timeout_ms / 1000):
                    await self._primary()
                    transaction = c.transaction(isolation='read_committed')
                    await transaction.start()
                    for name, value in (('statement_timeout', cfg.statement_timeout_ms), ('lock_timeout', cfg.statement_timeout_ms),
                                        ('idle_in_transaction_session_timeout', cfg.idle_transaction_timeout_ms)):
                        await c.execute('SELECT set_config($1,$2,true)', name, str(value))
                    before = json.loads(await c.fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a WHERE account_id=$1 FOR UPDATE', cfg.account_id))
                    policy, records, _ = await self._history()
                    expected = dict(payload=command.json(), approval=approval.body(), signature=approval.signature,
                                    cut=cut.json(), facts=facts.json(), verifier_policy=self.verifier.policy())
                    if command.operation_id in records:
                        prior = records[command.operation_id]
                        if any(prior[k] != v for k, v in expected.items()):
                            raise StorageError('PG_CONTROL_OPERATION_CONFLICT')
                        await transaction.rollback()
                        return self._receipt(self.binding, prior['receipt'], replayed=True)
                    current = json.loads(await c.fetchval('SELECT life_financial.spot_state($1)', cfg.account_id))
                    version, digest = self.financial._cut(cut, command.owner if type(command) is ReconcileSessionSuccessor else command.token or _owner(before))
                    if (version, digest) != (before['version'], current['digest']):
                        raise StorageError('PG_CONTROL_CUT_STALE')
                    now = await c.fetchval('SELECT clock_timestamp()')
                    if not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= policy['max_fact_age_ms']:
                        raise StorageError('PG_CONTROL_FACTS_UNQUALIFIED')
                    self.verifier.verify(approval, self.binding, command.role, command.json(), cut, facts, now)
                    grant_id = None
                    successor = None
                    execution = None
                    if type(command) is NativeExecutionMode:
                        execution = await self._execution(command, before, policy, facts, now)
                    elif type(command) is ReconcileSessionSuccessor:
                        successor = await self._successor(command, before, current, policy, facts, now)
                    else:
                        token = command.token
                        grant_id = None
                        if command.mode is OwnerMode.ACTIVE:
                            session = current['session']
                            if str(token.session_id) != session['session_id']:
                                session = await self._target_successor(token, before)
                            if (before['halted'] or token.generation == _owner(before).generation
                                    or not token.valid_from <= now < token.expires_at <= datetime.fromisoformat(session['expires_at'])
                                    or str(token.session_id) != session['session_id'] or token.epoch != session['epoch']
                                    or not await c.fetchval('SELECT life_financial.control_facts_qualified($1,$2::jsonb)', cfg.account_id, json.dumps(facts.json()))):
                                raise StorageError('PG_CONTROL_ACTIVATION_UNQUALIFIED')
                            for previous in records.values():
                                if str(token.generation) in (previous['before_account']['owner_generation'], previous['after_account']['owner_generation']):
                                    raise StorageError('PG_CONTROL_GENERATION_CONSUMED')
                            if str(token.session_id) == before['current_session']:
                                await c.execute('SELECT life_financial.guard_spot_session($1)', cfg.account_id)
                            grant_id = uuid5(cfg.account_id, command.operation_id)
                            await c.execute('INSERT INTO life_financial.activation_grants VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)',
                                            cfg.account_id, grant_id, token.process_id, token.generation, token.epoch, token.session_id,
                                            token.valid_from, token.expires_at, bytes.fromhex(approval.signature))
                            await c.execute('INSERT INTO life_financial.grant_consumptions VALUES($1,$2,$3)', cfg.account_id, grant_id, command.operation_id)
                        elif token is not None and token != _owner(before):
                            raise StorageError('PG_CONTROL_OWNER_MISMATCH')
                        # Logical UNARMED keeps the old observation writer in native
                        # RECOVERY_ONLY. Clearing it would orphan late ACK/fill facts.
                        actual_mode = 'RECOVERY_ONLY' if command.mode is OwnerMode.UNARMED else command.mode.value
                        token = _owner(before) if token is None else token
                        await c.execute('''UPDATE life_financial.accounts SET mode=$2,owner_process=$3,owner_generation=$4,
                            owner_epoch=$5,current_session=$6,owner_since=$7,owner_until=$8,version=version+1 WHERE account_id=$1''',
                                        cfg.account_id, actual_mode, token.process_id, token.generation, token.epoch,
                                        token.session_id, token.valid_from, token.expires_at)
                    after = json.loads(await c.fetchval('SELECT to_jsonb(a) FROM life_financial.accounts a WHERE account_id=$1', cfg.account_id))
                    if execution is not None:
                        receipt = dict(kind='EXECUTION_MODE', operation_id=command.operation_id, mode=command.mode, observer_id=str(command.observer_id),
                                       version=after['version'], replayed=False, trading_permission=False)
                    elif successor is not None:
                        receipt = {**successor, 'version': after['version'], 'replayed': False, 'trading_permission': False}
                    else:
                        receipt = dict(kind='NATIVE_CONTROL', operation_id=command.operation_id, mode=command.mode.value,
                                       token=None if command.token is None else command.token.json(), version=after['version'],
                                       replayed=False, trading_permission=False)
                    record = {**expected, 'before_account': before, 'after_account': after, 'receipt': receipt,
                              'grant_id': None if grant_id is None else str(grant_id)}
                    body = json.dumps(record, sort_keys=True, separators=(',', ':'))
                    if len(records) >= policy['max_records']:
                        raise StorageError('PG_CONTROL_BOUND')
                    digest = await c.fetchval("SELECT encode(sha256(convert_to(($1::jsonb)::text,'UTF8')),'hex')", body)
                    await c.execute('INSERT INTO life_financial.control_records(account_id,operation_id,body,source_hash,applied_at) VALUES($1,$2,$3::jsonb,$4,$5)',
                                    cfg.account_id, command.operation_id, body, digest, now)
                    if await c.fetchval('SELECT COALESCE(sum(octet_length(body::text)),0) FROM life_financial.control_records WHERE account_id=$1', cfg.account_id) > policy['max_total_bytes']:
                        raise StorageError('PG_CONTROL_BOUND')
                    await c.execute('INSERT INTO life_financial.operations VALUES($1,$2,$3,$4::jsonb,$5::jsonb,$6,$7)', cfg.account_id,
                                    command.operation_id, fingerprint(expected), json.dumps(dict(kind='NATIVE_CONTROL', record_hash=digest)),
                                    json.dumps(receipt), after['version'], now)
                    await c.execute('SELECT life_financial.control_commit_check($1,$2)', cfg.account_id, command.operation_id)
                    committing = True
                    await self._commit(transaction)
                    self._original()
                    return self._receipt(self.binding, receipt, replayed=False)
            except BaseException as error:
                if type(command) is NativeExecutionMode:
                    self.runtime._healthy = False
                try:
                    if transaction is not None and not c.is_closed() and c.is_in_transaction():
                        await transaction.rollback()
                except Exception:
                    pass
                if isinstance(error, asyncpg.PostgresError) and error.sqlstate and (
                        error.sqlstate.startswith('23') or error.sqlstate in ('P0001', '55000', '22023', '22P02')):
                    raise sql_error(error) from None
                if committing:
                    raise CommitUnknown('PG_CONTROL_COMMIT_UNKNOWN') from None
                if isinstance(error, (StorageError, asyncio.CancelledError)):
                    raise
                raise StorageUnavailable('PG_CONTROL_ABORTED') from None

    async def _execution(self, command, before, policy, facts, now):
        c, cfg = self.connection, self.runtime.config
        p = await c.fetchval('SELECT body FROM life_financial.execution_policies WHERE account_id=$1 AND policy_digest=$2', cfg.account_id, command.policy_digest)
        if (p is None or command.observer_id != self.runtime._risk_observer
                or before['mode'] != 'ACTIVE' or not await c.fetchval('SELECT life_financial.execution_policy_qualified($1)', cfg.account_id)
                or not await c.fetchval('SELECT life_financial.execution_facts_qualified($1,$2::jsonb)', cfg.account_id, json.dumps(facts.json()))):
            raise StorageError('PG_EXECUTION_MODE_QUIESCENCE_REQUIRED')
        p = json.loads(p)
        if (command.campaign_id != p['campaign_id'] or command.mode != 'MM' and type(p.get(command.mode.lower())) is not dict):
            raise StorageError('PG_EXECUTION_MODE_DISABLED')
        await c.execute('SELECT life_financial.guard_account($1,$2::jsonb,$3::jsonb,$4)', cfg.account_id, json.dumps(self.binding.json()), json.dumps(_owner(before).json()), before['version'])
        await c.execute('UPDATE life_financial.accounts SET version=version+1 WHERE account_id=$1', cfg.account_id)
        return True

    async def _target_successor(self, token, before):
        c, account = self.connection, self.runtime.config.account_id
        row = await c.fetchrow('''SELECT to_jsonb(s) AS session,g.*,pg.rollback_latched AS parent_rollback,pg.last_seen_at AS parent_last_seen,
            life_financial.control_successor_verified(x.account_id,x.operation_id) AS verified
            FROM life_financial.session_successors x JOIN life_financial.sessions s USING(account_id,session_id)
            JOIN life_financial.spot_session_progress g USING(account_id,session_id)
            JOIN life_financial.spot_session_progress pg ON pg.account_id=x.account_id AND pg.session_id=x.parent_session
            WHERE x.account_id=$1 AND x.parent_session=$2 AND x.session_id=$3''', account, UUID(before['current_session']), token.session_id)
        now = await c.fetchval('SELECT clock_timestamp()')
        if (row is None or row['verified'] is not True or row['state'] != 'ACTIVE' or row['rollback_latched']
                or now < row['last_seen_at'] or row['parent_rollback'] or now < row['parent_last_seen']):
            raise StorageError('PG_SUCCESSOR_ACTIVATION_UNQUALIFIED')
        return json.loads(row['session'])

    async def _successor(self, command, before, current, policy, facts, now):
        c, cfg = self.connection, self.runtime.config
        parent, progress = current['session'], current['session_lifecycle']
        parent_policy = current['session_policy']
        expires = datetime.fromisoformat(parent['expires_at'])
        last_seen = datetime.fromisoformat(progress['last_seen_at'])
        if (command.owner != _owner(before) or before['halted'] or progress['rollback_latched']
                or progress['state'] != 'TRANSITIONING' or now < expires or now < last_seen
                or parent_policy['on_expiry'] != 'switch_to_market_reference'
                or not last_seen <= command.reference_observed_at <= now
                or (now - command.reference_observed_at).total_seconds() * 1000 > min(policy['max_fact_age_ms'], parent_policy['max_reconciliation_age_ms'])
                or facts.observed_at < last_seen
                or not await c.fetchval('SELECT life_financial.control_facts_qualified($1,$2::jsonb)', cfg.account_id, json.dumps(facts.json()))):
            raise StorageError('PG_SUCCESSOR_SOURCE_UNQUALIFIED')
        if await c.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.session_successors WHERE account_id=$1 AND parent_session=$2)', cfg.account_id, command.owner.session_id):
            raise StorageError('PG_SUCCESSOR_ALREADY_RECONCILED')
        child = uuid5(NAMESPACE_URL, parent['session_id'] + ':market-successor')
        deadline = expires + timedelta(milliseconds=policy['successor_duration_ms'])
        await c.execute('''INSERT INTO life_financial.sessions VALUES($1,$2,$3,$4,$5,$6,'market',$7,$8::jsonb)''',
                        cfg.account_id, child, parent['epoch'] + 1, parent['config_version'], expires, deadline,
                        parent['model_version'], json.dumps({'LIFE-USDT': str(command.market_anchor_usdt)}))
        state = 'ACTIVE' if now < deadline else 'PAUSED'
        await c.execute('INSERT INTO life_financial.spot_session_policies VALUES($1,$2,$3,$4,$5,$6,$7)', cfg.account_id, child,
                        self.binding.policy_hash, 'pause_quotes', parent_policy['max_reconciliation_age_ms'], now, state)
        await c.execute('INSERT INTO life_financial.spot_session_progress VALUES($1,$2,$3,$4,$5,false,0)', cfg.account_id, child, state,
                        'SESSION_ACTIVE' if state == 'ACTIVE' else 'SESSION_GATE_UNAVAILABLE', now)
        await c.execute('INSERT INTO life_financial.session_successors VALUES($1,$2,$3,$4,$5)', cfg.account_id, command.owner.session_id,
                        child, command.operation_id, command.observer_id)
        await c.execute("UPDATE life_financial.accounts SET mode='RECOVERY_ONLY',version=version+1 WHERE account_id=$1", cfg.account_id)
        return dict(kind='SESSION_SUCCESSOR', operation_id=command.operation_id, parent_session_id=parent['session_id'], session_id=str(child),
                    epoch=parent['epoch'] + 1, started_at=expires.isoformat(), expires_at=deadline.isoformat())

    async def successor(self):
        """Fresh read-only status; original receipt/deadline never grants authority."""
        if self._mutex.locked():
            raise StorageUnavailable('PG_CONTROL_BACKPRESSURE')
        async with self._mutex, asyncio.timeout(self.runtime.config.transaction_timeout_ms / 1000):
            await self._primary()
            _, records, _ = await self._history()
            row = await self.connection.fetchrow('''SELECT x.operation_id,s.expires_at,c.state,c.reason_code,c.rollback_latched,c.last_seen_at,
                p.rollback_latched AS parent_rollback,p.last_seen_at AS parent_last_seen,a.current_session,x.session_id,
                clock_timestamp() AS observed_at FROM life_financial.session_successors x
                JOIN life_financial.sessions s USING(account_id,session_id)
                JOIN life_financial.spot_session_progress c USING(account_id,session_id)
                JOIN life_financial.spot_session_progress p ON p.account_id=x.account_id AND p.session_id=x.parent_session
                JOIN life_financial.accounts a ON a.account_id=x.account_id WHERE x.account_id=$1''', self.runtime.config.account_id)
            if row is None:
                return None
            receipt = self._receipt(self.binding, records[row['operation_id']]['receipt'], replayed=True)
            now = row['observed_at']
            if row['rollback_latched'] or row['parent_rollback'] or now < max(row['last_seen_at'], row['parent_last_seen']):
                state, reason = 'PAUSED', 'CLOCK_ROLLBACK'
            elif now >= row['expires_at']:
                state, reason = 'EXPIRED', 'SUCCESSOR_DEADLINE_REACHED'
            elif row['current_session'] != row['session_id']:
                state, reason = 'PAUSED', 'MANUAL_ACTIVATION_REQUIRED'
            else:
                state, reason = row['state'], row['reason_code']
            return NativeSuccessorView(receipt, state, reason, now)

    async def observe_successor(self, operation_id, *, owner):
        """Clock-only observation of the still-current native session.

        Before ACTIVATE the parent remains current and only its observation
        writer may checkpoint time. No gate/reference assertions are made and
        the reconciled child's original clock/deadline is never rewritten.
        """
        identifier(operation_id)
        if type(owner) is not OwnerToken or self._mutex.locked():
            raise StorageError('PG_SUCCESSOR_OBSERVATION_UNQUALIFIED')
        async with self._mutex, asyncio.timeout(self.runtime.config.transaction_timeout_ms / 1000):
            await self._primary()
            await self._history()
            row = await self.connection.fetchrow('''SELECT to_jsonb(a) AS account,p.max_reconciliation_age_ms FROM life_financial.accounts a
                JOIN life_financial.spot_session_policies p ON p.account_id=a.account_id AND p.session_id=a.current_session
                WHERE a.account_id=$1 AND EXISTS(SELECT 1 FROM life_financial.session_successors x WHERE x.account_id=a.account_id)''', self.runtime.config.account_id)
            if row is None or _owner(json.loads(row['account'])) != owner:
                raise StorageError('PG_SUCCESSOR_OBSERVATION_UNQUALIFIED')
            command = SessionTick(operation_id, str(owner.session_id), owner.epoch, False, False, False, row['max_reconciliation_age_ms'])
            cut = (await self.financial.snapshot(self.binding)).cut
            return await self.financial.tick_session(command, cut=cut, owner=owner)

    async def close(self):
        if self._tasks:
            async with asyncio.timeout(self.runtime.config.shutdown_timeout_ms / 1000):
                await asyncio.shield(asyncio.gather(*tuple(self._tasks), return_exceptions=True))
