"""Typed original pending commands; recovery never reconstructs their prices."""
from dataclasses import dataclass
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StateBinding, StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import AckSpot, FillSpot, PrepareSpot
from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand


@dataclass(frozen=True)
class PendingSpotCommand:
    kind: str
    command: PrepareSpot | AckSpot | FillSpot | CashflowSpot | SpotLifecycleCommand
    owner: OwnerToken

    def __post_init__(self):
        expected = {'PREPARE': PrepareSpot, 'ARM': PrepareSpot, 'ACK': AckSpot, 'FILL': FillSpot,
                    'CASHFLOW': CashflowSpot, 'LIFECYCLE': SpotLifecycleCommand}.get(self.kind)
        if expected is None or not isinstance(self.command, expected) or not isinstance(self.owner, OwnerToken):
            raise StorageError('SPOT_PENDING_COMMAND_INVALID')


class PendingSpotRepository(Protocol):
    async def pending_spot_commands(self, binding: StateBinding) -> tuple[PendingSpotCommand, ...]:
        ...


class LegacyPendingAdapter:
    def _pending_spot_commands(self):
        self._snapshot()
        meta = self._read()
        result = []
        for row in meta.resources['commands'].values():
            if row['state'] in ('PREPARING', 'ARMING'):
                command = PrepareSpot.from_json(row['command'])
                owner = OwnerToken.from_json(row['original_owner'])
                if row['fingerprint'] != fingerprint({'command': command.json(), 'cut': row['original_cut'], 'owner': owner.json()}):
                    raise StorageError('SPOT_PENDING_ORIGIN_UNVERIFIED')
                result.append(PendingSpotCommand('PREPARE' if row['state'] == 'PREPARING' else 'ARM', command, owner))
        for name, kind, complete, parse in (
            ('acknowledgements', 'ACK', 'ACKED', self._ack_from_json),
            ('fill_operations', 'FILL', 'APPLIED', FillSpot.from_json),
            ('cashflow_operations', 'CASHFLOW', 'APPLIED', self._cashflow_from_json),
            ('lifecycle_operations', 'LIFECYCLE', 'APPLIED', SpotLifecycleCommand.from_json),
        ):
            for row in meta.resources.get(name, {}).values():
                if row['state'] == complete:
                    continue
                command = parse(row['command'])
                owner = (OwnerToken.from_json(row['owner']) if row.get('owner') is not None else
                         OwnerToken.from_json(meta.resources['commands'][command.prepare_operation_id]['original_owner'])
                         if kind in ('ACK', 'FILL') else meta.ownership.token)
                if owner is None or row.get('fingerprint', row.get('identity')) != fingerprint({
                        'command': command.json(), 'owner': owner.json()}):
                    raise StorageError('SPOT_PENDING_ORIGIN_UNVERIFIED')
                result.append(PendingSpotCommand(kind, command, owner))
        return tuple(result)

    @staticmethod
    def _ack_from_json(value):
        from datetime import datetime
        data = dict(value)
        data['observed_at'] = datetime.fromisoformat(data['observed_at'])
        return AckSpot(**data)

    async def pending_spot_commands(self, binding):
        self._assert_binding(binding)
        return await self._call(self._pending_spot_commands)
