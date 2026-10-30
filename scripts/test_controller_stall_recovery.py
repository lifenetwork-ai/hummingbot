"""Shipping-image diagnostic: resume a delayed engine without inventing progress.

SIGSTOP pauses the engine alone beyond its controller heartbeat deadline but
below the unchanged supervisor grace. This is an injected scheduling fault,
not a healthy-load acceptance or a claim about the old host's root cause.
"""
import argparse
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.test_e2e_offline import Harness, docker, poll  # noqa: E402
from scripts.test_mm_load import command, verify_ledger  # noqa: E402
from scripts.test_mm_process_isolation import prepare  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    image = json.loads(docker('image', 'inspect', args.image).stdout)[0]['Id']
    report = ROOT / 'build/controller-stall' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    report.mkdir(parents=True)
    evidence = dict(image_id=image, passed=False, diagnostic=True, release_scope_complete=False, cycles=[])
    try:
        with tempfile.TemporaryDirectory(prefix='hbot-controller-stall-') as directory:
            assets = Path(directory)
            prepare(assets, 'single')
            harness = Harness(image, assets, report, 'demo')
            harness.env.update(ADMIN_API_ENABLED='true', ADMIN_API_USERNAME='operator',
                               ADMIN_API_PASSWORD='offline-admin', ADMIN_STATISTICS_CACHE_TTL_SECONDS='15')
            try:
                harness.setup()
                harness.control(include_own_orders=True)
                bot, port = harness.start('shipping-controller-stall', cpus=2)
                actual = json.loads(docker('inspect', bot).stdout)[0]['Config']['Cmd']
                assert actual == ['python', '-m', 'hummingbot.cli.service']
                evidence['entrypoint'] = actual
                command(harness, port, 'start', market_mode='both', selection='fixed',
                        profile_id='follow_market', duration_seconds=600)
                initial = harness.state()
                identity = harness.engine_identity(bot)
                evidence['engine_pid'] = identity

                def fill_both():
                    for instrument, amount in (('LIFE-USDT', '.001'), ('LIFE-USDT-SWAP', '.1')):
                        order = poll(lambda: next((o for o in harness.ledger()['orders'] if o['instId'] == instrument
                                                  and o['side'] == 'buy' and o['state'] in ('live', 'partially_filled')), None),
                                     description='working quote after controller resume')
                        harness.control(fill={'clOrdId': order['clOrdId'], 'amount': amount, 'fee': '0.000001'}, duplicate=True)
                    expected = len(harness.ledger()['fills'])
                    poll(lambda: int(harness.sql('SELECT count(*) FROM trade_fills')) == expected,
                         description='post-resume fills durably recorded once')

                fill_both()
                for offset in (0, .17, .43):
                    time.sleep(offset)
                    started = time.monotonic()
                    docker('exec', bot, 'kill', '-STOP', str(identity))
                    try:
                        time.sleep(6)
                    finally:
                        docker('exec', bot, 'kill', '-CONT', str(identity), check=False)
                    row = dict(paused_seconds=time.monotonic() - started, offset_seconds=offset)
                    evidence['cycles'].append(row)
                    time.sleep(2)
                    observed = harness.state()
                    row.update(phase=observed['status'].get('phase'), halt=bool(observed['recovery_state'].get('halt')),
                               engine_pid=harness.engine_identity(bot))
                    assert not row['halt'], row
                    harness.wait_running(port)
                    assert row['engine_pid'] == identity
                    assert observed['owner_uuid'] == initial['owner_uuid']
                    assert observed['mm_session']['id'] == initial['mm_session']['id']
                    fill_both()
                    row['ledger'] = verify_ledger(harness, port)
                command(harness, port, 'stop')
                evidence['passed'] = True
            finally:
                harness.cleanup()
    except BaseException as error:
        evidence['error_type'] = type(error).__name__
        raise
    finally:
        (report / 'result.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print('Controller scheduling evidence:', report, flush=True)


if __name__ == '__main__':
    main()
