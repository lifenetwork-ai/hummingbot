"""Bounded primary-only PG2 infrastructure; no trading backend or send grant.

The allocation primitive proves row serialization/idempotent COMMIT behavior.
PG3 must implement domain repositories; selection never falls back to files.
No connection/session escapes this adapter. Caller cancellation retains physical
work and its capacity until completion; unknown commits require primary readback.
"""
import asyncio
import json
import re
from collections import deque
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    OwnerToken,
    StateBinding,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.access import MUTATING_ENTRYPOINTS, role_name
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace, _Trace, failure, phase
from hummingbot.strategy_v2.life_liquidity.storage.postgres.migrations import _ssl_context, load_migrations


@dataclass(frozen=True)
class PgConfig:
    dsn: str = field(repr=False)
    principal: str
    account_id: UUID
    database_lineage: UUID
    binding: StateBinding
    server_major: int
    min_size: int
    max_size: int
    max_pending: int
    safety_size: int
    safety_max_pending: int
    acquire_timeout_ms: int
    statement_timeout_ms: int
    transaction_timeout_ms: int
    idle_transaction_timeout_ms: int
    shutdown_timeout_ms: int
    retry_count: int
    retry_base_ms: int
    local_insecure: bool = False
    ssl_ca_file: Path | None = None
    write: bool = True

    def __post_init__(self):
        role_name(self.principal)
        if (type(self.dsn) is not str or not self.dsn or type(self.binding) is not StateBinding or self.binding.schema_version != 1
                or any(type(v) is not UUID or v.int == 0 for v in (self.account_id, self.database_lineage))
                or type(self.write) is not bool or type(self.local_insecure) is not bool):
            raise StorageError('PG_CONFIG_INVALID')
        values = (self.server_major, self.min_size, self.max_size, self.max_pending, self.safety_size, self.safety_max_pending,
                  self.acquire_timeout_ms, self.statement_timeout_ms, self.transaction_timeout_ms, self.idle_transaction_timeout_ms,
                  self.shutdown_timeout_ms, self.retry_base_ms)
        if (any(type(v) is not int or v <= 0 for v in values) or not 1 <= self.min_size <= self.max_size <= 32
                or not self.max_size <= self.max_pending <= 1024 or not 1 <= self.safety_size <= 8
                or not self.safety_size <= self.safety_max_pending <= 64 or self.server_major != 16
                or max(values[6:]) > 60000 or type(self.retry_count) is not int or not 0 <= self.retry_count <= 5):
            raise StorageError('PG_CAPACITY_INVALID')
        _ssl_context(self.dsn, self.local_insecure, self.ssl_ca_file)


@dataclass(frozen=True)
class AllocateBalance:
    operation_id: str
    expected_version: int
    asset: str
    amount: Decimal

    def __post_init__(self):
        identifier(self.operation_id)
        if (type(self.expected_version) is not int or self.expected_version < 0 or self.asset not in ('LIFE', 'USDT')
                or type(self.amount) is not Decimal or not self.amount.is_finite() or not 0 < self.amount < Decimal('1e38')
                or -self.amount.as_tuple().exponent > 18):
            raise StorageError('PG_ALLOCATION_INVALID')

    def json(self):
        return {**self.__dict__, 'amount': str(self.amount)}


@dataclass(frozen=True)
class PgReceipt:
    account_id: UUID
    operation_id: str
    version: int
    asset: str
    amount: Decimal
    replayed: bool
    trading_permission: bool = False

    @classmethod
    def parse(cls, value):
        if value.get('trading_permission') is not False:
            raise StorageUnavailable('PG_RECEIPT_INVALID')
        return cls(UUID(value['account_id']), value['operation_id'], value['version'], value['asset'], Decimal(value['amount']), value['replayed'])


@dataclass(frozen=True)
class PgBalance:
    asset: str
    total: Decimal
    reserved: Decimal
    liability: Decimal
    version: int


def sql_error(error):
    # Known static server codes only; never expose SQL, values or DSNs.
    message = getattr(error, 'message', '')
    if re.fullmatch(r'LIFE_[A-Z_]+', message):
        code = message.replace('LIFE_', 'PG_', 1)
        return StorageConflict(code) if 'CONFLICT' in code else StorageError(code)
    return StorageError('PG_CONSTRAINT_REJECTED')


class PgRuntime:
    def __init__(self, config):
        self.config = config
        self._pools = {}
        self._tasks = set()
        self._counts = {False: 0, True: 0}
        self._closing = False
        self._healthy = False
        self._ingress_epoch = 0
        self._observation_inflight = 0
        self._close_task = None
        self._bundle = ()
        self._diagnostics = deque(maxlen=128)

    @property
    def diagnostics(self):
        """Recent physical admissions only; diagnostic readback grants nothing.

        Immutable samples and bounded retention; no I/O or user callback runs on
        the command path. Exporters can poll outside the financial boundary.
        """
        return tuple(self._diagnostics)

    @property
    def healthy(self):
        return self._healthy and not self._closing and self._observation_inflight == 0

    @classmethod
    async def open(cls, config):
        if type(config) is not PgConfig:
            raise StorageError('PG_CONFIG_REQUIRED')
        import asyncpg
        self = cls(config)
        try:
            async with asyncio.timeout(config.transaction_timeout_ms / 1000):
                self._bundle = await asyncio.to_thread(load_migrations, Path(__file__).resolve().parents[5] / 'migrations' / 'life_liquidity')
                context = await asyncio.to_thread(_ssl_context, config.dsn, config.local_insecure, config.ssl_ca_file)
                for safety, size, minimum in ((False, config.max_size, config.min_size), (True, config.safety_size, 1)):
                    self._pools[safety] = await asyncpg.create_pool(
                        config.dsn, ssl=context,
                        min_size=minimum, max_size=size, max_inactive_connection_lifetime=30,
                        timeout=config.acquire_timeout_ms / 1000, command_timeout=config.statement_timeout_ms / 1000,
                        init=self._initialize_connection, server_settings={
                            'application_name': 'life_financial_runtime', 'synchronous_commit': 'on', 'timezone': 'UTC', 'search_path': 'pg_catalog',
                            'statement_timeout': str(config.statement_timeout_ms),
                            'idle_in_transaction_session_timeout': str(config.idle_transaction_timeout_ms)})
            self._healthy = True
            return self
        except BaseException:
            for pool in self._pools.values():
                pool.terminate()
            raise StorageUnavailable('PG_STARTUP_UNQUALIFIED') from None

    async def _initialize_connection(self, connection):
        await connection.set_type_codec('jsonb', schema='pg_catalog', encoder=lambda v: json.dumps(v, allow_nan=False), decoder=json.loads)
        await self._validate_connection(connection)

    async def _validate_connection(self, c):
        cfg = self.config
        # A single statement supplies one primary MVCC view. Every admission
        # rechecks it; successful prior checks are never a privilege cache.
        row = await c.fetchrow('''WITH history AS MATERIALIZED (
            SELECT version,name,checksum FROM life_migrations.schema_migrations ORDER BY version LIMIT $3)
            SELECT current_user AS principal,pg_catalog.pg_is_in_recovery() AS replica,
            pg_catalog.current_setting('server_version_num')::pg_catalog.int4 OPERATOR(pg_catalog./) 10000 AS major,
            pg_catalog.current_setting('transaction_read_only') AS readonly,pg_catalog.current_setting('synchronous_commit') AS sync,
            pg_catalog.current_setting('fsync') AS fsync,pg_catalog.current_setting('full_page_writes') AS full_page,
            pg_catalog.current_setting('TimeZone') AS timezone,pg_catalog.current_setting('search_path') AS search_path,
            (SELECT lineage FROM life_storage.storage_metadata) AS lineage,
            (SELECT ssl FROM pg_catalog.pg_stat_ssl WHERE pid OPERATOR(pg_catalog.=) pg_catalog.pg_backend_pid()) AS tls,
            (SELECT pg_catalog.to_jsonb(s) FROM life_financial.schema_contract s) AS contract,
            (SELECT COALESCE(pg_catalog.jsonb_agg(CASE WHEN pg_catalog.octet_length(name) OPERATOR(pg_catalog.<=) 512 AND pg_catalog.octet_length(checksum) OPERATOR(pg_catalog.=) 64
                THEN pg_catalog.jsonb_build_array(version,name,checksum) ELSE 'null'::pg_catalog.jsonb END ORDER BY version),'[]'::pg_catalog.jsonb)
                FROM history) AS history,
            (SELECT pg_catalog.to_jsonb(a) FROM life_financial.accounts a WHERE account_id OPERATOR(pg_catalog.=) $1) AS account,
            life_financial.can_access($1,$2) AS access,
            (SELECT rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb OR rolreplication
                FROM pg_catalog.pg_roles WHERE rolname OPERATOR(pg_catalog.=) current_user) AS unsafe,
            EXISTS(SELECT 1 FROM pg_catalog.pg_tables WHERE schemaname OPERATOR(pg_catalog.=) 'life_financial'
                AND pg_catalog.has_table_privilege(current_user,pg_catalog.quote_ident(schemaname) OPERATOR(pg_catalog.||) '.' OPERATOR(pg_catalog.||) pg_catalog.quote_ident(tablename),'INSERT,UPDATE,DELETE,TRUNCATE')) AS direct,
            pg_catalog.has_schema_privilege(current_user,'life_financial','CREATE') OR pg_catalog.has_database_privilege(current_user,pg_catalog.current_database(),'CREATE') AS schema_create,
            (SELECT pg_catalog.bool_and(pg_catalog.to_regprocedure(signature) IS NOT NULL
                AND pg_catalog.has_function_privilege(current_user,pg_catalog.to_regprocedure(signature),'EXECUTE') OPERATOR(pg_catalog.=) $2)
            FROM pg_catalog.unnest($4::pg_catalog.text[]) AS signatures(signature)) AS profile''', cfg.account_id, cfg.write, len(self._bundle) + 1, list(MUTATING_ENTRYPOINTS))
        if (row['principal'] != cfg.principal or row['replica'] or row['major'] != cfg.server_major
                or row['readonly'] != 'off' or row['sync'] != 'on' or row['fsync'] != 'on' or row['full_page'] != 'on'
                or row['timezone'] != 'UTC' or row['search_path'] != 'pg_catalog' or row['lineage'] != cfg.database_lineage):
            raise StorageUnavailable('PG_PRIMARY_CONTEXT_INVALID')
        if not cfg.local_insecure and row['tls'] is not True:
            raise StorageUnavailable('PG_TLS_REQUIRED')
        contract = row['contract']
        if contract is None or contract['format_version'] != 1 or contract['decimal_scale'] != 18:
            raise StorageUnavailable('PG_SCHEMA_INCOMPATIBLE')
        if row['history'] != [[m.version, m.name, m.checksum] for m in self._bundle]:
            raise StorageUnavailable('PG_MIGRATION_BUNDLE_INCOMPATIBLE')
        account = row['account']
        if (account is None or account['database_lineage'] != str(cfg.database_lineage)
                or any(account[key] != value for key, value in cfg.binding.json().items()) or row['access'] is not True):
            raise StorageUnavailable('PG_ACCOUNT_BINDING_INVALID')
        if any(row[key] is not False for key in ('unsafe', 'direct', 'schema_create')):
            raise StorageUnavailable('PG_RUNTIME_PRIVILEGES_UNSAFE')
        if row['profile'] is not True:
            raise StorageUnavailable('PG_ROLE_PROFILE_INVALID')

    async def _admit(self, callback, *, safety, commit_state=None, operation='read'):
        cfg = self.config
        trace = _Trace(operation, safety)
        if self._closing or not safety and not self.healthy:
            self._diagnostics.append(trace.finish('unqualified'))
            raise StorageUnavailable('PG_CLOSED_OR_UNQUALIFIED')
        maximum = cfg.safety_max_pending if safety else cfg.max_pending
        if self._counts[safety] >= maximum:
            self._diagnostics.append(trace.finish('backpressure'))
            raise StorageUnavailable('PG_BACKPRESSURE')
        self._counts[safety] += 1

        async def physical():
            token = _current_trace.set(trace)
            outcome = 'error'
            completed = False
            result = None
            deadline = asyncio.timeout(cfg.transaction_timeout_ms / 1000)
            try:
                async with deadline:
                    phase('acquire')
                    async with self._pools[safety].acquire(timeout=cfg.acquire_timeout_ms / 1000) as c:
                        try:
                            trace.backend_pid = c.get_server_pid()
                            phase('validate')
                            await self._validate_connection(c)
                            if not safety and self._observation_inflight:
                                raise StorageError('PG_OBSERVATION_IN_PROGRESS')
                            phase('execute')
                            result = await callback(c)
                            completed = True
                        except BaseException as error:
                            if not trace.pending_failure:
                                failure(error)
                            raise
                        finally:
                            phase('release')
                    outcome = 'success'
                    return result
            except StorageUnavailable as error:
                if isinstance(error, CommitUnknown):
                    outcome = 'commit_unknown'
                if trace.error_phase is None:
                    failure(error)
                self._healthy = False
                raise
            except StorageError as error:
                if trace.error_phase is None:
                    failure(error)
                raise
            except Exception as error:
                if completed or trace.error_phase is None:
                    failure(error)
                elif isinstance(error, TimeoutError) and trace.error_kind == 'cancelled':
                    trace.error_kind = 'timeout'  # The outer physical deadline.
                self._healthy = False
                # Pool reset/release may itself fail after a lost COMMIT reply.
                # Cleanup must not downgrade uncertainty into a generic error.
                if completed:
                    outcome = 'confirmed_after_release_error'
                    return result  # The database command already confirmed.
                if commit_state is not None and commit_state['attempted']:
                    outcome = 'commit_unknown'
                    raise CommitUnknown('PG_COMMIT_UNKNOWN') from None
                raise StorageUnavailable('PG_DATABASE_UNAVAILABLE') from None
            finally:
                if deadline.expired() and trace.error_kind == 'cancelled':
                    trace.error_kind = 'timeout'
                self._counts[safety] -= 1
                self._diagnostics.append(trace.finish(outcome))
                _current_trace.reset(token)
        task = asyncio.create_task(physical())
        self._tasks.add(task)

        def complete(result):
            self._tasks.discard(result)
            if not result.cancelled():
                result.exception()
        task.add_done_callback(complete)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            trace.caller_cancelled = True
            self._healthy = False
            raise

    async def _commit(self, transaction):
        # A named physical boundary permits deterministic lost-reply tests.
        await transaction.commit()

    async def allocate(self, command, owner):
        if type(command) is not AllocateBalance or type(owner) is not OwnerToken or not self.config.write:
            raise StorageError('PG_ALLOCATION_CONTEXT_INVALID')
        cfg = self.config
        identity = fingerprint({'account_id': str(cfg.account_id), 'binding': cfg.binding.json(), 'owner': owner.json(), 'command': command.json()})
        return await self._transaction('SELECT life_financial.allocate_balance($1,$2,$3,$4,$5,$6,$7,$8)',
                                       (cfg.account_id, cfg.binding.json(), owner.json(), command.expected_version,
                                        command.operation_id, identity, command.asset, command.amount), owner, PgReceipt.parse)

    async def _transaction(self, query, args, owner, parse, *, observation=False, untrusted_ingress=False):
        """Internal fixed-command boundary shared by typed repository adapters."""
        import asyncpg
        if untrusted_ingress and (not observation or query not in (
                'SELECT life_financial.spot_capture_ack_ingress($1,$2)',
                'SELECT life_financial.swap_capture_ack($1,$2)',
                'SELECT life_financial.swap_mark_ack($1,$2)',
                'SELECT life_financial.spot_capture_financial_ingress($1,$2)',
                'SELECT life_financial.spot_capture_provider_page($1,$2)',
                'SELECT life_financial.spot_mark_financial_ingress($1,$2,$3)',
                'SELECT life_financial.spot_mark_ack_ingress($1,$2)')):
            raise StorageError('PG_UNTRUSTED_INGRESS_QUERY_DENIED')
        cfg = self.config
        boundary = {'attempted': False}

        async def execute(c):
            async def rollback(transaction):
                try:
                    if not c.is_closed() and c.is_in_transaction():
                        await transaction.rollback()
                except Exception:
                    self._healthy = False
                    c.terminate()
            for attempt in range(cfg.retry_count + 1):
                boundary['attempted'] = False
                transaction = c.transaction(isolation='read_committed')
                committing = False
                try:
                    phase('begin')
                    await transaction.start()
                    await c.execute("SELECT set_config('lock_timeout',$1,true)", str(cfg.statement_timeout_ms))
                    phase('execute')
                    receipt = await c.fetchval(query, *args)
                    # Replay has no effect and is allowed after owner expiry.
                    if not receipt['replayed'] and not untrusted_ingress:
                        phase('guard')
                        guard = 'guard_spot_observation' if observation else 'guard_account'
                        await c.execute(f'SELECT life_financial.{guard}($1,$2,$3,$4)', cfg.account_id, cfg.binding.json(), owner.json(), receipt['version'])
                    if not observation and self._observation_inflight:
                        raise StorageError('PG_OBSERVATION_IN_PROGRESS')
                    committing = True
                    boundary['attempted'] = True
                    trace = _current_trace.get()
                    trace.commit_attempted = True
                    phase('commit')
                    await self._commit(transaction)
                    trace.commit_confirmed = True
                    phase('parse')
                    return parse(receipt)
                except (asyncpg.SerializationError, asyncpg.DeadlockDetectedError) as error:
                    failure(error)
                    boundary['attempted'] = False  # PostgreSQL explicitly aborted.
                    phase('rollback')
                    await rollback(transaction)
                    if attempt >= cfg.retry_count:
                        raise StorageConflict('PG_TRANSACTION_RETRY_EXHAUSTED') from None
                    # Only a handled retry allows a later failure to supersede
                    # this one. Cleanup failures must preserve the raw cause.
                    _current_trace.get().pending_failure = False
                    phase('retry_wait')
                    await asyncio.sleep(cfg.retry_base_ms / 1000 * (2**attempt))
                except asyncpg.PostgresError as error:
                    failure(error)
                    phase('rollback')
                    await rollback(transaction)
                    if error.sqlstate and (error.sqlstate.startswith('23') or error.sqlstate in ('P0001', '55000', '22023', '22P02')):
                        boundary['attempted'] = False
                        raise sql_error(error) from None
                    self._healthy = False
                    # Shutdown and transport failures can lose a committed reply;
                    # only the definite rejection classes above prove rollback.
                    if committing:
                        raise CommitUnknown('PG_COMMIT_UNKNOWN') from None
                    raise StorageUnavailable('PG_TRANSACTION_ABORTED') from None
                except BaseException as error:
                    failure(error)
                    self._healthy = False
                    phase('rollback')
                    await rollback(transaction)
                    if committing:
                        raise CommitUnknown('PG_COMMIT_UNKNOWN') from None
                    raise
        family = re.fullmatch(r'SELECT life_financial\.([a-z][a-z0-9_]{0,79})\(\$[0-9]+(?:,\$[0-9]+)*\)', query)
        return await self._admit(execute, safety=observation, commit_state=boundary,
                                 operation=family[1] if family else 'transaction')

    async def operation(self, operation_id):
        identifier(operation_id)

        async def read(c):
            row = await c.fetchval('SELECT receipt FROM life_financial.operations WHERE account_id=$1 AND operation_id=$2', self.config.account_id, operation_id)
            return None if row is None else PgReceipt.parse({**row, 'replayed': True})
        return await self._admit(read, safety=True)

    async def balances(self):
        async def read(c):
            rows = await c.fetch('SELECT asset,total,reserved,liability,version FROM life_financial.balances WHERE account_id=$1 ORDER BY asset', self.config.account_id)
            return tuple(PgBalance(**dict(row)) for row in rows)
        return await self._admit(read, safety=True)

    async def close(self):
        self._closing = True
        self._healthy = False
        if self._close_task is None:
            async def drain():
                try:
                    async with asyncio.timeout(self.config.shutdown_timeout_ms / 1000):
                        if self._tasks:
                            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
                        await asyncio.gather(*(pool.close() for pool in self._pools.values()))
                except TimeoutError:
                    for pool in self._pools.values():
                        pool.terminate()
                    raise StorageUnavailable('PG_SHUTDOWN_READBACK_REQUIRED') from None
            self._close_task = asyncio.create_task(drain())
        await asyncio.shield(self._close_task)
