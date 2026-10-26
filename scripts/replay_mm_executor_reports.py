"""Read-only report CPU replay from an offline executor snapshot, without a DB."""
import argparse
import hashlib
import json
import statistics
import sys
import time
from collections import deque
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def replay(path, generation, iterations=100):
    from hummingbot.core.data_type.common import PositionMode
    from hummingbot.strategy_v2.executors.executor_orchestrator import ExecutorOrchestrator
    from hummingbot.strategy_v2.models.executors_info import ExecutorInfo, PerformanceReport

    class RecordedExecutor:
        def __init__(self, row):
            model = ExecutorInfo.model_validate(row)
            self.values = {key: getattr(model, key) for key in ExecutorInfo.model_fields}
            self.reads = 0

        @property
        def executor_info(self):
            self.reads += 1
            return ExecutorInfo(**self.values)

    rows = [row for row in json.loads(path.read_text()) if row['config'].get('mm_generation') == generation]
    if not rows:
        raise ValueError('Selected generation is absent from the snapshot')
    # Skip the constructor's recorder/DB hydration; replay only stored DTOs and
    # pure report math. No executor actor or connector is started.
    orchestrator = ExecutorOrchestrator.__new__(ExecutorOrchestrator)
    orchestrator.active_executors = {}
    for row in rows:
        orchestrator.active_executors.setdefault(row['controller_id'], []).append(RecordedExecutor(row))
    orchestrator.positions_held = {key: [] for key in orchestrator.active_executors}
    orchestrator.executors_ids_position_held = deque(maxlen=50)
    orchestrator.cached_performance = {key: PerformanceReport() for key in orchestrator.active_executors}
    orchestrator.strategy = SimpleNamespace(
        connectors={'okx_perpetual': SimpleNamespace(position_mode=PositionMode.HEDGE)},
        markets={'okx': {'LIFE-USDT'}, 'okx_perpetual': {'LIFE-USDT'}},
        market_data_provider=SimpleNamespace(get_price_by_type=lambda *args: Decimal('0.1')))
    orchestrator.get_all_reports()  # Hydrate position holds once, outside timing.
    timings = []
    for _ in range(iterations):
        start = time.thread_time_ns()
        orchestrator.get_all_reports()
        timings.append((time.thread_time_ns() - start) / 1e6)
    return dict(generation=generation, recorded_executors=len(rows),
                held_executor_ids=len(orchestrator.executors_ids_position_held),
                position_buckets=sum(map(len, orchestrator.positions_held.values())), iterations=iterations,
                median_cpu_ms=statistics.median(timings), max_cpu_ms=max(timings),
                snapshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                orchestrator_sha256=hashlib.sha256((ROOT / 'hummingbot/strategy_v2/executors/executor_orchestrator.py').read_bytes()).hexdigest(),
                scope='Stored terminal DTO batch and existing report math; excludes live executor PnL properties, clock scheduling, DB, network and current in-flight state. Not an exact process replay.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot', type=Path)
    parser.add_argument('--generation', type=int, default=4)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = replay(args.snapshot, args.generation)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
