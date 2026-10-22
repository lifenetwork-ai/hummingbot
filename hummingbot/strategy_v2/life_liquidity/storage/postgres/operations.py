"""Bounded native operational reports. A report never grants recovery or sends.

Read the immutable original event bytes, not rounded legacy ORM projections.
One primary statement binds integrity, pending work, account state and rows.
No session, credential or mutable financial object escapes the runtime.
"""
import asyncio
import json
import math
import re
from dataclasses import asdict, dataclass, field

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
from hummingbot.strategy_v2.life_liquidity.storage.recorder_configuration import ConfigurationReport
from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import EventProjection
from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import PositionReport


@dataclass(frozen=True)
class PgOperationalReport:
    controller_id: str
    configured_account_uid: str
    backend_healthy: bool
    reporting_ready: bool
    reason: str | None
    revision: int | None = None
    financial_recovery_required: bool | None = None
    executor_json: tuple[str, ...] = ()
    position_json: tuple[str, ...] = ()
    trade_json: tuple[str, ...] = ()
    authenticated_account_uid: None = field(default=None, init=False)
    trading_permission: bool = field(default=False, init=False)

    @property
    def reporting_complete(self):
        return self.reason is None

    @property
    def trades(self):
        return sorted((json.loads(value) for value in self.trade_json),
                      key=lambda t: (t['timestamp_ms'], t['market'], t['trade_id']))

    def format_status(self):
        return (f'PostgreSQL reporting only [{self.controller_id}]: '
                f'{"verified view" if self.reporting_complete else "incomplete: " + self.reason}; '
                f'backend admission healthy={self.backend_healthy}; reporting ready={self.reporting_ready}; '
                f'financial recovery required={self.financial_recovery_required}; '
                f'configured UID={self.configured_account_uid} '
                '(authentication not established by reporting); this report grants no trading permission')


class PgRecorderOperations:
    def __init__(self, app):
        if type(app) is not PgRecorderApplication or app.controller.config.storage_binding != app.binding:
            raise StorageError('PG_HISTORY_ASSEMBLY_REQUIRED')
        self.app = app

    def _report(self, reason, **kwargs):
        app = self.app
        return PgOperationalReport(app.repository.controller_id, app.binding.account_uid,
                                   app.financial._runtime.healthy, app.ready, reason, **kwargs)

    async def snapshot(self, *, start_ms, timeout_s):
        if (type(start_ms) is not int or not 0 <= start_ms < 2 ** 63
                or type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 60):
            raise StorageError('PG_HISTORY_QUERY_INVALID')
        app = self.app
        if getattr(app, '_operations_task', None) is not None:
            return self._report('PG_HISTORY_BACKPRESSURE')
        task = asyncio.create_task(self._read(start_ms))
        app._operations_task = task

        def finished(done):
            if app._operations_task is done:
                app._operations_task = None
            if not done.cancelled():
                done.exception()
        task.add_done_callback(finished)
        try:
            async with asyncio.timeout(timeout_s):
                # Cancellation/deadline retains the single original read slot
                # until physical completion; repeated CLI calls cannot fan out.
                return await asyncio.shield(task)
        except TimeoutError:
            return self._report('PG_HISTORY_TIMEOUT')
        except StorageError as error:
            code = str(error)
            return self._report(code if re.fullmatch(r'[A-Z][A-Z0-9_]{0,95}', code) else 'PG_HISTORY_UNAVAILABLE')
        except Exception:
            return self._report('PG_HISTORY_UNAVAILABLE')

    async def _read(self, start_ms):
        app, repo = self.app, self.app.repository

        async def read(c):
            return await c.fetchrow('''WITH integrity AS MATERIALIZED (
                    SELECT life_financial.recorder_executor_verified($1) AND life_financial.recorder_scope_verified($1,$2,$7) AS verified),
                selected AS MATERIALIZED (
                    SELECT DISTINCT ON(o.stream) o.projection_id,o.stream,o.sequence,
                        octet_length(o.body::text) AS bytes
                    FROM life_financial.recorder_outbox o
                    JOIN life_financial.projection_receipts r USING(account_id,projection_id)
                    WHERE o.account_id=$1 AND CASE WHEN (SELECT verified FROM integrity) THEN
                        CASE o.body->>'subtype'
                          WHEN 'INITIAL' THEN (o.body#>>'{command,payload_json}')::jsonb->>'controller_id'
                          WHEN 'PROGRESS' THEN (o.body#>>'{command,payload_json}')::jsonb->>'controller_id'
                          WHEN 'EVENT' THEN o.body#>>'{command,source,controller_id}'
                          ELSE o.body#>>'{command,controller_id}' END END=$2
                    AND CASE WHEN (SELECT verified FROM integrity) AND o.body->>'subtype'='EVENT'
                        THEN ((o.body#>>'{command,payload_json}')::jsonb->>'timestamp_ms')::bigint >= $6
                        ELSE true END
                    ORDER BY o.stream,o.sequence DESC LIMIT $3),
                stats AS MATERIALIZED (SELECT count(*) AS count,COALESCE(sum(bytes),0)+2*count(*)+2 AS bytes FROM selected),
                state AS MATERIALIZED (SELECT CASE WHEN verified THEN life_financial.spot_state($1) END AS value FROM integrity)
                SELECT integrity.verified,stats.count,stats.bytes,
                    value#>>'{account,version}' AS revision,value->>'unverified' AS unverified,
                    value->>'recovery_required' AS recovery_required,
                    EXISTS(SELECT 1 FROM life_financial.recorder_outbox o
                        LEFT JOIN life_financial.projection_receipts r USING(account_id,projection_id)
                        WHERE o.account_id=$1 AND r.projection_id IS NULL) AS pending,
                    CASE WHEN integrity.verified AND stats.count<=$4 AND stats.bytes<=$5 THEN
                        COALESCE((SELECT jsonb_agg(o.body ORDER BY s.stream,s.sequence)
                            FROM selected s JOIN life_financial.recorder_outbox o ON o.account_id=$1
                            AND o.projection_id=s.projection_id),'[]'::jsonb) ELSE '[]'::jsonb END AS rows
                FROM integrity,stats,state''',
                                    repo._runtime.config.account_id, repo.controller_id, repo.max_rows + 1,
                                    repo.max_rows, repo.max_read_bytes, start_ms, [asdict(r.source) for r in app.events])
        row = await repo._runtime._admit(read, safety=True, operation='recorder_operations')
        if not row['verified'] or row['unverified'] != 'false':
            return self._report('PG_RECORDER_UNVERIFIED')
        if row['pending']:
            return self._report('PG_RECORDER_DELIVERY_PENDING')
        if row['count'] > repo.max_rows:
            return self._report('PG_RECORDER_CAPACITY')
        if row['bytes'] > repo.max_read_bytes:
            return self._report('PG_RECORDER_BYTE_CAPACITY')
        executors, positions, trades, configuration = [], [], [], None
        for body in row['rows']:
            command, subtype = body['command'], body['subtype']
            if subtype in ('INITIAL', 'PROGRESS'):
                executors.append(ExecutorProjection(command['operation_id'], app.binding, command['payload_json']).payload_json)
            elif subtype == 'POSITION':
                positions.append(json.dumps(PositionReport.from_json(command).json(), sort_keys=True))
            elif subtype == 'CONFIGURATION':
                configuration = ConfigurationReport.from_json(command)
            elif subtype == 'EVENT':
                event = EventProjection.from_json(command)
                if event.binding != app.binding or event.source not in tuple(r.source for r in app.events):
                    return self._report('PG_RECORDER_SOURCE_UNVERIFIED')
                value = json.loads(event.payload_json)
                if value['kind'] == 'OrderFilled':
                    trades.append(json.dumps({**value, 'market': event.source.market,
                                              'controller_id': event.source.controller_id,
                                              'config_file_path': event.source.config_file_path}, sort_keys=True))
            else:
                return self._report('PG_RECORDER_SUBTYPE_UNVERIFIED')
        if configuration is None or configuration.controller_json != app.controller.config.model_dump_json():
            return self._report('PG_RECORDER_CONTROLLER_CONFIG_MISMATCH')
        if not app.ready:
            return self._report('PG_RECORDER_LOCAL_PENDING_OR_UNAVAILABLE')
        return self._report(None, revision=int(row['revision']), financial_recovery_required=row['recovery_required'] != 'false',
                            executor_json=tuple(executors), position_json=tuple(positions), trade_json=tuple(trades))


def postgres_reporting(strategy):
    """Select native routes by configured backend, never by cached DB URL."""
    controllers = getattr(strategy, 'controllers', None)
    apps = getattr(getattr(strategy, 'executor_orchestrator', None), '_repository_reporting', None)
    original_native = (type(apps) is dict and any(type(v) is PgRecorderApplication for v in apps.values())
                       or getattr(getattr(getattr(strategy, 'service', None), 'config', None), 'storage_backend', None) == 'postgres')
    if type(controllers) is not dict:
        if original_native:
            raise StorageError('PG_HISTORY_ASSEMBLY_REQUIRED')
        return None
    native = {key: c for key, c in controllers.items() if getattr(getattr(c, 'config', None), 'storage_backend', None) == 'postgres'}
    if not native:
        if original_native:
            raise StorageError('PG_HISTORY_ASSEMBLY_REQUIRED')
        return None
    if (type(apps) is not dict or set(native) != set(controllers) or set(apps) != set(native)
            or any(type(apps[key]) is not PgRecorderApplication or apps[key].controller is not c
                   or apps[key].binding != c.config.storage_binding for key, c in native.items())):
        raise StorageError('PG_HISTORY_ASSEMBLY_REQUIRED')
    # The current configured native service has one controller per account.
    # Do not merge account/lineage cuts into a misleading single history view.
    if len(apps) != 1:
        raise StorageError('PG_HISTORY_SINGLE_ACCOUNT_REQUIRED')
    return PgRecorderOperations(next(iter(apps.values())))
