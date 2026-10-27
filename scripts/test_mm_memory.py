"""Offline memory diagnostics; no .env, real exchange or production database."""
import argparse
import gc
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import time
import tracemalloc
from decimal import Decimal
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT if (ROOT / 'hummingbot').exists() else Path.cwd()))


def component_probe():
    from hummingbot.core.data_type.common import OrderType, TradeType
    from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
    from hummingbot.core.event.event_logger import EventLogger
    from hummingbot.core.event.events import OrderFilledEvent

    results = {}
    for label, maximum in [('legacy_unbounded_control', None), ('bounded_diagnostics', 1000)]:
        gc.collect()
        tracemalloc.start()
        logger = EventLogger()
        logger.set_fill_event_limit(maximum)
        rows, previous = [], 0
        for count in (1000, 10000, 50000, 100000):
            for index in range(previous, count):
                logger(OrderFilledEvent(index, f'order-{index}', 'LIFE-USDT', TradeType.BUY,
                                        OrderType.LIMIT, Decimal('.1'), Decimal('1'), AddedToCostTradeFee()))
            gc.collect()
            current, peak = tracemalloc.get_traced_memory()
            rows.append(dict(injected=count, retained=len(logger.event_log), python_bytes=current, peak_python_bytes=peak))
            previous = count
        results[label] = rows
        del logger
        gc.collect()
        tracemalloc.stop()
    bounded = results['bounded_diagnostics']
    assert all(row['retained'] == 1000 for row in bounded)
    assert bounded[-1]['python_bytes'] - bounded[1]['python_bytes'] < 2 * 1024**2
    assert results['legacy_unbounded_control'][-1]['retained'] == 100000
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='hummingbot-cloudrun:local')
    parser.add_argument('--seconds', type=int, default=600)
    parser.add_argument('--component-only', action='store_true')
    args = parser.parse_args()
    if args.component_only:
        print(json.dumps(component_probe(), indent=2))
        return
    if args.seconds < 180:
        parser.error('Use at least 180 seconds for lifecycle sampling.')
    import yaml

    from scripts.test_e2e_offline import Harness, docker, poll
    from scripts.test_mm_load import assets_for, read_metrics, run_window, seed_history, verify_ledger

    report = ROOT / 'build/memory-review' / time.strftime('%Y%m%d-%H%M%S')
    report.mkdir(parents=True)
    metadata = json.loads(docker('image', 'inspect', args.image).stdout)[0]
    evidence = dict(passed=False, diagnostic=True, seconds=args.seconds, image_id=metadata['Id'],
                    revision=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    budgets=dict(rss_growth_bytes=32 * 1024**2, read_p99_ms=1000, unexpected_errors=0),
                    gc_scope='Only requested after measured workload and explicit Stop; never a production GC policy')
    print('Memory evidence:', report, flush=True)
    with tempfile.TemporaryDirectory(prefix='hbot-memory-') as directory:
        assets = Path(directory)
        assets_for(assets)
        (assets / 'test_mm_memory.py').write_bytes(Path(__file__).read_bytes())
        profiles = yaml.safe_load((assets / 'life_mm_profiles.yaml').read_text())
        profiles['random']['step_duration_seconds'] = 30
        profiles['allow_until_stopped'] = True
        (assets / 'life_mm_profiles.yaml').write_text(yaml.safe_dump(profiles))
        evidence['fixture_sha256'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in assets.iterdir()}
        harness = Harness(metadata['Id'], assets, report, 'demo')
        harness.env.update(ADMIN_API_ENABLED='true', ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin',
                           HBOT_MEMORY_DIAGNOSTICS='1', FUNDING_SYNC_INTERVAL_SECONDS='5')
        finished = threading.Event()
        events, failures = [], []
        timeline = None
        try:
            harness.setup()
            bot, port = harness.start('memory-bot', instrumented=True)
            docker('update', '--memory', '2g', '--memory-swap', '2g', bot)
            seed_history(harness, 100000)
            poll(lambda: bool(harness.state().get('status')), description='memory owner status')
            harness.mm_command(port, 'start', market_mode='both', selection='random', duration_mode='until_stopped')
            initial = read_metrics(harness, bot)
            evidence['initial'] = initial

            def controls():
                began = time.monotonic()
                try:
                    for fraction, action in ((.3, 'restart_session'), (.6, 'fixed'), (.7, 'random')):
                        if finished.wait(max(0, began + args.seconds * fraction - time.monotonic())):
                            return
                        if action == 'restart_session':
                            harness.mm_command(port, 'stop')
                            receipt = harness.mm_command(port, 'start', market_mode='both', selection='random', duration_mode='until_stopped')
                        else:
                            fields = dict(market_mode='both', selection=action, duration_mode='until_stopped')
                            if action == 'fixed':
                                fields['profile_id'] = 'sideways'
                            receipt = harness.mm_command(port, 'switch', **fields)
                        events.append(dict(action=action, elapsed_seconds=time.monotonic() - began, receipt_id=receipt['id']))
                except Exception as error:
                    failures.append(dict(error=type(error).__name__, detail=str(error)))
            client = harness.prefix + '-memory-client'
            harness.run_container(client, ['--platform', 'linux/amd64', '--cpus', '1', '--memory', '512m', '--read-only',
                                           '--tmpfs', '/tmp:rw,nosuid,size=128m', '--mount',
                                           f'type=bind,source={assets},target=/test-assets,readonly'],
                                  command=['python', '-c', 'import time; time.sleep(20000)'])
            timeline = threading.Thread(target=controls, daemon=True)
            timeline.start()
            data = run_window(harness, bot, client, 'memory', args.seconds, report,
                              options=('--random-soak', '--no-controls', '--operations'))
            evidence['window'] = data
            assert not failures, failures
            assert len(events) == 3, events
            evidence['ledger'] = verify_ledger(harness, port)
            sample_rows = json.loads((report / 'memory-runtime-minutes.json').read_text())
            assert len(sample_rows) >= 2
            growth = median(row['memory']['VmRSS'] for row in sample_rows[-2:]) - median(row['memory']['VmRSS'] for row in sample_rows[:2])
            evidence.update(rss_growth_bytes=growth, controls=events, samples=sample_rows)
            assert growth <= evidence['budgets']['rss_growth_bytes'], growth
            assert data['unexpected_errors'] == 0 and data['completed_reads'] == data['requested_reads']
            assert data['read_p99_ms'] <= evidence['budgets']['read_p99_ms']
            for row in sample_rows:
                counts = row['retention']
                assert counts['processed_executor_ids'] <= counts['resident_executors']
                assert counts['position_order_ids'] <= counts['resident_held_order_ids']
                assert all(item['fill_events'] <= 1000 and item['legacy_fills'] == item['legacy_order_ids'] == 0
                           for item in counts['connectors'].values()), counts
            harness.mm_command(port, 'stop')
            docker('exec', bot, 'python', '-c', "from pathlib import Path; Path('/tmp/memory-review-collect').touch()")

            def after_gc():
                result = read_metrics(harness, bot)
                return result if result and result['retention']['gc_runs'] else None
            final = poll(after_gc, timeout=20, description='post-Stop diagnostic collection')
            evidence['after_stop_gc'] = final
            assert final['retention']['strategies'] == final['retention']['providers'] == 0, final['retention']
            component = docker('exec', client, 'python', '/test-assets/test_mm_memory.py', '--component-only', timeout=90)
            evidence['component'] = json.loads(component.stdout)
            evidence['passed'] = True
        finally:
            finished.set()
            if timeline:
                timeline.join(timeout=30)
            evidence['control_errors'] = failures
            harness.cleanup()
            (report / 'result.json').write_text(json.dumps(evidence, indent=2))
    print(json.dumps(dict(passed=evidence['passed'], rss_growth_bytes=evidence['rss_growth_bytes'], report=str(report))))


if __name__ == '__main__':
    main()
