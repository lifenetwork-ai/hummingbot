"""Bounded, read-only reporting ancestry on one SQLite read transaction.

All rows retain their original namespace and SQL primary keys. They are a
reporting projection, never balances, trade evidence or activation authority.
"""
import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal

from hummingbot.model.controllers import Controllers
from hummingbot.model.executors import Executors
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.position import Position
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    StateBinding,
    StateCut,
    StorageError,
    StorageUnavailable,
    digest,
    fingerprint,
    identifier,
    thaw,
)
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import (
    TABLES,
    EventProjection,
    EventSource,
    json_text,
    record_json,
)
from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import PositionReport, position_values
from hummingbot.strategy_v2.life_liquidity.storage.recorder_progress import (
    ExecutorProgress,
    progress_transition,
    same_reporting,
)


def capture_limits(max_bytes, max_rows):
    if any(type(v) is not int or v < 1 for v in (max_bytes, max_rows)):
        raise StorageError('HANDOFF_CAPTURE_LIMITS_INVALID')


@dataclass(frozen=True)
class RecorderSource:
    cut: StateCut
    controller_id: str
    rows_json: str

    def __post_init__(self):
        try:
            if type(self.cut) is not StateCut or type(self.rows_json) is not str:
                raise ValueError()
            identifier(self.controller_id)
            value = json.loads(self.rows_json)
            identity, tables = value['identity'], value['tables']
            if type(identity) is not dict or type(tables) is not dict or type(value.get('schema')) is not dict:
                raise ValueError()
            digest(identity['database_sha256'])
            expected = {m.__tablename__ for m in (Executors, Position, Controllers, *TABLES.values())}
            if (set(value) != {'identity', 'tables', 'schema'} or identity['controller_id'] != self.controller_id
                    or identity['backend'] != 'sqlite' or set(tables) != expected
                    or set(value['schema']) != expected
                    or any(type(rows) is not list or any(type(r) is not dict for r in rows) for rows in tables.values())):
                raise ValueError()
            json_text(value)  # Reject nonfinite JSON; shape checks are not provenance.
        except (StorageError, ValueError, TypeError, KeyError):
            raise StorageError('RECORDER_SOURCE_INVALID') from None

    @property
    def content_digest(self):
        return fingerprint({'cut': self.cut.json(), 'controller_id': self.controller_id,
                            'rows_sha256': hashlib.sha256(self.rows_json.encode()).hexdigest()})


def row_values(model, row):
    return {column.name: (str(value) if isinstance(value, (Decimal, float)) else value)
            for column in model.__table__.columns for value in (getattr(row, column.name),)}


class FileRecorderSourceAdapter:
    def _source_origin(self, row, command, key, prefix, ledger):
        cut, owner = StateCut.from_json(thaw(row['cut'])), OwnerToken.from_json(thaw(row['owner']))
        inherited = (command.binding != self.binding and command.binding == cut.binding and getattr(self.store, '_inherited_record', lambda *args: False)(
            {'recorder': 'recorder_outbox', 'report': 'recorder_progress', 'position': 'recorder_positions', 'event': 'recorder_events'}[prefix], key, row))
        if (row['state'] != 'APPLIED' or (command.binding != self.binding or cut.binding != self.binding) and not inherited
                or row['identity'] != fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
                or ledger[prefix + '-start:' + key]['revision'] != cut.revision + 1
                or ledger[prefix + '-done:' + key]['revision'] <= cut.revision + 1):
            raise StorageUnavailable('RECORDER_SOURCE_ORIGIN_UNVERIFIED')

    def _source_executors(self, meta, ledger):
        from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
        expected = {}
        for key, row in sorted(meta.resources.get('recorder_outbox', {}).items(), key=lambda item: item[1]['cut']['revision']):
            value = thaw(row['command'])
            command = ExecutorProjection(value['operation_id'], StateBinding.from_json(value['binding']), value['payload_json'])
            self._source_origin(row, command, key, 'recorder', ledger)
            if command.operation_id != key or json.loads(command.payload_json)['controller_id'] != self.controller_id:
                raise StorageUnavailable('RECORDER_SOURCE_ORIGIN_UNVERIFIED')
            previous = expected.setdefault(command.executor_id, command.payload_json)
            a, b = json.loads(previous), json.loads(command.payload_json)
            if any(a[k] != b[k] for k in ('id', 'config', 'controller_id', 'type', 'timestamp')):
                raise StorageUnavailable('RECORDER_SOURCE_EXECUTOR_CONFLICT')
        heads = {}
        for key, row in sorted(meta.resources.get('recorder_progress', {}).items(), key=lambda item: item[1]['cut']['revision']):
            command = ExecutorProgress.from_json(thaw(row['command']))
            self._source_origin(row, command, key, 'report', ledger)
            if (command.operation_id != key or command.executor_id not in expected
                    or command.expected_sequence != heads.get(command.executor_id, {}).get('sequence', 0)):
                raise StorageUnavailable('RECORDER_SOURCE_PROGRESS_UNVERIFIED')
            progress_transition(json.loads(expected[command.executor_id]), json.loads(command.payload_json))
            expected[command.executor_id] = command.payload_json
            heads[command.executor_id] = {'sequence': command.expected_sequence + 1, 'operation_id': key}
        actual_heads = meta.resources.get('recorder_progress_heads', {})
        if heads.keys() != actual_heads.keys():
            raise StorageUnavailable('RECORDER_SOURCE_PROGRESS_UNVERIFIED')
        for key, head in heads.items():
            actual = actual_heads[key]
            if (any(head[k] != actual[k] for k in head)
                    or not same_reporting(actual['payload_json'], expected[key])):
                raise StorageUnavailable('RECORDER_SOURCE_PROGRESS_UNVERIFIED')
            expected[key] = actual['payload_json']
        return expected

    def _source_positions(self, meta, ledger):
        head = None
        for key, row in sorted(meta.resources.get('recorder_positions', {}).items(), key=lambda item: item[1]['cut']['revision']):
            command = PositionReport.from_json(thaw(row['command']))
            self._source_origin(row, command, key, 'position', ledger)
            if (command.operation_id != key or command.controller_id != self.controller_id
                    or command.expected_sequence != (0 if head is None else head.expected_sequence + 1)):
                raise StorageUnavailable('RECORDER_SOURCE_POSITION_UNVERIFIED')
            head = command
        expected = None if head is None else {'command': head.json(), 'sequence': head.expected_sequence + 1}
        if thaw(meta.resources.get('recorder_position_head')) != expected:
            raise StorageUnavailable('RECORDER_SOURCE_POSITION_UNVERIFIED')
        return self._expected_position_rows(head)

    def _source_events(self, meta, ledger):
        rows, heads, descriptors = {}, {}, {}
        for key, row in sorted(meta.resources.get('recorder_events', {}).items(), key=lambda item: item[1]['cut']['revision']):
            command = EventProjection.from_json(thaw(row['command']))
            self._source_origin(row, command, key, 'event', ledger)
            if command.event_id != key:
                raise StorageUnavailable('RECORDER_SOURCE_EVENT_UNVERIFIED')
            self._validate_event(command)
            prefix = replace(meta, resources={**thaw(meta.resources), 'recorder_event_rows': rows,
                                              'recorder_event_status_heads': heads})
            effects, status = self._effects(command, prefix)
            if effects != row['effect_json'] or status != row['status']:
                raise StorageUnavailable('RECORDER_SOURCE_EVENT_UNVERIFIED')
            for effect in json.loads(effects):
                rows[effect['row_key']] = effect['after']
                descriptors[effect['row_key']] = effect
            wire = json.loads(command.payload_json).get('wire')
            if wire is not None:
                heads[command.source.market + ':' + wire] = status
        if rows != meta.resources.get('recorder_event_rows', {}) or heads != meta.resources.get('recorder_event_status_heads', {}):
            raise StorageUnavailable('RECORDER_SOURCE_EVENT_UNVERIFIED')
        return rows, descriptors

    def _handoff_source(self, max_bytes, max_rows):
        self.store.assert_worker_thread()
        capture_limits(max_bytes, max_rows)
        try:
            meta = self.store._read()
            self._verify_binding(meta)
            with self.store._state.inspect() as state:
                ledger = thaw(state['operations'])
            expected = self._source_executors(meta, ledger)
            positions = self._source_positions(meta, ledger)
            event_rows, descriptors = self._source_events(meta, ledger)
            config_meta, history = self._configuration_history()
            if config_meta.cut != meta.cut or any(row['state'] != 'APPLIED' for _, row, _, _ in history):
                raise StorageUnavailable('RECORDER_SOURCE_ORIGIN_UNVERIFIED')
            tables, schema, count, size = {}, {}, 0, 0
            with self._recorder.sql_manager.get_new_session() as session:
                # Explicit BEGIN is needed for a stable SQLite read transaction
                # with the driver's legacy transaction behavior. No DDL/DML.
                session.connection().exec_driver_sql('BEGIN')

                def collect(model, query):
                    nonlocal count, size
                    name = self._recorder.sql_manager.engine.dialect.identifier_preparer.quote(model.__tablename__)
                    columns = [dict(row) for row in session.connection().exec_driver_sql('PRAGMA table_info(' + name + ')').mappings()]
                    if {c['name'] for c in columns} != {c.name for c in model.__table__.columns}:
                        raise StorageUnavailable('RECORDER_SOURCE_SCHEMA_UNSUPPORTED')
                    schema[model.__tablename__] = columns
                    values = []
                    for row in query.order_by(*model.__table__.primary_key.columns).limit(max_rows - count + 1):
                        count += 1
                        value = row_values(model, row)
                        size += len(json_text(value).encode())
                        if count > max_rows or size > max_bytes:
                            raise StorageUnavailable('HANDOFF_CAPTURE_CAPACITY_EXCEEDED')
                        values.append((row, value))
                    tables[model.__tablename__] = [value for _, value in values]
                    return [row for row, _ in values]

                actual = collect(Executors, session.query(Executors).filter_by(controller_id=self.controller_id))
                if {r.id for r in actual} != expected.keys():
                    raise StorageUnavailable('RECORDER_SOURCE_EXECUTOR_UNVERIFIED')
                for row in actual:
                    projection = self._read_projection(row.to_executor_info())
                    if not same_reporting(projection.payload_json, expected[row.id]):
                        raise StorageUnavailable('RECORDER_SOURCE_EXECUTOR_UNVERIFIED')
                actual = collect(Position, session.query(Position).filter_by(controller_id=self.controller_id))
                if tuple(sorted((position_values(r) for r in actual), key=lambda r: r['id'])) != positions:
                    raise StorageUnavailable('RECORDER_SOURCE_POSITION_UNVERIFIED')
                actual = collect(Controllers, session.query(Controllers).filter_by(id=self._configuration_sql_id()))
                observed = tuple(sorted(({'id': r.id, 'timestamp': r.timestamp, 'type': r.type, 'config': r.config} for r in actual),
                                        key=lambda r: r['config']['expected_sequence']))
                if observed != self._expected_configuration_rows(history):
                    raise StorageUnavailable('CONFIG_SQL_UNVERIFIED')
                for table, model in TABLES.items():
                    query = session.query(model)
                    if model is OrderStatus:
                        wires = set()
                        for market in ('okx', 'okx_perpetual'):
                            wires |= self._known_wires(EventSource(self.controller_id, self._recorder.config_file_path,
                                                                   self._recorder.strategy_name, market))
                        query = query.filter(model.order_id.in_(sorted(wires)))
                    else:
                        query = query.filter_by(config_file_path=self._recorder.config_file_path).filter(model.market.in_(('okx', 'okx_perpetual')))
                    actual = collect(model, query)
                    wanted = [event_rows[key] for key, d in descriptors.items() if d['table'] == table and event_rows[key] is not None]
                    if sorted(record_json(model, row) for row in actual) != sorted(wanted):
                        raise StorageUnavailable('RECORDER_SOURCE_EVENT_UNVERIFIED')
            payload = json_text({'identity': self._identity, 'schema': schema, 'tables': tables})
            if len(payload.encode()) > max_bytes:
                raise StorageUnavailable('HANDOFF_CAPTURE_CAPACITY_EXCEEDED')
            if self.store._read().cut != meta.cut:
                raise StorageUnavailable('RECORDER_SOURCE_CUT_CHANGED')
            return RecorderSource(meta.cut, self.controller_id, payload)
        except StorageError:
            raise
        except (ValueError, KeyError, TypeError, OSError):
            raise StorageUnavailable('RECORDER_SOURCE_UNVERIFIED') from None

    async def handoff_source(self, binding, *, max_bytes, max_rows):
        self.store._assert_binding(binding)
        capture_limits(max_bytes, max_rows)
        return await self.store._call(self._handoff_source, max_bytes, max_rows)
