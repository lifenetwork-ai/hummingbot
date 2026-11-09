"""Stopped-engine recovery coordinator with independent GET-only readers."""
import asyncio
import functools

from hummingbot.cli.admin_database import AdminDatabaseExecutor
from hummingbot.core.admin_identity import AccessDenied
from hummingbot.core.bot_ownership import OwnershipLost
from hummingbot.core.recovery import RECOVERY_TIMEOUT_SECONDS, RecoveryBlocked, recovery_blockers, replay_positions
from hummingbot.core.recovery_exchange import verify_exchange
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.recovery_store import RecoveryStore


class RecoveryReader:
    PATHS = frozenset({'/api/v5/account/config', '/api/v5/public/instruments', '/api/v5/trade/order',
                      '/api/v5/trade/orders-pending', '/api/v5/trade/fills', '/api/v5/account/positions'})

    def __init__(self, connector):
        self.connector = connector
        self.name = connector.name

    async def _api_get(self, *, path_url, **kwargs):
        if path_url not in self.PATHS:
            raise RecoveryBlocked('recovery_read_scope_invalid')
        if self.name == 'okx' and path_url == '/api/v5/account/positions':
            # Spot has no positions bucket; reuse the existing conservative
            # account-config bucket, as the credential cleanup adapter does.
            kwargs['limit_id'] = '/api/v5/account/config'
        return await self.connector._api_get(path_url=path_url, **kwargs)

    async def _initialize_trading_pair_symbol_map(self):
        await self.connector._initialize_trading_pair_symbol_map()

    def _format_size_to_amount(self, pair, size):
        return self.connector._format_size_to_amount(pair, size)

    async def close(self):
        await self.connector._web_assistants_factory.close()


class RecoveryCoordinator:
    def __init__(self, host):
        self.host = host
        self.store = RecoveryStore(host.store)
        self.work = AdminDatabaseExecutor()
        self.readers = {}

    async def call(self, function, *args, **kwargs):
        return await self.work.submit(functools.partial(function, *args, **kwargs), heavy=True)

    async def run(self, operation_id):
        try:
            await asyncio.wait_for(self._run(operation_id), RECOVERY_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            raise
        except OwnershipLost:
            raise
        except Exception as error:
            code = (error.code if isinstance(error, (RecoveryBlocked, ControlConflict, AccessDenied)) else
                    'recovery_timeout' if isinstance(error, asyncio.TimeoutError) else 'recovery_check_unavailable')
            self.host.owner.check_owner()
            self.store.fail(self.host.owner, operation_id, code)
            self.host.log.warning('Recovery operation blocked code=%s', code,
                                  extra={'event': 'recovery_blocked', 'operation_id': operation_id, 'reason_code': code})
        finally:
            readers, self.readers = self.readers, {}
            await asyncio.gather(*(reader.close() for reader in readers.values()), return_exceptions=True)

    async def _run(self, operation_id):
        host, owner = self.host, self.host.owner
        # Physical ownership connection belongs to this event-loop thread.
        # Only bulk read snapshots/pure replay use the background worker.
        # Claim/finalize use the existing short DB budgets while trading is stopped.
        row = self.store.claim(owner)
        if row is None:
            return
        core = host.hb.trading_core
        if (host.running or host.start_task is not None or core.strategy is not None
                or core._is_running or core.connector_manager.connectors or owner.submission_tasks or owner.transport_tasks):
            raise RecoveryBlocked('recovery_stop_incomplete')
        owner.allow_new_orders = False
        snapshot = await self.call(self.store.snapshot)
        blockers = recovery_blockers(snapshot['state'])
        if blockers:
            if row['action'] == 'recovery_check':
                self.store.finish_check(owner, row, snapshot, [], dict(verified=False), blockers=blockers)
                return
            raise RecoveryBlocked(blockers[0])
        plan = await self.call(replay_positions, snapshot)
        if host.managed_credentials is None:
            raise RecoveryBlocked('recovery_credentials_unavailable')
        await host.funding.credentials_changed()
        await host.managed_credentials.reload()
        from hummingbot.connector.credential_adapters import read_only_connector
        for name, config in host.managed_credentials.configs.items():
            if name not in ('okx', 'okx_perpetual'):
                raise RecoveryBlocked('unsupported_connector')
            self.readers[name] = RecoveryReader(read_only_connector(name, config))
        proof = await verify_exchange(snapshot, plan, self.readers)
        owner.check_owner()
        if row['action'] == 'recovery_check':
            self.store.finish_check(owner, row, snapshot, plan, proof, blockers=[])
        else:
            self.store.finish_reset(owner, row, snapshot, plan, proof)
            host.error = None
            host.halt_requested = False
            host.applied_version = None

    async def close(self):
        await self.work.close()
