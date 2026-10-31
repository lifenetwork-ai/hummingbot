"""Matched ordinary-entrypoint control; isolated exchange/DB, no timing wrapper.

Reuse a completed load run's effective catalogue and client/exchange fixtures.
Internal instrumentation counters are unavailable: this is not full acceptance.
"""
import argparse
import hashlib
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.mm_load_budgets import violations  # noqa: E402
from scripts.test_e2e_offline import Harness, docker, poll  # noqa: E402
from scripts.test_mm_load import assets_for, command, seed_history, verify_ledger  # noqa: E402


def validate_baseline(baseline, *, diagnostic_baseline=False):
    if not baseline.get('enhancements') or not baseline.get('volatility_defense'):
        raise ValueError('Matched control requires an all-feature volatility-defense fixture')
    if not baseline.get('passed') and not diagnostic_baseline:
        raise ValueError('A failed baseline requires --diagnostic-baseline; it cannot close release acceptance')
    return baseline.get('passed') is True


def control_windows(*, probe_seconds=None, include_burst=False):
    if probe_seconds is not None:
        if (isinstance(probe_seconds, bool) or not isinstance(probe_seconds, int) or
                not 30 <= probe_seconds <= 900 or include_burst):
            raise ValueError('A diagnostic probe requires 30..900 seconds and cannot include burst')
        return [('probe', probe_seconds)]
    return [('warmup', 300), ('mixed', 900)] + ([('bursts', 90)] if include_burst else [])


def acceptance_eligible(baseline, *, probe_seconds=None):
    return (probe_seconds is None and baseline.get('passed') is True and baseline.get('profile') == 'all' and
            baseline.get('release_scope_complete') is True and baseline.get('diagnostic') is False)


def resource_snapshot(bot):
    # One-shot inspection only, outside the measured client window. Ordinary
    # service children keep their shipping command and have no probe monkeypatch.
    import inspect
    from test.e2e.offline.load_process_metrics import process_snapshot
    code = 'import json; from pathlib import Path\n' + inspect.getsource(process_snapshot)
    code += "\nprint(json.dumps(dict(processes=process_snapshot(),cpu={k:int(v) for k,v in (line.split() for line in Path('/sys/fs/cgroup/cpu.stat').read_text().splitlines())},memory={k:int(Path('/sys/fs/cgroup/memory.'+v).read_text()) for k,v in [('current_bytes','current'),('peak_bytes','peak')]})))"
    return json.loads(docker('exec', bot, 'python', '-c', code).stdout)


def summarize_window(data, *, memory, phase, setup_commands, ledger):
    """Apply unchanged budgets using measured setup commands for nonburst reads."""
    row = dict(data)
    row.update(runtime=dict(runtime_phase=phase, container_memory=memory),
               peak_rss_bytes=memory['peak_bytes'],
               command_seconds=row['control_results'].get('command_seconds', setup_commands), ledger=ledger)
    row['violations'] = violations(row)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True, help='Completed load evidence directory')
    parser.add_argument('--diagnostic-baseline', action='store_true', help='Explicit investigation of a failed baseline; never release acceptance')
    parser.add_argument('--include-burst', action='store_true', help='Add the same ninety-second burst after warm300/mixed900')
    parser.add_argument('--probe-seconds', type=int, help='Bounded 30..900s diagnostic only; never the R2 shipping control')
    parser.add_argument('--keep-going', action='store_true', help='Retain later diagnostic windows after a budget failure; overall result still fails')
    args = parser.parse_args()
    try:
        windows = control_windows(probe_seconds=args.probe_seconds, include_burst=args.include_burst)
    except ValueError as error:
        parser.error(str(error))
    args.baseline = args.baseline.resolve()
    baseline = json.loads((args.baseline / 'result.json').read_text())
    baseline_passed = validate_baseline(baseline, diagnostic_baseline=args.diagnostic_baseline)
    image = baseline['image_id']
    report = ROOT / 'build/service-supervision' / ('production-control-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    report.mkdir(parents=True)
    evidence = dict(scope=__doc__, diagnostic=True, release_scope_complete=False, image_id=image,
                    baseline_passed=baseline_passed,
                    baseline_acceptance_eligible=acceptance_eligible(baseline, probe_seconds=args.probe_seconds),
                    probe_seconds=args.probe_seconds,
                    include_burst=args.include_burst, continue_after_budget_failure=args.keep_going,
                    baseline=str(args.baseline.relative_to(ROOT)), bot_limits=baseline['bot_limits'],
                    daily_cache_ttl_seconds=baseline['daily_cache_ttl_seconds'], passed=False, windows={},
                    not_measured=['query counts', 'worker/pool queue', 'event-loop/GC timing', 'held-adapter introspection'])
    try:
        with tempfile.TemporaryDirectory(prefix='hbot-production-control-') as temporary:
            assets = Path(temporary)
            assets_for(assets)
            (assets / 'life_mm_profiles.yaml').write_bytes((args.baseline / 'effective-life-mm-profiles.yaml').read_bytes())
            evidence['fixture_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in assets.iterdir()}
            for name in ('life_mm_profiles.yaml', 'load_client.py', 'load_health.py', 'exchange.py', 'fixtures.json'):
                assert evidence['fixture_sha256'][name] == baseline['fixture_sha256'][name], name
            harness = Harness(image, assets, report, 'demo')
            harness.env.update(ADMIN_API_ENABLED='true', ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin',
                               ADMIN_STATISTICS_CACHE_TTL_SECONDS=str(baseline['daily_cache_ttl_seconds']), FUNDING_SYNC_INTERVAL_SECONDS='5')
            try:
                harness.setup()
                harness.control(include_own_orders=True)
                docker('update', '--cpus', '1', '--memory', '512m', '--memory-swap', '512m', harness.simulator)
                docker('update', '--cpus', '2', '--memory', '512m', '--memory-swap', '512m', harness.pg)
                bot, port = harness.start('load-bot', cpus=baseline['bot_limits']['cpus'], network_alias='bot')
                assert json.loads(docker('inspect', bot).stdout)[0]['Config']['Cmd'] == ['python', '-m', 'hummingbot.cli.service']
                docker('update', '--memory', '2g', '--memory-swap', '2g', bot)
                command(harness, port, 'start', market_mode='spot', selection='fixed', profile_id='sideways', duration_seconds=600)
                poll(lambda: bool(harness.state()['recovery_state'].get('spot_outflow', {}).get('binding')),
                     description='empty DB outflow provenance')
                command(harness, port, 'stop')
                seed_history(harness, baseline['history_orders'], active_outflow=True)
                stamp = int(time.time() * 1000) - 1000
                harness.control(funding_bills=[dict(billId=str(i), type='8', subType='174' if value[0] != '-' else '173',
                                                    instId='LIFE-USDT-SWAP', ts=str(stamp), pnl=value, ccy='USDT')
                                               for i, value in ((1, '-0.0000001234567890123456789'), (2, '0.0000000234567890123456789'))])
                controls = {}
                for action in ('start', 'switch', 'stop', 'start'):
                    elapsed, _ = command(harness, port, action, **(dict(market_mode='both', selection='fixed', profile_id='follow_market',
                                                                        duration_seconds=86400) if action != 'stop' else {}))
                    controls.setdefault(action, elapsed)
                evidence['setup_command_seconds'] = controls
                poll(lambda: len([o for o in harness.ledger()['orders'] if o['state'] == 'live']) == 12,
                     description='twelve production control quotes')
                assert harness.state()['status']['spot_outflow']['fill_count'] >= baseline['history_orders'] // 2
                client = harness.prefix + '-load-client'
                harness.run_container(client, ['--platform', 'linux/amd64', '--cpus', '1', '--memory', '512m', '--memory-swap', '512m',
                                               '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m', '--mount',
                                               f'type=bind,source={assets},target=/test-assets,readonly'],
                                      command=['python', '-c', 'import time;time.sleep(20000)'])
                print('Production control:', report, flush=True)
                for name, duration in windows:
                    before = resource_snapshot(bot)
                    options = (['--operations'] if baseline.get('operations_reads_per_second') else [])
                    if name == 'bursts':
                        options += ['--burst']
                        if baseline.get('market_diagnostics'):
                            options += ['--market-diagnostics']
                    result = docker('exec', client, 'python', '/test-assets/load_client.py', '--duration', duration,
                                    '--output', '/tmp/result.json', *options, timeout=duration + 60, check=False)
                    (report / (name + '-client.log')).write_text(result.stdout + result.stderr)
                    assert result.returncode == 0
                    row = json.loads(docker('exec', client, 'cat', '/tmp/result.json').stdout)
                    (report / (name + '-client.json')).write_text(json.dumps(row, indent=2) + '\n')
                    after = resource_snapshot(bot)
                    status = harness.admin(port, '/status')[1]
                    row = summarize_window(row, phase=status['phase'], memory=after['memory'], setup_commands=controls,
                                           ledger=verify_ledger(harness, port, require_running=False))
                    row['resources'] = dict(before=before, after=after)
                    evidence['windows'][name] = row
                    (report / 'result.json').write_text(json.dumps(evidence, indent=2) + '\n')
                    print(name, {key: row[key] for key in ('read_p95_ms', 'read_p99_ms', 'unexpected_errors', 'violations')}, flush=True)
                    if row['violations'] and not args.keep_going:
                        raise AssertionError('Ordinary-entrypoint latency/error budget failed')
                assert all(not row['violations'] for row in evidence['windows'].values()), 'Ordinary-entrypoint budgets failed'
                evidence['passed'] = True
            finally:
                harness.cleanup()
    except Exception as error:
        evidence['failure_type'] = type(error).__name__
        raise
    finally:
        (report / 'result.json').write_text(json.dumps(evidence, indent=2) + '\n')


if __name__ == '__main__':
    main()
