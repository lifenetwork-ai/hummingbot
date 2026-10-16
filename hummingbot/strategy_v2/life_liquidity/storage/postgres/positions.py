"""Sequenced exact held-position reporting. Never exchange-position authority."""
from dataclasses import dataclass, field

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    ResourceVersion,
    StateCut,
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder import PgRecorderRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import PositionReport, PositionState


@dataclass(frozen=True)
class PositionDeliveryReceipt:
    projection_id: str
    sequence: int
    state: str
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class PendingPositionDelivery:
    command: PositionReport
    original_cut: FinancialCut
    owner: OwnerToken


class PgPositionRepository(PgRecorderRepository):
    _controller_sql = "o.body#>>'{command,controller_id}'"
    _all_history = True
    _subtypes = ('POSITION',)

    @staticmethod
    def _receipt(value):
        if value.get('trading_permission') is not False or value.get('kind') not in ('RECORDER_POSITION_STAGE', 'RECORDER_POSITION_APPLY'):
            raise StorageUnavailable('PG_POSITION_RECEIPT_INVALID')
        return PositionDeliveryReceipt(value['projection_id'], value['sequence'], value['state'], value['replayed'])

    async def stage(self, command, *, cut, owner):
        if type(command) is not PositionReport or command.binding != self.binding or command.controller_id != self.controller_id:
            raise StorageError('PG_POSITION_COMMAND_INVALID')
        version, digest = self.financial._cut(cut, owner)
        identity = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        args = (self._runtime.config.account_id, self.binding.json(), owner.json(), version, digest, identity, command.json())
        return await self._runtime._transaction(
            'SELECT life_financial.recorder_position_stage($1,$2,$3,$4,$5,$6,$7)', args, owner, self._receipt, observation=True)

    async def apply(self, projection_id, *, cut, owner):
        identifier(projection_id)
        version, digest = self.financial._cut(cut, owner)
        args = (self._runtime.config.account_id, self.binding.json(), owner.json(), version, digest, projection_id)
        return await self._runtime._transaction(
            'SELECT life_financial.recorder_position_apply($1,$2,$3,$4,$5,$6)', args, owner, self._receipt, observation=True)

    async def pending(self, binding):
        result = []
        for row in await self._read(binding, True):
            body, version = row['body'], row['body']['version']
            checkpoint = StateCut(binding, version, version, (ResourceVersion('account', version),), body['cut'])
            result.append(PendingPositionDelivery(
                PositionReport.from_json(body['command']), FinancialCut(checkpoint, (('postgres_account', body['cut']),)),
                OwnerToken.from_json(body['owner'])))
        return tuple(result)

    async def positions(self, binding):
        # Full bounded audit history, not an unverified current-row cache.
        rows, cut = await self._read(binding, False, with_cut=True)
        if not rows:
            return PositionState(binding, 0, cut.checkpoint, ())
        last = rows[-1]
        return PositionState(binding, last['sequence'], cut.checkpoint, PositionReport.from_json(last['body']['command']).positions)

    async def read(self, binding):
        return await self.positions(binding)
