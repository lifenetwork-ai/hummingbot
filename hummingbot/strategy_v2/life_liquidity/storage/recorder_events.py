"""Detached OKX event reporting with a recoverable, independent SQL outbox.

Connector events are reporting observations, not authenticated account coverage,
financial fills, funding bills, terminal release or submission permission.
"""
import asyncio
import json
import math
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Protocol

from sqlalchemy import JSON, Float, Integer

from hummingbot.core.event.events import MarketEvent
from hummingbot.model.decimal_type_decorator import SqliteDecimal
from hummingbot.model.funding_payment import FundingPayment
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.trade_fill import TradeFill
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
from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import sql_value

CREATES = {'BuyOrderCreated', 'SellOrderCreated'}
STATUSES = {'OrderCancelled', 'OrderFailure', 'OrderExpired', 'BuyOrderCompleted', 'SellOrderCompleted'}
TABLES = {'order': Order, 'status': OrderStatus, 'trade': TradeFill, 'funding': FundingPayment, 'state': MarketState}


def json_text(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str)


@dataclass(frozen=True)
class EventSource:
    controller_id: str
    config_file_path: str
    strategy_name: str
    market: str

    def __post_init__(self):
        identifier(self.controller_id)
        if (self.market not in ('okx', 'okx_perpetual')
                or any(type(value) is not str or not 0 < len(value) <= 255
                       for value in (self.config_file_path, self.strategy_name))):
            raise StorageError('RECORDER_EVENT_SOURCE_INVALID')


@dataclass(frozen=True)
class EventProjection:
    binding: StateBinding
    source: EventSource
    payload_json: str
    tracking_json: str

    def __post_init__(self):
        if not isinstance(self.binding, StateBinding) or not isinstance(self.source, EventSource):
            raise StorageError('RECORDER_EVENT_INVALID')
        try:
            body, tracking = json.loads(self.payload_json), json.loads(self.tracking_json)
            kind = body['kind']
            common = {'kind', 'timestamp_ms'}
            keys = (common | {'wire', 'pair', 'exchange_id', 'order_type', 'side', 'amount', 'price', 'leverage', 'position'}
                    if kind in CREATES else common | {'wire', 'pair', 'exchange_id', 'order_type', 'side', 'amount', 'price',
                                                      'leverage', 'position', 'trade_id', 'fee_json', 'fee_quote'}
                    if kind == 'OrderFilled' else common | {'wire', 'exchange_id'} if kind in STATUSES
                    else common | {'pair', 'rate', 'amount'} if kind == 'FundingPaymentCompleted' else None)
            if (keys is None or set(body) != keys or type(body['timestamp_ms']) is not int
                    or not 0 <= body['timestamp_ms'] < 2 ** 63 or type(tracking) is not dict
                    or kind == 'FundingPaymentCompleted' and self.source.market != 'okx_perpetual'):
                raise StorageError('RECORDER_EVENT_INVALID')
            if 'pair' in body and body['pair'] != 'LIFE-USDT':
                raise StorageError('RECORDER_EVENT_MARKET_INVALID')
            for key in ('wire', 'trade_id'):
                if key in body:
                    identifier(body[key])
            if 'exchange_id' in body and body['exchange_id'] is not None:
                identifier(body['exchange_id'])
            if 'price' in body:
                if (body['side'] not in ('BUY', 'SELL') or body['order_type'] not in ('MARKET', 'LIMIT', 'LIMIT_MAKER')
                        or type(body['leverage']) is not int or body['leverage'] <= 0
                        or body['position'] not in ('NIL', 'OPEN', 'CLOSE')
                        or kind in CREATES and body['side'] != ('BUY' if kind == 'BuyOrderCreated' else 'SELL')):
                    raise StorageError('RECORDER_EVENT_INVALID')
                for key in ('amount', 'price'):
                    value = decimal(Decimal(body[key]))
                    if value <= 0:
                        raise StorageError('RECORDER_EVENT_INVALID')
                    body[key] = str(value)
                    sql_value(value)
            if kind == 'OrderFilled':
                body['fee_quote'] = str(signed_decimal(Decimal(body['fee_quote'])))
                sql_value(Decimal(body['fee_quote']))
                if type(json.loads(body['fee_json'])) is not dict:
                    raise StorageError('RECORDER_EVENT_INVALID')
                body['fee_json'] = json_text(json.loads(body['fee_json']))
            if kind == 'FundingPaymentCompleted':
                for key in ('amount', 'rate'):
                    body[key] = str(signed_decimal(Decimal(body[key])))
                    if not math.isfinite(float(body[key])):
                        raise StorageError('RECORDER_EVENT_INVALID')
            for wire in tracking:
                identifier(wire)
                if type(tracking[wire]) is not dict:
                    raise StorageError('RECORDER_EVENT_TRACKING_INVALID')
            object.__setattr__(self, 'payload_json', json_text(body))
            object.__setattr__(self, 'tracking_json', json_text(tracking))
        except StorageError:
            raise
        except Exception:
            raise StorageError('RECORDER_EVENT_INVALID') from None

    @classmethod
    def capture(cls, binding, source, tag, event, tracking):
        try:
            kind = MarketEvent(tag).name
            timestamp = event.creation_timestamp if kind in CREATES else event.timestamp
            if type(timestamp) not in (int, float) or not math.isfinite(timestamp) or timestamp < 0:
                raise StorageError('RECORDER_EVENT_TIME_INVALID')
            if kind == 'FundingPaymentCompleted' and type(timestamp) is not int:
                raise StorageError('RECORDER_EVENT_TIME_INVALID')
            # OKX funding events retain the REST bill's millisecond integer;
            # order/fill events use Hummingbot's seconds convention.
            body = {'kind': kind, 'timestamp_ms': timestamp if kind == 'FundingPaymentCompleted' else int(timestamp * 1000)}
            if kind in CREATES or kind == 'OrderFilled':
                body.update(wire=event.order_id, pair=event.trading_pair, exchange_id=event.exchange_order_id or None,
                            order_type=(event.type if kind in CREATES else event.order_type).name,
                            side=('BUY' if kind == 'BuyOrderCreated' else 'SELL') if kind in CREATES else event.trade_type.name,
                            amount=str(event.amount), price=str(event.price), leverage=event.leverage or 1,
                            position=event.position or 'NIL')
            if kind == 'OrderFilled':
                body.update(trade_id=event.exchange_trade_id, fee_json=json_text(event.trade_fee.to_json()),
                            fee_quote=str(event.trade_fee.fee_amount_in_token(trading_pair=event.trading_pair,
                                          price=event.price, order_amount=event.amount, token='USDT')))
            elif kind in STATUSES:
                body.update(wire=event.order_id, exchange_id=getattr(event, 'exchange_order_id', None) or None)
            elif kind == 'FundingPaymentCompleted':
                if event.market != source.market:
                    raise StorageError('RECORDER_EVENT_SOURCE_MISMATCH')
                body.update(pair=event.trading_pair, rate=str(event.funding_rate), amount=str(event.amount))
            return cls(binding, source, json_text(body), json_text(tracking))
        except StorageError:
            raise
        except Exception:
            raise StorageError('RECORDER_EVENT_CAPTURE_INVALID') from None

    @property
    def event_id(self):
        body = json.loads(self.payload_json)
        key = {'source': asdict(self.source), 'kind': body['kind'], 'wire': body.get('wire')}
        if body['kind'] == 'OrderFilled':
            key['trade_id'] = body['trade_id']  # Changed time/fees cannot hide a reused trade ID.
        elif body['kind'] not in CREATES:
            key['timestamp_ms'] = body['timestamp_ms']
        return fingerprint(key)

    @property
    def operation_id(self):
        return 'event:' + self.event_id

    def json(self):
        return {'binding': self.binding.json(), 'source': asdict(self.source), 'payload_json': self.payload_json,
                'tracking_json': self.tracking_json}

    @classmethod
    def from_json(cls, value):
        return cls(StateBinding.from_json(value['binding']), EventSource(**value['source']),
                   value['payload_json'], value['tracking_json'])


@dataclass(frozen=True)
class PendingEvent:
    command: EventProjection
    original_cut: StateCut
    owner: OwnerToken


@dataclass(frozen=True)
class EventReceipt:
    binding: StateBinding
    event_id: str
    replayed: bool
    atomic_scope: AtomicScope = AtomicScope.RECOVERABLE_SAGA


@dataclass(frozen=True)
class ConnectorHistory:
    source: EventSource
    trade_ids: tuple[tuple[str, str, str], ...]
    exchange_ids: tuple[tuple[str, str], ...]
    tracking_json: str  # Diagnostic only; never restore an SDK tracker from SQL.


class EventRepository(Protocol):
    async def connector_history(self, source: EventSource, binding: StateBinding) -> ConnectorHistory:
        ...

    async def event_origin(self, event_id: str, binding: StateBinding) -> PendingEvent | None:
        ...

    async def pending_events(self, binding: StateBinding) -> tuple[PendingEvent, ...]:
        ...

    async def project_event(self, command: EventProjection, *, cut: StateCut, owner: OwnerToken) -> EventReceipt:
        ...


def record_json(model, row):
    if row is None:
        return None
    value = {}
    for column in model.__table__.columns:
        if column.name == 'id' and model in (OrderStatus, MarketState):
            continue
        item = getattr(row, column.name)
        value[column.name] = str(item) if isinstance(item, (Decimal, float)) else item
    return json_text(value)


def sql_record(model, values):
    result = dict(values)
    for column in model.__table__.columns:
        key = column.name
        if key not in result or result[key] is None:
            continue
        if isinstance(column.type, SqliteDecimal):
            result[key] = sql_value(Decimal(result[key]))
        elif isinstance(column.type, Float):
            result[key] = float(result[key])
        elif isinstance(column.type, Integer):
            result[key] = int(result[key])
        elif isinstance(column.type, JSON):
            result[key] = json.loads(json_text(result[key]))
    return result


def select_row(session, descriptor):
    model = TABLES[descriptor['table']]
    return session.query(model).filter_by(**descriptor['key']).one_or_none()


def persist_event(recorder, effect_json):
    """Only detached effects/SQL models touch the owning worker's DB session."""
    if recorder.sql_manager.engine.dialect.name != 'sqlite':
        raise StorageError('RECORDER_FILE_SQLITE_REQUIRED')
    effects = json.loads(effect_json)
    with recorder.sql_manager.get_new_session() as session:
        session.connection().exec_driver_sql('BEGIN IMMEDIATE')
        actual_rows = [select_row(session, effect) for effect in effects]
        actual = tuple(record_json(TABLES[item['table']], row) for item, row in zip(effects, actual_rows))
        if actual == tuple(item['after'] for item in effects):
            return
        if actual != tuple(item['before'] for item in effects):
            raise StorageConflict('RECORDER_EVENT_SQL_CONFLICT')
        for effect, row in zip(effects, actual_rows):
            model = TABLES[effect['table']]
            if effect['before'] == effect['after']:
                continue
            values = sql_record(model, json.loads(effect['after']))
            if row is None:
                session.add(model(**values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)
        session.commit()


class FileEventAdapter:
    def _event_origin(self, event_id):
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import digest
        digest(event_id)
        meta = self.store._read()
        self._verify_binding(meta)
        row = meta.resources.get('recorder_events', {}).get(event_id)
        if row is None:
            return None
        command = EventProjection.from_json(thaw(row['command']))
        cut, owner = StateCut.from_json(thaw(row['cut'])), OwnerToken.from_json(thaw(row['owner']))
        if (command.binding != self.binding or command.source.controller_id != self.controller_id
                or command.event_id != event_id or row['state'] not in ('PROJECTING', 'APPLIED')
                or row['identity'] != fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})):
            raise StorageUnavailable('RECORDER_EVENT_ORIGIN_UNVERIFIED')
        return PendingEvent(command, cut, owner)

    async def event_origin(self, event_id, binding):
        self.store._assert_binding(binding)
        return await self.store._call(self._event_origin, event_id)

    def _pending_events(self):
        meta = self.store._read()
        return tuple(self._event_origin(key) for key, row in meta.resources.get('recorder_events', {}).items()
                     if row['state'] != 'APPLIED')

    async def pending_events(self, binding):
        self.store._assert_binding(binding)
        return await self.store._call(self._pending_events)

    def _known_wires(self, source):
        from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
        from hummingbot.strategy_v2.models.executors_info import ExecutorInfo
        known = set()
        meta = self.store._read()
        for row in meta.resources.get('recorder_outbox', {}).values():
            value = thaw(row['command'])
            projection = ExecutorProjection(value['operation_id'], StateBinding.from_json(value['binding']), value['payload_json'])
            if projection.binding != self.binding:
                raise StorageError('RECORDER_EVENT_WIRE_UNVERIFIED')
            info = ExecutorInfo.model_validate_json(projection.payload_json)
            if info.controller_id == source.controller_id and info.config.connector_name == source.market:
                head = meta.resources.get('recorder_progress_heads', {}).get(info.id)
                info = info if head is None else ExecutorInfo.model_validate_json(head['payload_json'])
                if info.custom_info.get('life_storage_binding') != self.binding.json():
                    raise StorageError('RECORDER_EVENT_WIRE_UNVERIFIED')
                wires = info.custom_info.get('recovery_order_ids')
                if type(wires) is not list or len(set(wires)) != len(wires):
                    raise StorageError('RECORDER_EVENT_WIRE_UNVERIFIED')
                for wire in wires:
                    identifier(wire)
                known.update(wires)
        if source.market == 'okx' and hasattr(self.store, '_wal'):
            known.update(row.client_order_id for row in self.store._wal.all_records())
        return known

    def _validate_event(self, command):
        if (command.source.controller_id != self.controller_id
                or command.source.config_file_path != self._recorder.config_file_path
                or command.source.strategy_name != self._recorder.strategy_name):
            raise StorageError('RECORDER_EVENT_SOURCE_MISMATCH')
        body = json.loads(command.payload_json)
        known = self._known_wires(command.source)
        if ('wire' in body and body['wire'] not in known
                or not set(json.loads(command.tracking_json)) <= known):
            raise StorageError('RECORDER_EVENT_WIRE_UNVERIFIED')

    def _effects(self, command, meta):
        body, source = json.loads(command.payload_json), command.source
        rows = thaw(meta.resources.get('recorder_event_rows', {}))
        effects = []

        def add(table, key, values):
            descriptor = {'table': table, 'key': key}
            row_key = fingerprint(descriptor)
            after = record_json(TABLES[table], TABLES[table](**sql_record(TABLES[table], values)))
            effects.append({**descriptor, 'row_key': row_key, 'before': rows.get(row_key), 'after': after})

        common = {'config_file_path': source.config_file_path, 'market': source.market}
        wire = body.get('wire')
        order_key = fingerprint({'table': 'order', 'key': {'id': wire}})
        previous = rows.get(order_key)
        latest = thaw(meta.resources.get('recorder_event_status_heads', {})).get(source.market + ':' + str(wire))
        current_status = {'timestamp_ms': body['timestamp_ms'], 'status': body['kind']}
        if latest is not None and latest['timestamp_ms'] >= body['timestamp_ms']:
            current_status = latest
        if body['kind'] in CREATES:
            add('order', {'id': wire}, {**common, 'id': wire, 'strategy': source.strategy_name, 'symbol': body['pair'],
                'base_asset': 'LIFE', 'quote_asset': 'USDT', 'creation_timestamp': body['timestamp_ms'],
                                        'order_type': body['order_type'], 'amount': body['amount'], 'price': body['price'], 'leverage': body['leverage'],
                                        'position': body['position'], 'exchange_order_id': body['exchange_id'], 'last_status': current_status['status'],
                                        'last_update_timestamp': current_status['timestamp_ms']})
        elif wire is not None and previous is not None:
            values = json.loads(previous)
            values.update(last_status=current_status['status'], last_update_timestamp=current_status['timestamp_ms'])
            add('order', {'id': wire}, values)
        elif wire is not None:
            # Check for an unscoped legacy Order even when this event creates
            # only a fill/status row. It cannot be implicitly adopted.
            effects.append({'table': 'order', 'key': {'id': wire}, 'row_key': order_key, 'before': None, 'after': None})
        if wire is not None:
            key = {'order_id': wire, 'timestamp': body['timestamp_ms'], 'status': body['kind']}
            add('status', key, key)
        if body['kind'] == 'OrderFilled':
            key = {'market': source.market, 'order_id': wire, 'exchange_trade_id': body['trade_id']}
            add('trade', key, {**key, 'config_file_path': source.config_file_path, 'strategy': source.strategy_name,
                'symbol': body['pair'], 'base_asset': 'LIFE', 'quote_asset': 'USDT', 'timestamp': body['timestamp_ms'],
                               'trade_type': body['side'], 'order_type': body['order_type'], 'price': body['price'], 'amount': body['amount'],
                               'leverage': body['leverage'], 'position': body['position'], 'trade_fee': json.loads(body['fee_json']),
                               'trade_fee_in_quote': body['fee_quote']})
        if body['kind'] == 'FundingPaymentCompleted':
            add('funding', {'timestamp': body['timestamp_ms']}, {**common, 'timestamp': body['timestamp_ms'],
                'symbol': body['pair'], 'rate': body['rate'], 'amount': body['amount']})
        previous_state = rows.get(fingerprint({'table': 'state', 'key': common}))
        state_time = max(body['timestamp_ms'], 0 if previous_state is None else json.loads(previous_state)['timestamp'])
        add('state', common, {**common, 'timestamp': state_time, 'saved_state': json.loads(command.tracking_json)})
        return json_text(effects), current_status

    def _event_rows(self, effect_json):
        with self._recorder.sql_manager.get_new_session() as session:
            return tuple(record_json(TABLES[item['table']], select_row(session, item)) for item in json.loads(effect_json))

    def _project_event(self, command, cut, owner):
        self.store._assert_binding(command.binding)
        self.store._assert_binding(cut.binding)
        meta = self.store._read()
        self._verify_binding(meta)
        self._validate_event(command)
        now = utc(self.store._clock())
        if (meta.ownership.token != owner or meta.ownership.mode not in (OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY)
                or now < owner.valid_from):
            raise StorageError('RECORDER_EVENT_OWNER_INVALID')
        operations = thaw(meta.resources.get('recorder_events', {}))
        previous = operations.get(command.event_id)
        identity = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        if previous is not None:
            self._event_origin(command.event_id)
            if previous['identity'] != identity:
                raise StorageConflict('RECORDER_EVENT_OPERATION_CONFLICT')
            if previous['state'] == 'APPLIED':
                return EventReceipt(self.binding, command.event_id, True)
            effect_json, status = previous['effect_json'], previous['status']
            if (effect_json, status) != self._effects(command, meta):
                raise StorageUnavailable('RECORDER_EVENT_EFFECT_UNVERIFIED')
        else:
            if meta.cut != cut:
                raise StorageConflict('STORAGE_CUT_STALE')
            if any(row['state'] != 'APPLIED' for name in ('recorder_outbox', 'recorder_progress', 'recorder_positions', 'recorder_events')
                   for row in meta.resources.get(name, {}).values()):
                raise StorageUnavailable('RECORDER_OUTBOX_REPAIR_REQUIRED')
            effect_json, status = self._effects(command, meta)
            if self._event_rows(effect_json) != tuple(item['before'] for item in json.loads(effect_json)):
                raise StorageUnavailable('RECORDER_EVENT_NATIVE_UNVERIFIED')
            operations[command.event_id] = {'identity': identity, 'command': command.json(), 'cut': cut.json(),
                                            'owner': owner.json(), 'state': 'PROJECTING', 'effect_json': effect_json, 'status': status}
            self.store._commit(meta.cut, owner, 'event-start:' + command.event_id,
                               (('recorder_events', encode(operations)), ('recorder_binding', encode(self._identity))), observation=True)
        try:
            self._recorder.project_market_event(effect_json)
            effects = json.loads(effect_json)
            if self._event_rows(effect_json) != tuple(item['after'] for item in effects):
                raise StorageUnavailable('RECORDER_EVENT_NATIVE_POSTCONDITION_FAILED')
            rows = thaw(meta.resources.get('recorder_event_rows', {}))
            rows.update({item['row_key']: item['after'] for item in effects})
            heads = thaw(meta.resources.get('recorder_event_status_heads', {}))
            wire = json.loads(command.payload_json).get('wire')
            if wire is not None:
                heads[command.source.market + ':' + wire] = status
            operations[command.event_id]['state'] = 'APPLIED'
            latest = self.store._read()
            self.store._commit(latest.cut, owner, 'event-done:' + command.event_id,
                               (('recorder_events', encode(operations)), ('recorder_event_rows', encode(rows)),
                                ('recorder_event_status_heads', encode(heads))), observation=True)
            return EventReceipt(self.binding, command.event_id, previous is not None)
        except BaseException:
            self.store._uncertain = True
            raise StorageUnavailable('RECORDER_EVENT_UNKNOWN') from None

    async def project_event(self, command, *, cut, owner):
        if not isinstance(command, EventProjection) or not isinstance(cut, StateCut) or not isinstance(owner, OwnerToken):
            raise StorageError('RECORDER_EVENT_INVALID')
        try:
            return await self.store._call(self._project_event, command, cut, owner)
        except asyncio.CancelledError:
            self.store._uncertain = True
            raise

    def _connector_history(self, source):
        meta = self.store._read()
        self._verify_binding(meta)
        if (source.controller_id != self.controller_id or source.config_file_path != self._recorder.config_file_path
                or source.strategy_name != self._recorder.strategy_name):
            raise StorageError('RECORDER_EVENT_SOURCE_MISMATCH')
        heads = meta.resources.get('recorder_event_rows', {})
        descriptors = {}
        for key, row in meta.resources.get('recorder_events', {}).items():
            origin = self._event_origin(key)
            if row['state'] != 'APPLIED':
                raise StorageUnavailable('RECORDER_OUTBOX_REPAIR_REQUIRED')
            if origin.command.source == source:
                for effect in json.loads(row['effect_json']):
                    descriptors[effect['row_key']] = effect
        trade_ids, exchange_ids, tracking = [], [], '{}'
        with self._recorder.sql_manager.get_new_session() as session:
            for key, descriptor in descriptors.items():
                model = TABLES[descriptor['table']]
                raw = record_json(model, select_row(session, descriptor))
                if raw != heads.get(key):
                    raise StorageUnavailable('RECORDER_EVENT_NATIVE_UNVERIFIED')
                if raw is None:
                    continue
                value = json.loads(raw)
                if descriptor['table'] == 'trade':
                    trade_ids.append((value['market'], value['exchange_trade_id'], value['symbol']))
                elif descriptor['table'] == 'order' and value['exchange_order_id']:
                    exchange_ids.append((value['exchange_order_id'], value['id']))
                elif descriptor['table'] == 'state':
                    tracking = json_text(value['saved_state'])
            # Existing rows from legacy or another lineage are not imported by
            # matching a config filename. Require a separately qualified import.
            for table in ('order', 'trade', 'funding', 'state'):
                model = TABLES[table]
                known = {heads[key] for key, descriptor in descriptors.items() if descriptor['table'] == table}
                for row in session.query(model).filter_by(config_file_path=source.config_file_path, market=source.market).all():
                    if record_json(model, row) not in known:
                        raise StorageUnavailable('RECORDER_EVENT_IMPORT_REQUIRED')
        return ConnectorHistory(source, tuple(sorted(set(trade_ids))), tuple(sorted(set(exchange_ids))), tracking)

    async def connector_history(self, source, binding):
        if not isinstance(source, EventSource):
            raise StorageError('RECORDER_EVENT_SOURCE_INVALID')
        self.store._assert_binding(binding)
        return await self.store._call(self._connector_history, source)
