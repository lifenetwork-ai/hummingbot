"""Short CPU replay of real admin history DTOs; no DB, credentials or exchange."""
import argparse
import hashlib
import json
import runpy
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def pages(models, count):
    observed = datetime(2026, 1, 1, 7, tzinfo=timezone(timedelta(hours=7)))
    order = dict(id='order', connector='okx', market='spot', pair='LIFE-USDT', side='BUY',
                 amount=Decimal('0.00000001234567890123456789'), price=Decimal('0.1234567890123456789'),
                 order_type='LIMIT_MAKER', position='OPEN', leverage=1, status='BuyOrderCompleted',
                 creation_pending=False, exchange_order_id='exchange', created_at=observed, updated_at=observed)
    fill = dict(id='fill', order_id='order', market='spot', connector='okx', pair='LIFE-USDT', side='BUY',
                price=order['price'], amount=order['amount'], timestamp=observed, leverage=1, position='OPEN',
                fee=dict(percent='0', flat_fees=[dict(token='USDT', amount='0.000001')]),
                fee_in_quote=Decimal('0.000001'), quote_currency='USDT', conversion_status='recorded')
    return {name: models[name].model_validate(dict(items=[dict(row, id=str(i)) for i in range(count)], next_cursor=None))
            for name, row in [('OrdersPage', order), ('FillsPage', fill)]}


def benchmark(baseline, *, count=500, iterations=100, repeats=5):
    current = ROOT / 'hummingbot/cli/admin_models.py'
    paths = dict(baseline=baseline, current=current)
    models = {name: pages(runpy.run_path(str(path)), count) for name, path in paths.items()}
    measurements = {name: {kind: [] for kind in entries} for name, entries in models.items()}
    for kind in models['current']:
        assert models['baseline'][kind].model_dump(mode='json') == models['current'][kind].model_dump(mode='json')
    for entries in models.values():
        for page in entries.values():
            for _ in range(10):
                page.model_dump(mode='json')
    for repeat in range(repeats):
        for name in (('baseline', 'current') if repeat % 2 == 0 else ('current', 'baseline')):
            for kind, page in models[name].items():
                started = time.thread_time_ns()
                for _ in range(iterations):
                    page.model_dump(mode='json')
                measurements[name][kind].append((time.thread_time_ns() - started) / 1e6 / iterations)
    return dict(rows=count, iterations=iterations, repeats=repeats, clock='thread_time_ns',
                json_outputs_equal=True, per_page_cpu_ms=measurements,
                median_cpu_ms={name: {kind: statistics.median(values) for kind, values in entries.items()}
                               for name, entries in measurements.items()},
                source_sha256={name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()},
                scope='Synchronous serialization CPU only; excludes validation, DB, network and live event-loop scheduling.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rows', type=int, default=500, choices=(1, 100, 500))
    args = parser.parse_args()
    result = benchmark(args.baseline, count=args.rows)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['median_cpu_ms']))


if __name__ == '__main__':
    main()
