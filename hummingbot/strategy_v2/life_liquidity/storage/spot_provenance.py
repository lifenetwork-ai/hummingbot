"""Cold unsent SPOT scope from original preparation and reporting outboxes.

This proves runner provenance only. It cannot grant send/release permission or
supply financial amounts; native joint account qualification remains mandatory.
"""
import json
from datetime import datetime, timezone

from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.state import IntentRecord
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StateBinding, StateCut, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut, PrepareSpot
from hummingbot.strategy_v2.life_liquidity.storage.recorder import ExecutorProjection
from hummingbot.strategy_v2.life_liquidity.storage.recorder_progress import (
    ExecutorProgress,
    progress_transition,
    same_reporting,
)
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


def _initial(repository, resources, intent_id, owner, prepared_cut):
    candidates = []
    for row in resources.get('recorder_outbox', {}).values():
        value = row['command']
        if json.loads(value['payload_json'])['id'] != intent_id:
            continue
        command = ExecutorProjection(value['operation_id'], StateBinding.from_json(value['binding']), value['payload_json'])
        cut, writer = StateCut.from_json(row['cut']), OwnerToken.from_json(row['owner'])
        info = ExecutorInfo.model_validate_json(command.payload_json)
        if (row['state'] != 'APPLIED' or writer != owner or command.binding != repository.binding
                or cut.binding != repository.binding or row['identity'] != fingerprint({
                    'command': command.json(), 'cut': cut.json(), 'owner': writer.json()})
                or command.operation_id != 'initial:' + fingerprint({'config_json': info.config.model_dump_json()})
                or info.status != RunnableStatus.NOT_STARTED or info.is_active or info.is_trading
                or info.filled_amount_quote != 0 or info.custom_info.get('recovery_order_ids') != []):
            raise ValueError('SPOT_INITIAL_RUNNER_PROVENANCE_INVALID')
        with repository.store._state.inspect() as state:
            start = state['operations'].get('recorder-start:' + command.operation_id)
            done = state['operations'].get('recorder-done:' + command.operation_id)
            causal = (start is not None and done is not None
                      and start['revision'] == cut.revision + 1
                      and start['revision'] < done['revision'] <= prepared_cut.revision)
        if not causal:
            raise ValueError('SPOT_INITIAL_RUNNER_ORDER_INVALID')
        candidates.append(command)
    if len(candidates) != 1:
        raise ValueError('SPOT_INITIAL_RUNNER_PROVENANCE_REQUIRED')
    return candidates[0]


def _reported(repository, resources, initial, owner):
    original = ExecutorInfo.model_validate_json(initial.payload_json)
    expected = initial.payload_json
    updates = []
    for row in resources.get('recorder_progress', {}).values():
        if json.loads(row['command']['payload_json'])['id'] != original.id:
            continue
        command = ExecutorProgress.from_json(row['command'])
        cut, writer = StateCut.from_json(row['cut']), OwnerToken.from_json(row['owner'])
        if (row['state'] != 'APPLIED' or writer != owner or command.binding != repository.binding
                or cut.binding != repository.binding or row['identity'] != fingerprint({
                    'command': command.json(), 'cut': cut.json(), 'owner': writer.json()})):
            raise ValueError('SPOT_RUNNER_PROGRESS_ORIGIN_INVALID')
        updates.append(command)
    for sequence, command in enumerate(sorted(updates, key=lambda v: v.expected_sequence)):
        if command.expected_sequence != sequence:
            raise ValueError('SPOT_RUNNER_PROGRESS_SCOPE_INVALID')
        progress_transition(json.loads(expected), json.loads(command.payload_json))
        expected = command.payload_json
    head = resources.get('recorder_progress_heads', {}).get(original.id)
    if updates:
        last = max(updates, key=lambda v: v.expected_sequence)
        if (head is None or head['sequence'] != len(updates) or head['operation_id'] != last.operation_id
                or not same_reporting(head['payload_json'], expected)):
            raise ValueError('SPOT_RUNNER_PROGRESS_HEAD_INVALID')
    elif head is not None:
        raise ValueError('SPOT_RUNNER_PROGRESS_ORIGIN_REQUIRED')
    actual = repository._row_projection(original.id)
    if not same_reporting(actual.payload_json, expected):
        raise ValueError('SPOT_RUNNER_SQL_CUT_CHANGED')
    return ExecutorInfo.model_validate_json(actual.payload_json)


def prepared_runner_provenance(application, records):
    store, c = application.store, application.controller
    store.assert_worker_thread()
    try:
        runtime = c._storage_runtime
        reporting = runtime._recorder_application
        if (not records or not store._cold_unarmed or store._shared_spot is None
                or runtime._spot_application is not application or reporting is None
                or reporting.store is not store or c._runner_executors() != ()
                or store._account_lock is None or store._account_lock._fd is None):
            return False
        meta = store._read()
        if meta.ownership.token != application.owner:
            return False
        reporting.repository._verify_binding(meta)
        for record in records:
            if type(record) is not IntentRecord or record.state != 'PREPARED' or store._wal.get(record.intent_id) != record:
                return False
            originals = [row for row in meta.resources['commands'].values()
                         if row['command']['intent_id'] == record.intent_id]
            if len(originals) != 1:
                return False
            row = originals[0]
            prepared = PrepareSpot.from_json(row['command'])
            store._assert_original_prepared_receipt(prepared, row, application.owner,
                                                    store._clock(), states=('PREPARING', 'PREPARED'))
            prepared_cut = FinancialCut.from_json(row['original_cut']).checkpoint
            initial = _initial(reporting.repository, meta.resources, record.intent_id, application.owner, prepared_cut)
            original = ExecutorInfo.model_validate_json(initial.payload_json)
            info = _reported(reporting.repository, meta.resources, initial, application.owner)
            cfg = original.config
            if (type(cfg) is not OrderExecutorConfig or cfg != info.config
                    or cfg.controller_id != c.config.id or info.controller_id != c.config.id
                    or (cfg.id, cfg.connector_name, cfg.trading_pair, cfg.side.name, cfg.amount, cfg.price, cfg.level_id) != (
                        prepared.intent_id, c.config.strategy.spot.connector, prepared.market, prepared.side,
                        prepared.quantity_base, prepared.limit_price_usdt, str(prepared.quote_level))
                    or cfg.execution_strategy != ExecutionStrategy.LIMIT_MAKER
                    or not application.owner.valid_from <= datetime.fromtimestamp(cfg.timestamp, timezone.utc)
                <= datetime.fromisoformat(row['prepared_at'])
                    or info.filled_amount_quote != 0 or info.cum_fees_quote != 0
                    or not set(info.custom_info.get('recovery_order_ids', ())).issubset({prepared.client_order_id})):
                return False
        return True
    except Exception:
        return False
