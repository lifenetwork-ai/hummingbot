"""Private process enrollment and owner-side current-head broker qualification."""
import asyncio
import json
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    StorageError,
    StorageUnavailable,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.generation_access import (
    BrokerRegistration,
    GenerationBroker,
    GenerationClient,
)
from hummingbot.strategy_v2.life_liquidity.storage.postgres.control import PgControlRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace, failure, phase
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime, sql_error


async def bind_private_broker(runtime, client, operation_id):
    identifier(operation_id)
    if (type(runtime) is not PgRuntime or type(client) is not GenerationClient or not runtime.lease_ready
            or runtime._process_capability is None or runtime._process_capability.registration != client.identity.registration.process):
        raise StorageError('PG_BROKER_ORIGINAL_PROCESS_REQUIRED')
    client._original()
    boundary = {'attempted': False}

    async def command(c):
        import asyncpg
        tx = c.transaction()
        await tx.start()
        try:
            phase('execute')
            value = await c.fetchval('SELECT life_financial.process_bind_broker($1,$2,$3)', runtime.config.account_id,
                                     operation_id, client.identity.registration.client_id)
            boundary['attempted'] = True
            _current_trace.get().commit_attempted = True
            phase('commit')
            await runtime._commit(tx)
            _current_trace.get().commit_confirmed = True
            return value
        except asyncpg.PostgresError as error:
            failure(error)
            if c.is_in_transaction():
                await tx.rollback()
            if error.sqlstate and (error.sqlstate.startswith('23') or error.sqlstate in ('P0001', '55000', '22023', '22P02')):
                boundary['attempted'] = False
                raise sql_error(error) from None
            raise CommitUnknown('PG_BROKER_BINDING_COMMIT_UNKNOWN') if boundary['attempted'] else StorageUnavailable('PG_BROKER_BINDING_UNAVAILABLE') from None
        except BaseException as error:
            failure(error)
            if c.is_in_transaction():
                await tx.rollback()
            if boundary['attempted']:
                raise CommitUnknown('PG_BROKER_BINDING_COMMIT_UNKNOWN') from None
            raise
    try:
        receipt = await runtime._admit(command, safety=True, commit_state=boundary, operation='process_bind_broker')
        if (receipt.get('replayed') is not False or receipt.get('trading_permission') is not False
                or receipt.get('client_id') != client.identity.registration.client_id):
            raise StorageError('PG_BROKER_ORIGINAL_BINDING_REQUIRED')
        return receipt
    except BaseException:
        runtime._healthy = runtime._lease_ready = False
        client.stop()
        raise


class PgGenerationBrokerControl:
    """Control-role API, outside the runtime FETCH/AUTHORIZE dispatcher."""

    def __init__(self, control, broker):
        if type(control) is not PgControlRepository or type(broker) is not GenerationBroker:
            raise StorageError('PG_BROKER_CONTROL_INVALID')
        if control.binding != broker.binding or control.runtime.config.database_lineage != broker.database_lineage:
            raise StorageError('PG_BROKER_CONTROL_SCOPE_INVALID')
        self.control, self.broker = control, broker
        self._context = control, broker

    async def sync(self, registration, *, enable_active=False):
        control, broker = self.control, self.broker
        if self._context != (control, broker) or type(registration) is not BrokerRegistration or type(enable_active) is not bool:
            raise StorageError('PG_BROKER_CONTROL_CONTEXT_CHANGED')
        if control._mutex.locked() or control._tasks:
            raise StorageUnavailable('PG_BROKER_CONTROL_BACKPRESSURE')
        cfg = control.runtime.config
        if registration.process.account_id != cfg.account_id or registration.process.database_lineage != cfg.database_lineage or registration.process.principal != cfg.principal:
            raise StorageError('PG_BROKER_CONTROL_SCOPE_INVALID')
        async with control._mutex, asyncio.timeout(cfg.transaction_timeout_ms / 1000):
            control._original()
            await control._primary()
            c = control.connection
            async with c.transaction():
                await c.execute("SELECT set_config('statement_timeout',$1,true),set_config('lock_timeout',$2,true)", str(cfg.statement_timeout_ms), str(cfg.acquire_timeout_ms))
                a = await c.fetchrow('SELECT * FROM life_financial.accounts WHERE account_id=$1 FOR UPDATE', cfg.account_id)
                await control._history()
                config = json.loads(await c.fetchval('SELECT body FROM life_financial.configurations WHERE account_id=$1 AND config_hash=$2', cfg.account_id, cfg.binding.config_hash))
                if config.get('generation_access') != broker.policy() or config.get('operator_verifier') != control.verifier.policy():
                    raise StorageError('PG_BROKER_POLICY_INVALID')
                row = await c.fetchrow('''SELECT b.*,r.owner,r.capability_hash,r.principal,h.expires_at,h.last_operation,clock_timestamp() AS now,
                    EXISTS(SELECT 1 FROM life_financial.process_recovery_grants x WHERE x.account_id=h.account_id AND x.grant_id=h.grant_id) AS recovery
                    FROM life_financial.process_leases h JOIN life_financial.process_lease_registrations r USING(account_id,grant_id)
                    JOIN life_financial.process_broker_bindings b USING(account_id,grant_id,lease_epoch)
                    WHERE h.account_id=$1''', cfg.account_id)
                if (row is None or row['client_id'] != registration.client_id or row['capability_hash'] != registration.process.capability_hash
                        or json.loads(row['owner']) != registration.identity.owner.json() or row['principal'] != registration.process.principal
                        or row['now'] >= row['expires_at'] or row['now'] < row['registered_at']
                        or a['mode'] not in ('ACTIVE', 'RECOVERY_ONLY') or enable_active and (a['mode'] != 'ACTIVE' or row['recovery'])):
                    raise StorageError('PG_BROKER_NATIVE_BINDING_UNQUALIFIED')
                broker.bind_head(registration, UUID(str(row['grant_id'])), row['lease_epoch'])
                if enable_active:
                    broker.enable(registration, mode='ACTIVE', expires_at=registration.identity.owner.expires_at)
