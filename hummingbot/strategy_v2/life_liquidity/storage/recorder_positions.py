"""Exact held-position snapshots with a sequenced, independent SQL outbox.

These are reporting records. No snapshot, SQL row or receipt proves an exchange
position, releases a reservation or grants submission authority.
"""
import asyncio
from dataclasses import dataclass, fields
from decimal import Decimal
from typing import Protocol

from hummingbot.model.position import Position
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerMode,
    OwnerToken,
    StateBinding,
    StateCut,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import decimal, signed_decimal

HOLD_VALUES = ('net_amount_base', 'avg_entry_price', 'realized_pnl_quote', 'volume_traded_quote',
               'cum_fees_quote', 'buy_amount_base', 'buy_amount_quote', 'sell_amount_base', 'sell_amount_quote')
SQL_VALUES = ('volume_traded_quote', 'amount', 'breakeven_price', 'unrealized_pnl_quote',
              'realized_pnl_quote', 'cum_fees_quote')


def sql_value(value):
    """Mirror the existing SqliteDecimal(6) reporting representation only."""
    integer = int(value * 10 ** 6)
    if not -(2 ** 63) <= integer < 2 ** 63:
        raise StorageError('RECORDER_POSITION_SQL_OVERFLOW')
    return Decimal(integer) / 10 ** 6


def position_values(row):
    return {key: getattr(row, key) for key in ('id', 'controller_id', 'connector_name', 'trading_pair',
                                               'side', 'timestamp') + SQL_VALUES}


@dataclass(frozen=True)
class HeldPosition:
    connector_name: str
    trading_pair: str
    side: str | None
    order_ids: tuple[str, ...]
    net_amount_base: Decimal
    avg_entry_price: Decimal
    realized_pnl_quote: Decimal
    volume_traded_quote: Decimal
    cum_fees_quote: Decimal
    buy_amount_base: Decimal
    buy_amount_quote: Decimal
    sell_amount_base: Decimal
    sell_amount_quote: Decimal
    mark_price: Decimal

    def __post_init__(self):
        if (self.connector_name not in ('okx', 'okx_perpetual') or self.trading_pair != 'LIFE-USDT'
                or self.side not in (None, 'BUY', 'SELL') or type(self.order_ids) is not tuple
                or tuple(sorted(set(self.order_ids))) != self.order_ids):
            raise StorageError('RECORDER_POSITION_INVALID')
        for wire in self.order_ids:
            identifier(wire)
        for key in HOLD_VALUES + ('mark_price',):
            parser = signed_decimal if key in ('net_amount_base', 'realized_pnl_quote', 'cum_fees_quote') else decimal
            object.__setattr__(self, key, parser(getattr(self, key)))
        for value in self.summary().values():
            sql_value(value)

    @classmethod
    def capture(cls, hold, mark_price):
        return cls(hold.connector_name, hold.trading_pair, None if hold.side is None else hold.side.name,
                   tuple(sorted(hold.order_ids)), *(getattr(hold, key) for key in HOLD_VALUES), mark_price)

    @property
    def key(self):
        return (self.connector_name, self.trading_pair, self.side)

    def json(self):
        return {field.name: (str(getattr(self, field.name)) if field.name in HOLD_VALUES + ('mark_price',)
                             else list(self.order_ids) if field.name == 'order_ids' else getattr(self, field.name))
                for field in fields(self)}

    @classmethod
    def from_json(cls, value):
        return cls(**{key: tuple(item) if key == 'order_ids' else Decimal(item) if key in HOLD_VALUES + ('mark_price',)
                      else item for key, item in value.items()})

    def summary(self):
        return {'volume_traded_quote': self.volume_traded_quote, 'amount': abs(self.net_amount_base),
                'breakeven_price': self.avg_entry_price,
                'unrealized_pnl_quote': (self.mark_price - self.avg_entry_price) * self.net_amount_base,
                'realized_pnl_quote': self.realized_pnl_quote, 'cum_fees_quote': self.cum_fees_quote}

    def restore(self):
        from hummingbot.core.data_type.common import TradeType
        from hummingbot.strategy_v2.executors.executor_orchestrator import PositionHold
        hold = PositionHold(self.connector_name, self.trading_pair, None if self.side is None else TradeType[self.side])
        hold.order_ids = set(self.order_ids)
        for key in HOLD_VALUES:
            setattr(hold, key, getattr(self, key))
        return hold


@dataclass(frozen=True)
class PositionReport:
    operation_id: str
    binding: StateBinding
    controller_id: str
    expected_sequence: int
    timestamp_ms: int
    positions: tuple[HeldPosition, ...]

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.controller_id)
        if (len(self.operation_id) > 96 or not isinstance(self.binding, StateBinding)
                or type(self.expected_sequence) is not int or self.expected_sequence < 0
                or type(self.timestamp_ms) is not int or not 0 <= self.timestamp_ms < 2 ** 63
                or type(self.positions) is not tuple or any(not isinstance(p, HeldPosition) for p in self.positions)
                or len({p.key for p in self.positions}) != len(self.positions)):
            raise StorageError('RECORDER_POSITION_REPORT_INVALID')
        wires = [wire for p in self.positions for wire in p.order_ids]
        if len(set(wires)) != len(wires):
            raise StorageError('RECORDER_POSITION_WIRE_CONFLICT')
        object.__setattr__(self, 'positions', tuple(sorted(self.positions, key=lambda p: (p.connector_name, p.side or ''))))

    def json(self):
        return {'operation_id': self.operation_id, 'binding': self.binding.json(), 'controller_id': self.controller_id,
                'expected_sequence': self.expected_sequence, 'timestamp_ms': self.timestamp_ms,
                'positions': [p.json() for p in self.positions]}

    @classmethod
    def from_json(cls, value):
        return cls(value['operation_id'], StateBinding.from_json(value['binding']), value['controller_id'],
                   value['expected_sequence'], value['timestamp_ms'], tuple(HeldPosition.from_json(p) for p in value['positions']))

    def sql_rows(self):
        return tuple(Position(id='life:' + fingerprint({'binding': self.binding.json(), 'controller': self.controller_id,
                                                       'key': list(p.key)}), controller_id=self.controller_id,
                              connector_name=p.connector_name, trading_pair=p.trading_pair,
                              side='BUY' if p.net_amount_base >= 0 else 'SELL', timestamp=self.timestamp_ms,
                              **{key: sql_value(value) for key, value in p.summary().items()}) for p in self.positions)


@dataclass(frozen=True)
class PositionState:
    binding: StateBinding
    sequence: int
    cut: StateCut
    positions: tuple[HeldPosition, ...]


@dataclass(frozen=True)
class PendingPositionReport:
    command: PositionReport
    original_cut: StateCut
    owner: OwnerToken


@dataclass(frozen=True)
class PositionReceipt:
    binding: StateBinding
    operation_id: str
    sequence: int
    replayed: bool
    atomic_scope: AtomicScope = AtomicScope.RECOVERABLE_SAGA


class PositionRepository(Protocol):
    async def positions(self, binding: StateBinding) -> PositionState:
        ...

    async def pending_positions(self, binding: StateBinding) -> tuple[PendingPositionReport, ...]:
        ...

    async def report_positions(self, command: PositionReport, *, cut: StateCut, owner: OwnerToken) -> PositionReceipt:
        ...


class FilePositionAdapter:
    def _position_head(self, meta):
        head = meta.resources.get('recorder_position_head')
        if head is None:
            return None
        command = PositionReport.from_json(thaw(head['command']))
        origin = meta.resources.get('recorder_positions', {}).get(command.operation_id)
        if (origin is None or origin['state'] != 'APPLIED' or thaw(origin['command']) != command.json()
                or command.binding != self.binding and not self.store._inherited_record('recorder_positions', command.operation_id, origin, meta.resources)
                or command.controller_id != self.controller_id
                or origin['identity'] != fingerprint({'command': command.json(), 'cut': thaw(origin['cut']),
                                                      'owner': thaw(origin['owner'])})
                or head['sequence'] != command.expected_sequence + 1):
            raise StorageUnavailable('RECORDER_POSITION_HEAD_UNVERIFIED')
        return command

    def _position_rows(self):
        return tuple(sorted((position_values(row) for row in self._recorder.get_positions_by_controller(self.controller_id)),
                            key=lambda row: row['id']))

    def _expected_position_rows(self, command):
        return () if command is None else tuple(sorted((position_values(row) for row in command.sql_rows()), key=lambda row: row['id']))

    def _positions(self):
        meta = self.store._read()
        self._verify_binding(meta)
        self._pending_positions()
        head = self._position_head(meta)
        if self._position_rows() != self._expected_position_rows(head):
            raise StorageUnavailable('RECORDER_POSITION_NATIVE_UNVERIFIED')
        return PositionState(self.binding, 0 if head is None else head.expected_sequence + 1, meta.cut,
                             () if head is None else head.positions)

    async def positions(self, binding):
        self.store._assert_binding(binding)
        return await self.store._call(self._positions)

    def _pending_positions(self):
        meta = self.store._read()
        self._verify_binding(meta)
        result = []
        for row in meta.resources.get('recorder_positions', {}).values():
            command = PositionReport.from_json(thaw(row['command']))
            cut, owner = StateCut.from_json(thaw(row['cut'])), OwnerToken.from_json(thaw(row['owner']))
            if (command.binding != self.binding and not self.store._inherited_record('recorder_positions', command.operation_id, row, meta.resources)
                    or command.controller_id != self.controller_id
                    or row['identity'] != fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})):
                raise StorageError('RECORDER_POSITION_ORIGIN_UNVERIFIED')
            if row['state'] != 'APPLIED':
                if row['state'] != 'PROJECTING':
                    raise StorageError('RECORDER_POSITION_ORIGIN_UNVERIFIED')
                result.append(PendingPositionReport(command, cut, owner))
        return tuple(result)

    async def pending_positions(self, binding):
        self.store._assert_binding(binding)
        return await self.store._call(self._pending_positions)

    def _report_positions(self, command, cut, owner):
        self.store._assert_binding(command.binding)
        self.store._assert_binding(cut.binding)
        if command.controller_id != self.controller_id:
            raise StorageError('RECORDER_CONTROLLER_MISMATCH')
        meta = self.store._read()
        self._verify_binding(meta)
        self._pending_positions()  # Audit original identities before any SQL.
        now = utc(self.store._clock())
        if (meta.ownership.token != owner or meta.ownership.mode not in (
                OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY) or now < owner.valid_from):
            raise StorageError('RECORDER_POSITION_OWNER_INVALID')
        operations = thaw(meta.resources.get('recorder_positions', {}))
        previous = operations.get(command.operation_id)
        identity = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        if previous is not None:
            if previous['identity'] != identity:
                raise StorageConflict('RECORDER_POSITION_OPERATION_CONFLICT')
            if previous['state'] == 'APPLIED':
                # A historical lookup can repair deleted SQL rows only from the
                # latest verified head, never from the older requested snapshot.
                head = self._position_head(meta)
                expected = self._expected_position_rows(head)
                actual = self._position_rows()
                if actual != expected:
                    by_id = {row['id']: row for row in expected}
                    if any(row != by_id.get(row['id']) for row in actual):
                        raise StorageUnavailable('RECORDER_POSITION_NATIVE_UNVERIFIED')
                    try:
                        self._recorder.project_position_reports(self.controller_id, actual, head.sql_rows())
                        if self._position_rows() != expected:
                            raise StorageUnavailable('RECORDER_POSITION_NATIVE_POSTCONDITION_FAILED')
                    except BaseException:
                        self.store._uncertain = True
                        raise StorageUnavailable('RECORDER_POSITIONS_UNKNOWN') from None
                return PositionReceipt(self.binding, command.operation_id, command.expected_sequence + 1, True)
        head = self._position_head(meta)
        if command.expected_sequence != (0 if head is None else head.expected_sequence + 1):
            raise StorageConflict('RECORDER_POSITION_SEQUENCE_STALE')
        if head is not None:
            old = {p.key: p for p in head.positions}
            new = {p.key: p for p in command.positions}
            if (command.timestamp_ms < head.timestamp_ms or not old.keys() <= new.keys()
                    or any(not set(p.order_ids) <= set(new[key].order_ids) for key, p in old.items())):
                raise StorageConflict('RECORDER_POSITION_HISTORY_REGRESSION')
        before, after = self._expected_position_rows(head), self._expected_position_rows(command)
        actual = self._position_rows()
        if actual != before and (previous is None or actual != after):
            raise StorageUnavailable('RECORDER_POSITION_NATIVE_UNVERIFIED')
        if previous is None:
            if meta.cut != cut:
                raise StorageConflict('STORAGE_CUT_STALE')
            if any(row['state'] != 'APPLIED' for name in ('recorder_outbox', 'recorder_progress', 'recorder_positions', 'recorder_events', 'recorder_configurations')
                   for row in meta.resources.get(name, {}).values()):
                raise StorageUnavailable('RECORDER_OUTBOX_REPAIR_REQUIRED')
            operations[command.operation_id] = {'identity': identity, 'command': command.json(), 'cut': cut.json(),
                                                'owner': owner.json(), 'state': 'PROJECTING'}
            self.store._commit(meta.cut, owner, 'position-start:' + command.operation_id,
                               (('recorder_positions', encode(operations)), ('recorder_binding', encode(self._identity))),
                               observation=True)
        try:
            self._recorder.project_position_reports(self.controller_id, before, command.sql_rows())
            if self._position_rows() != after:
                raise StorageUnavailable('RECORDER_POSITION_NATIVE_POSTCONDITION_FAILED')
            operations[command.operation_id]['state'] = 'APPLIED'
            latest = self.store._read()
            self.store._commit(latest.cut, owner, 'position-done:' + command.operation_id,
                               (('recorder_positions', encode(operations)),
                                ('recorder_position_head', encode({'command': command.json(),
                                                                  'sequence': command.expected_sequence + 1}))), observation=True)
            return PositionReceipt(self.binding, command.operation_id, command.expected_sequence + 1, previous is not None)
        except BaseException:
            self.store._uncertain = True
            raise StorageUnavailable('RECORDER_POSITIONS_UNKNOWN') from None

    async def report_positions(self, command, *, cut, owner):
        if not isinstance(command, PositionReport) or not isinstance(cut, StateCut) or not isinstance(owner, OwnerToken):
            raise StorageError('RECORDER_POSITION_REPORT_INVALID')
        try:
            return await self.store._call(self._report_positions, command, cut, owner)
        except asyncio.CancelledError:
            self.store._uncertain = True
            raise
