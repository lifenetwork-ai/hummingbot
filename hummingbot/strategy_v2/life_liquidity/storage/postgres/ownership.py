"""Opt-in managed process leases, not external sender fencing.

Only a privileged issuer can bind a private capability hash to an already
verified, signed native ACTIVATE grant. The raw capability is nondurable: process memory and a transient DB
session setting cleared on pool release. Successors require the separate signed
offline clean-handoff importer; no live/deployed profile is activated here.
"""
import asyncio
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    OwnerToken,
    StorageError,
    StorageUnavailable,
    digest,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace, failure, phase
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgConfig, PgRuntime, sql_error


@dataclass(frozen=True)
class ProcessRegistration:
    account_id: UUID
    database_lineage: UUID
    principal: str
    owner: OwnerToken
    capability_hash: str

    def __post_init__(self):
        if (any(not isinstance(v, UUID) or not v.int for v in (self.account_id, self.database_lineage))
                or type(self.owner) is not OwnerToken):
            raise StorageError('PG_PROCESS_LEASE_REGISTRATION_INVALID')
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.access import role_name
        role_name(self.principal)
        digest(self.capability_hash)


@dataclass(frozen=True)
class ProcessCapability:
    """Private memory only: never put this object/nonce in YAML, env or history."""
    _config: PgConfig = field(repr=False)
    _owner: OwnerToken
    _secret: str = field(repr=False)
    _pid: int = field(repr=False)

    @classmethod
    def create(cls, config, owner):
        if type(config) is not PgConfig or not config.write or type(owner) is not OwnerToken:
            raise StorageError('PG_PROCESS_CAPABILITY_CONTEXT_INVALID')
        return cls(config, owner, secrets.token_hex(32), os.getpid())

    def _validate(self, config):
        if (self._config != config or self._pid != os.getpid() or type(self._secret) is not str
                or len(self._secret) != 64 or any(c not in '0123456789abcdef' for c in self._secret)):
            raise StorageError('PG_PROCESS_CAPABILITY_CONTEXT_CHANGED')

    @property
    def registration(self):
        self._validate(self._config)
        return ProcessRegistration(self._config.account_id, self._config.database_lineage, self._config.principal, self._owner,
                                   hashlib.sha256(self._secret.encode('ascii')).hexdigest())

    def __reduce__(self):
        raise StorageError('PG_PROCESS_CAPABILITY_SERIALIZATION_DENIED')


@dataclass(frozen=True)
class ProcessLeaseReceipt:
    operation_id: str
    grant_id: UUID
    lease_epoch: int
    observed_at: datetime
    expires_at: datetime
    replayed: bool
    trading_permission: bool = field(default=False, init=False)

    @classmethod
    def parse(cls, value):
        if (type(value) is not dict or set(value) != {'operation_id', 'grant_id', 'lease_epoch', 'observed_at', 'expires_at', 'replayed', 'trading_permission'}
                or value['trading_permission'] is not False or type(value['replayed']) is not bool
                or type(value['lease_epoch']) is not int or value['lease_epoch'] <= 0):
            raise StorageUnavailable('PG_PROCESS_LEASE_RECEIPT_INVALID')
        return cls(value['operation_id'], UUID(value['grant_id']), value['lease_epoch'],
                   datetime.fromisoformat(value['observed_at']), datetime.fromisoformat(value['expires_at']), value['replayed'])


class PgLeaseGrantIssuer:
    """Owner-side binding only, using the original public operator verifier.

    This foundation is explicitly OFFLINE_SIMULATION and first-generation only.
    It does not prove exclusion of external keys or issue a successor grant.
    """

    def __init__(self, control):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
        if type(control) is not PgControlRepository:
            raise StorageError('PG_PROCESS_LEASE_ISSUER_INVALID')
        self.control = control

    async def register_first_generation(self, grant_id, registration):
        control = self.control
        if control._mutex.locked() or control._tasks:
            raise StorageUnavailable('PG_PROCESS_LEASE_ISSUER_BACKPRESSURE')
        async with control._mutex, asyncio.timeout(control.runtime.config.transaction_timeout_ms / 1000):
            return await self._register(grant_id, registration)

    async def _register(self, grant_id, registration, *, handoff=None):
        control = self.control
        control._original()
        if (not isinstance(grant_id, UUID) or type(registration) is not ProcessRegistration
                or registration.account_id != control.runtime.config.account_id
                or registration.database_lineage != control.runtime.config.database_lineage
                or registration.principal != control.runtime.config.principal):
            raise StorageError('PG_PROCESS_LEASE_REGISTRATION_INVALID')
        await control._primary()
        c, cfg = control.connection, control.runtime.config
        async with c.transaction():
            await c.execute("SELECT set_config('statement_timeout',$1,true),set_config('lock_timeout',$2,true),set_config('idle_in_transaction_session_timeout',$3,true)",
                            str(cfg.statement_timeout_ms), str(cfg.acquire_timeout_ms), str(cfg.idle_transaction_timeout_ms))
            a = await c.fetchrow('SELECT * FROM life_financial.accounts WHERE account_id=$1 FOR UPDATE', cfg.account_id)
            has_head = await c.fetchval('SELECT EXISTS(SELECT 1 FROM life_financial.process_leases WHERE account_id=$1)', cfg.account_id)
            if a['environment'] != 'OFFLINE_SIMULATION' or has_head != (handoff is not None):
                raise StorageError('PG_PROCESS_LEASE_HANDOFF_REQUIRED')
            policy = await c.fetchrow('SELECT * FROM life_financial.process_lease_policies WHERE account_id=$1', cfg.account_id)
            configuration = await c.fetchval('SELECT body FROM life_financial.configurations WHERE account_id=$1 AND config_hash=$2', cfg.account_id, cfg.binding.config_hash)
            configuration = json.loads(configuration)
            if (policy is None or policy['policy_hash'] != cfg.binding.policy_hash
                    or configuration.get('operator_verifier') != control.verifier.policy()
                    or configuration.get('process_lease') != dict(lease_duration_ms=policy['lease_duration_ms'], max_registrations=policy['max_registrations'])):
                raise StorageError('PG_PROCESS_LEASE_POLICY_INVALID')
            row = await c.fetchrow('''SELECT r.operation_id,r.body,g.* FROM life_financial.control_records r
                JOIN life_financial.grant_consumptions consumption USING(account_id,operation_id)
                JOIN life_financial.activation_grants g USING(account_id,grant_id)
                WHERE r.account_id=$1 AND g.grant_id=$2''', cfg.account_id, grant_id)
            body = None if row is None else json.loads(row['body'])
            if row is None or body['payload'].get('mode') != 'ACTIVE' or body['payload'].get('token') != registration.owner.json():
                raise StorageError('PG_PROCESS_LEASE_SIGNED_GRANT_REQUIRED')
            control.verifier.audit(body, cfg.binding)
            if not await c.fetchval('SELECT life_financial.control_verified($1)', cfg.account_id):
                raise StorageError('PG_PROCESS_LEASE_SIGNED_GRANT_REQUIRED')
            now = await c.fetchval('SELECT clock_timestamp()')
            if (not row['issued_at'] <= now < row['expires_at'] or a['mode'] != 'ACTIVE'
                    or a['owner_process'] != registration.owner.process_id or a['owner_generation'] != registration.owner.generation
                    or a['owner_epoch'] != registration.owner.epoch or a['current_session'] != registration.owner.session_id
                    or a['owner_since'] != registration.owner.valid_from or a['owner_until'] != registration.owner.expires_at):
                raise StorageError('PG_PROCESS_LEASE_SIGNED_GRANT_REQUIRED')
            if handoff is not None:
                await handoff(c, a)
            existing = await c.fetchrow('SELECT * FROM life_financial.process_lease_registrations WHERE account_id=$1 AND grant_id=$2', cfg.account_id, grant_id)
            if existing is not None:
                if json.loads(existing['owner']) != registration.owner.json() or existing['capability_hash'] != registration.capability_hash:
                    raise StorageError('PG_PROCESS_LEASE_REGISTRATION_CONFLICT')
                return  # Historical registration only; never readiness/secret recovery.
            count = await c.fetchval('SELECT count(*) FROM life_financial.process_lease_registrations WHERE account_id=$1', cfg.account_id)
            if count >= policy['max_registrations']:
                raise StorageError('PG_PROCESS_LEASE_REGISTRATION_BOUND')
            await c.execute('''INSERT INTO life_financial.process_lease_registrations VALUES($1,$2,$3,$4,$5,$6,$7)''',
                            cfg.account_id, grant_id, registration.principal, json.dumps(registration.owner.json()), registration.capability_hash, now, row['operation_id'])


class PgProcessLease:
    def __init__(self, runtime, capability):
        if type(runtime) is not PgRuntime or type(capability) is not ProcessCapability:
            raise StorageError('PG_PROCESS_LEASE_CONTEXT_INVALID')
        capability._validate(runtime.config)
        if not any(m.version == 114 for m in runtime._bundle) or runtime._process_capability is not None:
            raise StorageError('PG_PROCESS_LEASE_ALREADY_ATTACHED_OR_UNSUPPORTED')
        self.runtime, self.capability = runtime, capability
        runtime._process_capability = capability
        runtime._lease_ready = False

    @property
    def ready(self):
        self.capability._validate(self.runtime.config)
        return self.runtime.lease_ready

    def stop(self):
        self.runtime._lease_ready = False
        self.runtime._healthy = False

    async def lookup(self, operation_id):
        identifier(operation_id)

        async def read(c):
            value = await c.fetchval('SELECT receipt FROM life_financial.process_lease_commands WHERE account_id=$1 AND operation_id=$2',
                                     self.runtime.config.account_id, operation_id)
            return None if value is None else ProcessLeaseReceipt.parse({**value, 'replayed': True})
        return await self.runtime._admit(read, safety=True, operation='process_lease_lookup')

    async def claim(self, operation_id):
        return await self._command(operation_id, 'CLAIM')

    async def heartbeat(self, operation_id):
        return await self._command(operation_id, 'HEARTBEAT')

    async def _command(self, operation_id, kind):
        import asyncpg
        identifier(operation_id)
        runtime, cfg = self.runtime, self.runtime.config
        self.capability._validate(cfg)
        if (runtime._process_capability is not self.capability or not runtime._healthy or runtime._closing
                or kind == 'HEARTBEAT' and not runtime.lease_valid):
            raise StorageError('PG_PROCESS_LEASE_LOCAL_UNARMED')
        started = time.monotonic()
        boundary = {'attempted': False}

        async def execute(c):
            transaction = c.transaction()
            phase('begin')
            await transaction.start()
            try:
                phase('execute')
                grant = await c.fetchval('SELECT grant_id FROM life_financial.process_lease_registrations WHERE account_id=$1 AND capability_hash=$2',
                                         cfg.account_id, self.capability.registration.capability_hash)
                if grant is None:
                    raise StorageError('PG_PROCESS_LEASE_CAPABILITY_INVALID')
                value = await c.fetchval('SELECT life_financial.process_lease_command($1,$2,$3,$4)', cfg.account_id, operation_id, kind, grant)
                boundary['attempted'] = True
                trace = _current_trace.get()
                trace.commit_attempted = True
                phase('commit')
                await runtime._commit(transaction)
                trace.commit_confirmed = True
                phase('parse')
                return ProcessLeaseReceipt.parse(value)
            except asyncpg.PostgresError as error:
                failure(error)
                definite = bool(error.sqlstate and (error.sqlstate.startswith('23') or error.sqlstate in ('P0001', '55000', '22023', '22P02')))
                if definite:
                    boundary['attempted'] = False
                if c.is_in_transaction():
                    phase('rollback')
                    await transaction.rollback()
                if definite:
                    raise sql_error(error) from None
                if boundary['attempted']:
                    raise CommitUnknown('PG_COMMIT_UNKNOWN') from None
                raise StorageUnavailable('PG_PROCESS_LEASE_DATABASE_UNAVAILABLE') from None
            except BaseException as error:
                failure(error)
                if c.is_in_transaction():
                    phase('rollback')
                    await transaction.rollback()
                raise
        try:
            receipt = await runtime._admit(execute, safety=True, commit_state=boundary, operation='process_lease_' + kind.lower())
            if not receipt.replayed and runtime._healthy and not runtime._closing:
                if kind == 'CLAIM':
                    runtime._lease_ready = True
                # Request-start monotonic bound is conservative across command
                # and release latency; primary DB time remains authoritative.
                runtime._lease_deadline = started + (receipt.expires_at - receipt.observed_at).total_seconds()
            return receipt
        except BaseException:
            runtime._lease_ready = False
            runtime._healthy = False
            raise
