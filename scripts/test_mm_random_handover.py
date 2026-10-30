"""Short isolated Random handover diagnostic on a previously measured image.

Reuse the full-load catalogue and caps, with 30-second steps only for this
diagnostic. Quote probes observe original decisions; this is not release load.
"""
import argparse
import hashlib
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.test_e2e_offline import Harness, docker, poll  # noqa: E402
from scripts.test_mm_load import (  # noqa: E402
    assets_for,
    command,
    read_metrics,
    run_window,
    seed_history,
    verify_ledger,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    args = parser.parse_args()
    baseline = json.loads((args.baseline / 'result.json').read_text())
    report = ROOT / 'build/mm-random-handover' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    report.mkdir(parents=True)
    evidence = dict(diagnostic=True, release_scope_complete=False, image_id=baseline['image_id'],
                    baseline=str(args.baseline), passed=False, step_seconds=30, duration_seconds=180)
    try:
        with tempfile.TemporaryDirectory(prefix='hbot-handover-') as directory:
            assets = Path(directory)
            assets_for(assets)
            profiles = yaml.safe_load((args.baseline / 'effective-life-mm-profiles.yaml').read_text())
            profiles['random']['step_duration_seconds'] = 30
            (assets / 'life_mm_profiles.yaml').write_text(yaml.safe_dump(profiles))
            (report / 'effective-life-mm-profiles.yaml').write_bytes((assets / 'life_mm_profiles.yaml').read_bytes())
            (assets / 'quote_refresh_probe.py').write_bytes((ROOT / 'test/e2e/offline/quote_refresh_probe.py').read_bytes())
            source = (assets / 'load_service.py').read_text()
            assert source.count('service.main()') == 1
            (assets / 'load_service.py').write_text(source.replace('service.main()',
                                                                   'from quote_refresh_probe import install\ninstall()\nservice.main()'))
            evidence['fixture_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in assets.iterdir()}
            harness = Harness(baseline['image_id'], assets, report, 'demo')
            harness.env.update(ADMIN_API_ENABLED='true', ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin',
                               ADMIN_STATISTICS_CACHE_TTL_SECONDS='15', FUNDING_SYNC_INTERVAL_SECONDS='5')
            try:
                harness.setup()
                harness.control(include_own_orders=True)
                docker('update', '--cpus', '1', '--memory', '512m', '--memory-swap', '512m', harness.simulator)
                docker('update', '--cpus', '2', '--memory', '512m', '--memory-swap', '512m', harness.pg)
                bot, port = harness.start('handover', instrumented=True, cpus=2)
                poll(lambda: read_metrics(harness, bot), description='runtime metrics')
                command(harness, port, 'start', market_mode='spot', selection='fixed', profile_id='sideways', duration_seconds=600)
                poll(lambda: bool(harness.state()['recovery_state'].get('spot_outflow', {}).get('binding')),
                     description='empty database outflow origin')
                command(harness, port, 'stop')
                seed_history(harness, baseline['history_orders'], active_outflow=True)
                command(harness, port, 'start', market_mode='both', selection='random', duration_seconds=600)
                client = harness.prefix + '-load-client'
                harness.run_container(client, ['--platform', 'linux/amd64', '--cpus', '1', '--memory', '512m',
                                               '--memory-swap', '512m', '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m',
                                               '--mount', f'type=bind,source={assets},target=/test-assets,readonly'],
                                      command=['python', '-c', 'import time;time.sleep(1200)'])
                print('Random handover diagnostic:', report, flush=True)
                row = run_window(harness, bot, client, 'handover', 180, report, options=('--random-soak', '--operations'))
                evidence['window'] = row
                evidence['ledger'] = verify_ledger(harness, port)
                events = json.loads(harness.sql("SELECT COALESCE(json_agg(t),'[]') FROM (SELECT event_type,new_profile,payload FROM mm_events WHERE event_type='step_started' ORDER BY id) t"))
                evidence['steps'] = events
                logs = docker('logs', bot)
                probes = []
                for line in (logs.stdout + logs.stderr).splitlines():
                    try:
                        event = json.loads(line)
                        if event.get('message', '').startswith('HANDOVER_PROBE '):
                            probes.append(json.loads(event['message'].removeprefix('HANDOVER_PROBE ')))
                    except ValueError:
                        continue
                evidence['quote_probes'] = probes
                assert len(events) >= 5 and probes, 'Expected observed Random transitions and quote decisions'
                assert not row['errors'], row['errors']
                assert not harness.state()['recovery_state'].get('halt')
                evidence['passed'] = True
            finally:
                harness.cleanup()
    finally:
        (report / 'result.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print('Evidence:', report, flush=True)


if __name__ == '__main__':
    main()
