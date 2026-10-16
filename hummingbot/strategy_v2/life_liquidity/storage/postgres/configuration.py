"""Sequenced PostgreSQL configuration audit. Reports cannot activate trading."""
import json
from dataclasses import dataclass, field

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder import PgRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_configuration import ConfigurationReport, ConfigurationState


@dataclass(frozen=True)
class ConfigurationDeliveryReceipt:
    projection_id: str
    sequence: int
    active_revision: int
    reason_code: str
    state: str
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class PendingConfigurationDelivery:
    command: ConfigurationReport
    original_cut: FinancialCut
    owner: OwnerToken


class PgConfigurationRepository(PgRecorderRepository):
    _controller_sql = "o.body#>>'{command,controller_id}'"
    _all_history = True
    _subtypes = ('CONFIGURATION',)

    @staticmethod
    def _receipt(value):
        if value.get('trading_permission') is not False or value.get('kind') not in ('RECORDER_CONFIG_STAGE', 'RECORDER_CONFIG_APPLY'):
            raise StorageUnavailable('PG_CONFIG_RECEIPT_INVALID')
        return ConfigurationDeliveryReceipt(value['projection_id'], value['sequence'], value['active_revision'],
                                            value['reason_code'], value['state'], value['replayed'])

    async def stage(self, command, *, cut, owner):
        if (type(command) is not ConfigurationReport or command.binding != self.binding
                or command.controller_id != self.controller_id
                or json.loads(command.controller_json)['storage_backend'] != 'postgres'):
            raise StorageError('PG_CONFIG_COMMAND_INVALID')
        version, digest = self.financial._cut(cut, owner)
        identity = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        args = (self._runtime.config.account_id, self.binding.json(), owner.json(), version, digest, identity, command.json())
        return await self._runtime._transaction(
            'SELECT life_financial.recorder_config_stage($1,$2,$3,$4,$5,$6,$7)',
            args, owner, self._receipt, observation=True)

    async def apply(self, projection_id, *, cut, owner):
        identifier(projection_id)
        version, digest = self.financial._cut(cut, owner)
        args = (self._runtime.config.account_id, self.binding.json(), owner.json(), version, digest, projection_id)
        return await self._runtime._transaction(
            'SELECT life_financial.recorder_config_apply($1,$2,$3,$4,$5,$6)',
            args, owner, self._receipt, observation=True)

    async def pending(self, binding):
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import ResourceVersion, StateCut
        result = []
        for row in await self._read(binding, True):
            body = row['body']
            version = body['version']
            checkpoint = StateCut(binding, version, version, (ResourceVersion('account', version),), body['cut'])
            result.append(PendingConfigurationDelivery(
                ConfigurationReport.from_json(body['command']),
                FinancialCut(checkpoint, (('postgres_account', body['cut']),)), OwnerToken.from_json(body['owner'])))
        return tuple(result)

    async def configuration(self, binding):
        try:
            rows = await self._read(binding, False)
        except StorageUnavailable as error:
            if str(error) == 'PG_RECORDER_DELIVERY_PENDING':
                raise StorageUnavailable('PG_CONFIG_DELIVERY_PENDING') from None
            raise
        if not rows:
            return ConfigurationState(0, 0, None, False, False, None)
        commands = tuple(ConfigurationReport.from_json(row['body']['command']) for row in rows)
        last = commands[-1]
        rejection = next((c.reason_code for c in reversed(commands) if c.outcome == 'REJECTED'), None)
        return ConfigurationState(rows[-1]['sequence'], last.active_revision, last.controller_json,
                                  rejection is not None, any(json.loads(c.controller_json)['manual_kill_switch'] for c in commands),
                                  last.outcome, last.reason_code, rejection)

    async def read(self, binding):
        return await self.configuration(binding)
