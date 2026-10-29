"""Actual shipping entrypoint/roles, isolated PostgreSQL and fake exchange only."""
import argparse
import inspect
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test.e2e.offline.load_process_metrics import process_snapshot  # noqa: E402

from scripts.test_e2e_offline import Harness, docker, http, poll  # noqa: E402
from scripts.test_mm_load import command  # noqa: E402
from scripts.test_mm_process_isolation import prepare, recovery_faults  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--port', type=int, default=8090)
    args = parser.parse_args()
    metadata = json.loads(docker('image', 'inspect', args.image).stdout)[0]
    report = ROOT / 'build/service-supervision' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    report.mkdir(parents=True)
    evidence = dict(image_id=metadata['Id'], scope='Ordinary production entrypoint lifecycle; isolated dummy exchange/DB',
                    release_scope_complete=False, completed=False, ingress_port=args.port, case={})
    try:
        with tempfile.TemporaryDirectory(prefix='hbot-supervised-') as temporary:
            assets = Path(temporary)
            prepare(assets, 'single')  # Reuse healthy settings; no wrapper is executed.
            harness = Harness(metadata['Id'], assets, report, 'demo')
            harness.env.update(ADMIN_API_ENABLED='true', ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin',
                               ADMIN_STATISTICS_CACHE_TTL_SECONDS='15', PORT=str(args.port))
            try:
                harness.setup()
                harness.control(include_own_orders=True)
                bot, port = harness.start('shipping-roles', cpus=2)
                actual = json.loads(docker('inspect', bot).stdout)[0]
                assert actual['Config']['Cmd'] == ['python', '-m', 'hummingbot.cli.service']
                evidence['ordinary_entrypoint'] = actual['Config']['Cmd']

                def snapshot():
                    code = 'import json\nfrom pathlib import Path\n' + inspect.getsource(process_snapshot) + '\nprint(json.dumps(process_snapshot()))'
                    return json.loads(docker('exec', bot, 'python', '-c', code).stdout)

                def pids():
                    data = snapshot()
                    return [data.get(role, {}).get('pid') for role in ('engine', 'api')]

                evidence['initial_processes'] = snapshot()
                assert set(evidence['initial_processes']) == {'supervisor', 'engine', 'api'}
                assert harness.admin(port, '/status')[1]['desired_state'] == 'stopped'
                assert http(port, '/ready')[0] == 503
                private = args.port + 1 if args.port < 65535 else 65534
                code = """import sys
from urllib.request import urlopen
from urllib.error import URLError
try:
    urlopen(sys.argv[1], timeout=1).close()
except (URLError, TimeoutError, OSError):
    sys.exit(0)
sys.exit(1)
"""
                assert docker('exec', harness.simulator, 'python', '-c', code,
                              f'http://{bot}:{private}/live', check=False).returncode == 0
                evidence['engine_health_loopback_only'] = True
                api_pid = pids()[1]
                code = """import json,sys
from pathlib import Path
keys=[item.split('=',1)[0] for item in Path('/proc/'+sys.argv[1]+'/environ').read_bytes().decode().split(chr(0)) if item]
print(json.dumps([key for key in keys if key.startswith('HBOT_') or key in ('ENV_FILE','HBOT_ENV_FILE')]))
"""
                keys = json.loads(docker('exec', bot, 'python', '-c', code, api_pid).stdout)
                assert keys == ['HBOT_OFFLINE_HTTP_ORIGIN']
                evidence['api_private_environment_keys'] = keys
                command(harness, port, 'start', market_mode='both', selection='fixed', profile_id='follow_market', duration_seconds=3600)
                poll(lambda: len([o for o in harness.ledger()['orders'] if o['state'] == 'live']) == 12,
                     description='shipping role working quotes')
                for instrument, amount in (('LIFE-USDT', '.001'), ('LIFE-USDT-SWAP', '.1')):
                    order = next(o for o in harness.ledger()['orders'] if o['instId'] == instrument and o['side'] == 'buy' and o['state'] == 'live')
                    harness.control(fill={'clOrdId': order['clOrdId'], 'amount': amount, 'fee': '0.000001'})
                poll(lambda: harness.sql('SELECT count(*) FROM trade_fills') == '2', description='recorded fixture fills')
                assert harness.ledger()['positions']
                evidence['nonempty_positions_verified'] = True
                recovery_faults(harness, bot, port, evidence['case'], pids_provider=pids)
                count = docker('logs', bot).stdout.count('Applied PostgreSQL script ')
                expected = 3 * len(list((ROOT / 'hummingbot/model/db_migration/postgres/scripts').glob('*.sql')))
                assert count == expected
                evidence.update(migration_batches=3, applied_script_count=count, api_restarts_do_not_migrate=True,
                                completed=True)
            except BaseException as error:
                evidence['failure_type'] = type(error).__name__
                raise
            finally:
                harness.cleanup()
    finally:
        (report / 'result.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print('Supervision evidence:', report, flush=True)


if __name__ == '__main__':
    main()
