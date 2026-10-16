"""Immutable executor reporting outbox with bounded read results, no send grant."""
import json
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
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection, RecorderRow
from hummingbot.strategy_v2.life_liquidity.storage.recorder_progress import ExecutorProgress


@dataclass(frozen=True)
class RecorderDeliveryReceipt:
    projection_id: str
    executor_id: str
    sequence: int
    state: str
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class PendingRecorderDelivery:
    command: ExecutorProjection | ExecutorProgress
    original_cut: FinancialCut
    owner: OwnerToken


class PgRecorderRepository:
    def __init__(self, financial, *, controller_id, max_rows, max_read_bytes):
        if (type(financial) is not PgSpotRepository or type(max_rows) is not int or not 0 < max_rows <= 4096
                or type(max_read_bytes) is not int or not 0 < max_read_bytes <= 16 * 1024 * 1024):
            raise StorageError('PG_RECORDER_CONFIG_INVALID')
        identifier(controller_id)
        self.financial, self.binding = financial, financial.binding
        self._runtime, self.controller_id, self.max_rows = financial._runtime, controller_id, max_rows
        self.max_read_bytes = max_read_bytes

    @staticmethod
    def _receipt(value):
        if value.get('trading_permission') is not False or value.get('kind') not in ('RECORDER_STAGE', 'RECORDER_APPLY'):
            raise StorageUnavailable('PG_RECORDER_RECEIPT_INVALID')
        return RecorderDeliveryReceipt(value['projection_id'], value['executor_id'], value['sequence'],
                                       value['state'], value['replayed'])

    async def stage(self, command, *, cut, owner):
        if (type(command) not in (ExecutorProjection, ExecutorProgress) or command.binding != self.binding
                or json.loads(command.payload_json)['controller_id'] != self.controller_id):
            raise StorageError('PG_RECORDER_COMMAND_INVALID')
        version, digest = self.financial._cut(cut, owner)
        subtype = 'INITIAL' if type(command) is ExecutorProjection else 'PROGRESS'
        identity = fingerprint({'kind': 'RECORDER_STAGE', 'subtype': subtype, 'command': command.json(),
                                'cut': cut.json(), 'owner': owner.json()})
        args = (self._runtime.config.account_id, self.binding.json(), owner.json(), version, digest, identity,
                subtype, command.json())
        return await self._runtime._transaction(
            'SELECT life_financial.recorder_stage_executor($1,$2,$3,$4,$5,$6,$7,$8)',
            args, owner, self._receipt, observation=True)

    async def apply(self, projection_id, *, cut, owner):
        identifier(projection_id)
        version, digest = self.financial._cut(cut, owner)
        args = (self._runtime.config.account_id, self.binding.json(), owner.json(), version, digest, projection_id)
        return await self._runtime._transaction(
            'SELECT life_financial.recorder_apply_executor($1,$2,$3,$4,$5,$6)',
            args, owner, self._receipt, observation=True)

    async def _read(self, binding, pending):
        self.financial._binding(binding)

        async def read(c):
            # One primary statement snapshot: proof, pending barrier and rows
            # cannot observe different commits of the reporting dispatcher.
            # Materialize only IDs/lengths until both capacity checks pass.
            # Counting rows alone would allow multi-gigabyte JSON aggregation.
            columns = "o.projection_id,o.stream,o.sequence,octet_length(to_jsonb(o)::text) AS bytes"
            select = (
                "SELECT " + columns + """ FROM life_financial.recorder_outbox o
                LEFT JOIN life_financial.projection_receipts r USING(account_id,projection_id)
                WHERE o.account_id=$1 AND CASE WHEN (SELECT verified FROM integrity) THEN
                    ((o.body#>>'{command,payload_json}')::jsonb)->>'controller_id' END=$2
                    AND r.projection_id IS NULL ORDER BY o.stream,o.sequence LIMIT $3""" if pending else
                "SELECT DISTINCT ON(o.stream) " + columns + """ FROM life_financial.recorder_outbox o
                JOIN life_financial.projection_receipts r USING(account_id,projection_id)
                WHERE o.account_id=$1 AND CASE WHEN (SELECT verified FROM integrity) THEN
                    ((o.body#>>'{command,payload_json}')::jsonb)->>'controller_id' END=$2
                ORDER BY o.stream,o.sequence DESC LIMIT $3""")
            result = await c.fetchrow(
                """WITH integrity AS MATERIALIZED (
                    SELECT life_financial.recorder_executor_verified($1) AS verified),
                selected AS MATERIALIZED (""" + select + """),
                stats AS MATERIALIZED (SELECT count(*) AS count,COALESCE(sum(bytes),0)+2*count(*)+2 AS bytes FROM selected)
                SELECT integrity.verified,stats.count,stats.bytes,
                    EXISTS(SELECT 1 FROM life_financial.recorder_outbox o
                        LEFT JOIN life_financial.projection_receipts r USING(account_id,projection_id)
                        WHERE o.account_id=$1 AND r.projection_id IS NULL) AS pending,
                    CASE WHEN integrity.verified AND stats.count<=$4 AND stats.bytes<=$5 THEN
                        COALESCE((SELECT jsonb_agg(to_jsonb(o) ORDER BY s.stream,s.sequence)
                            FROM selected s JOIN life_financial.recorder_outbox o ON o.account_id=$1
                                AND o.projection_id=s.projection_id),'[]'::jsonb)
                        ELSE '[]'::jsonb END AS rows FROM integrity,stats""",
                self._runtime.config.account_id, self.controller_id, self.max_rows + 1, self.max_rows, self.max_read_bytes)
            if not result['verified']:
                raise StorageUnavailable('PG_RECORDER_UNVERIFIED')
            if not pending and result['pending']:
                raise StorageUnavailable('PG_RECORDER_DELIVERY_PENDING')
            if result['count'] > self.max_rows:
                raise StorageUnavailable('PG_RECORDER_CAPACITY')
            if result['bytes'] > self.max_read_bytes:
                raise StorageUnavailable('PG_RECORDER_BYTE_CAPACITY')
            return result['rows']
        rows = await self._runtime._admit(read, safety=True)
        if len(rows) > self.max_rows:
            raise StorageUnavailable('PG_RECORDER_CAPACITY')
        return rows

    async def pending(self, binding):
        rows = await self._read(binding, True)
        result = []
        for row in rows:
            body, value = row['body'], row['body']['command']
            command = (ExecutorProjection(value['operation_id'], binding, value['payload_json']) if body['subtype'] == 'INITIAL'
                       else ExecutorProgress.from_json(value))
            version = body['version']
            cut = StateCut(binding, version, version, (ResourceVersion('account', version),), body['cut'])
            result.append(PendingRecorderDelivery(command, FinancialCut(cut, (('postgres_account', body['cut']),)),
                                                  OwnerToken.from_json(body['owner'])))
        return tuple(result)

    async def read(self, binding):
        return tuple(RecorderRow(json.loads(row['body']['command']['payload_json'])['id'],
                                 row['body']['command']['payload_json']) for row in await self._read(binding, False))

    async def project(self, command, *, owner):
        await self.stage(command, cut=(await self.financial.snapshot(self.binding)).cut, owner=owner)
        return await self.apply(command.operation_id, cut=(await self.financial.snapshot(self.binding)).cut, owner=owner)
