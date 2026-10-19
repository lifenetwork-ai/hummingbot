"""Process-local joint-source consumption, never recoverable from raw diagnostics."""
import json
from datetime import timedelta

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import (
    JointSourceReport,
    PgJointAccountReconciler,
)


class JointSourceFence:
    def __init__(self, collector, report, *, operation_id, intent_id):
        if type(collector) is not PgJointAccountReconciler or type(report) is not JointSourceReport or report is not collector._report:
            raise StorageError('PG_JOINT_SOURCE_REPORT_REVOKED')
        collector.check_report(report, epoch=collector._epoch)
        if report.runner_executor_ids:
            raise StorageError('PG_JOINT_SOURCE_RUNNER_NOT_QUIESCENT')
        self.collector, self.report = collector, report
        self.operation_id, self.intent_id = operation_id, intent_id
        self.epoch, self.authorized, self.rejected_before_commit = collector._epoch + 1, None, False
        self._request = None

    def authorize(self, runtime, query, args, owner):
        positions = {
            'SELECT life_financial.spot_order_command($1,$2,$3,$4,$5,$6,$7,$8,$9)': 7,
            'SELECT life_financial.swap_terminal_command($1,$2,$3,$4,$5,$6,$7)': 6,
            'SELECT life_financial.shared_release_command($1,$2,$3,$4,$5,$6,$7)': 6,
        }
        version, digest = self.collector.financial._cut(self.report.cut, owner)
        if (runtime is not self.collector.runtime or owner != self.collector.swap.owner or query not in positions
                or tuple(args[3:5]) != (version, digest) or args[2] != owner.json()
                or args[1] != self.collector.binding.json() or args[0] != runtime.config.account_id
                or args[positions[query]].get('operation_id') != self.operation_id
                or positions[query] == 7 and (args[6] != 'SPOT_TERMINAL' or args[8] is not False)):
            raise StorageError('PG_JOINT_SOURCE_CONSUMER_INVALID')
        identity = fingerprint([query, str(args[0]), *args[1:]])
        if self.authorized not in (None, identity):
            raise StorageError('PG_JOINT_SOURCE_CONSUMER_INVALID')
        self.authorized = identity
        self._request = (query, args)
        native = next((json.loads(v.native_json) for v in self.report.orders
                       if json.loads(v.native_json)['intent_id'] == self.intent_id), None)
        body = args[positions[query]]
        if (native is None or body.get('intent_id', self.intent_id) != self.intent_id
                or 'prepare_operation_id' in body and body['prepare_operation_id'] != native['prepare_operation_id']):
            raise StorageError('PG_JOINT_SOURCE_CONSUMER_INVALID')
        self.check()

    def check(self):
        self.collector.check_report(self.report, epoch=self.epoch)
        if self.authorized is None or self.collector.runtime._observation_inflight != 1:
            raise StorageError('PG_JOINT_SOURCE_CONSUMER_INVALID')
        query, args = self._request
        if fingerprint([query, str(args[0]), *args[1:]]) != self.authorized:
            raise StorageError('PG_JOINT_SOURCE_CONSUMER_INVALID')

    @property
    def deadline(self):
        return dict(operation_id=self.operation_id, collection_id=self.report.collection_id, source_hash=self.report.source_hash,
                    observed_at=self.report.observed_at.isoformat(), max_age_ms=self.collector.max_age_ms,
                    expires_at=(self.report.observed_at + timedelta(milliseconds=self.collector.max_age_ms)).isoformat())

    def transaction(self, original):
        fence = self

        class CheckedCommit:
            async def commit(self):
                try:
                    fence.check()
                except BaseException:
                    fence.rejected_before_commit = True
                    raise
                return await original.commit()
        return CheckedCommit()
