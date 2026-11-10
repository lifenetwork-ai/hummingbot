"""Opt-in real OOM faults on an internal network; never uses live credentials."""
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

from scripts.mm_load_ledger import ledger_violations  # noqa: E402
from scripts.test_e2e_offline import Harness, docker, http, pin_manifest, poll  # noqa: E402
from scripts.test_mm_load import assets_for  # noqa: E402


def children(bot):
    code = """import json
from pathlib import Path
out={}
for pid in Path('/proc/1/task/1/children').read_text().split():
    args=(Path('/proc')/pid/'cmdline').read_bytes().decode().split(chr(0))
    if '--role' in args: out[args[args.index('--role')+1]]=int(pid)
print(json.dumps(out))
"""
    return json.loads(docker('exec', bot, 'python', '-c', code).stdout)


def memory_events(bot):
    return {k: int(v) for k, v in (line.split() for line in docker(
        'exec', bot, 'cat', '/sys/fs/cgroup/memory.events').stdout.splitlines())}


def kernel_oom_proven(before, after, *, container_state=None):
    if container_state is not None:
        return (container_state.get('Status') == 'exited' and container_state.get('ExitCode') == 137
                and container_state.get('OOMKilled') is True)
    return after.get('oom_kill', 0) > before.get('oom_kill', 0)


def arm(bot, role, boundary):
    docker('exec', bot, 'python', '-c',
           "from pathlib import Path;import json,sys;Path('/tmp/oom-'+sys.argv[1]+'.request').write_text(json.dumps({'boundary':sys.argv[2]}))",
           role, boundary)


def release(bot, role):
    code = """from pathlib import Path
import json,sys
p=Path('/tmp/oom-'+sys.argv[1]+'-armed.json')
if p.exists():
    print(p.read_text())
    Path('/tmp/oom-'+sys.argv[1]+'.release').touch()
"""

    def ready():
        value = docker('exec', bot, 'python', '-c', code, role).stdout.strip()
        return json.loads(value) if value else None
    return poll(ready, timeout=90, description='exact OOM boundary reached')


def exact_fills(harness):
    rows = json.loads(harness.sql("SELECT COALESCE(json_agg(t),'[]') FROM "
                                  "(SELECT market,order_id,exchange_trade_id,amount::text,price::text,"
                                  "trade_fee,trade_fee_in_quote::text FROM trade_fills) t"))
    assert not ledger_violations(harness.ledger(), rows)
    assert not harness.ledger()['unknown']
    return rows


def stop_after_oom(harness, bot):
    # Docker retains OOMKilled even if only a child was killed and the shipping
    # supervisor recovered. Preserve that cause instead of using the healthy
    # shutdown gate, which correctly rejects any OOM in ordinary release runs.
    started, requested = time.monotonic(), time.time()
    docker('kill', '--signal', 'TERM', bot)
    poll(lambda: json.loads(docker('inspect', bot).stdout)[0]['State']['Status'] == 'exited',
         timeout=9.5, description='bounded shutdown after intentional OOM')
    state = harness.record_shutdown_state(bot, started, requested)
    assert state['ExitCode'] == 0
    return state


def run_case(image, scenario, directory, memory_mib):
    with tempfile.TemporaryDirectory(prefix='hbot-oom-assets-') as tmp:
        assets = Path(tmp)
        assets_for(assets)
        source = ROOT / 'test/e2e/offline/oom_entrypoint.py'
        (assets / source.name).write_bytes(source.read_bytes())
        # Short ordinary refresh produces a fresh create after the seeded fill.
        import yaml
        path = assets / 'life_mm_profiles.yaml'
        cfg = yaml.safe_load(path.read_text())
        for market in cfg['markets'].values():
            market.update(executor_refresh_time=15, cooldown_time=1)
        path.write_text(yaml.safe_dump(cfg))
        h = Harness(image, assets, directory, 'demo')
        h.env.update(ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                     ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin')
        proof = dict(scenario=scenario, memory_mib=memory_mib, actual_okx=False, passed=False,
                     fixture_sha256={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in assets.iterdir()})
        try:
            h.setup()
            bot, port = h.start('oom-bot', instrumented=True, instrumented_entrypoint='oom_entrypoint.py', cpus=2)
            docker('update', '--memory', str(memory_mib) + 'm', '--memory-swap', str(memory_mib) + 'm', bot)
            assert docker('exec', bot, 'cat', '/sys/fs/cgroup/memory.oom.group').stdout.strip() == '0'
            if scenario == 'container':
                h.cli('start', 'pmm.yml', '--timeout', '0', '--json')
            else:
                h.mm_command(port, 'start', market_mode='both', selection='fixed',
                             profile_id='sideways', duration_mode='until_stopped')
            h.wait_running(port)
            poll(lambda: sum(o['state'] == 'live' for o in h.ledger()['orders']) >= 2,
                 description='working quotes before OOM')
            before, pids = h.state(), children(bot)
            initial_start = json.loads(docker('inspect', bot).stdout)[0]['State']['StartedAt']
            counters = memory_events(bot)
            if scenario != 'container':
                order = next(o for o in h.ledger()['orders'] if o['state'] == 'live' and not o['instId'].endswith('-SWAP'))
                h.control(fill=dict(clOrdId=order['clOrdId'], amount='.01', fee='.000001'), duplicate=True)
                poll(lambda: h.sql('SELECT count(*) FROM trade_fills') == '1', description='seed fill committed')
                exact_fills(h)
            role = {'api': 'api', 'engine': 'engine', 'container': 'engine'}[scenario]
            boundary = {'api': 'now', 'engine': 'create', 'container': 'fill'}[scenario]
            arm(bot, role, boundary)
            if scenario == 'container':
                # OOM the namespace init while the engine transaction is paused.
                arm(bot, 'supervisor', 'now')
                order = next(o for o in h.ledger()['orders'] if o['state'] == 'live')
                h.control(fill=dict(clOrdId=order['clOrdId'], amount='.01', fee='.000001'), duplicate=True)
                # Only release PID1 pressure; keep the recorder before COMMIT.
                marker_code = "from pathlib import Path;p=Path('/tmp/oom-engine-armed.json');print(p.read_text() if p.exists() else '')"
                fill_marker = poll(lambda: docker('exec', bot, 'python', '-c', marker_code).stdout.strip(),
                                   description='real fill flushed before container OOM')
                proof['fill_boundary'] = json.loads(fill_marker)
                assert h.sql('SELECT count(*) FROM trade_fills') == '0'
                role = 'supervisor'
            marker = release(bot, role)
            assert marker['pid'] == (1 if role == 'supervisor' else pids[role])
            proof.update(marker=marker, memory_events_before=counters)
            (directory / 'boundary.json').write_text(json.dumps(proof, indent=2))
            if scenario == 'container':
                state = poll(lambda: (s if (s := json.loads(docker('inspect', bot).stdout)[0]['State'])['Status'] == 'exited' else None),
                             timeout=30, description='kernel OOM kills the complete container')
                assert kernel_oom_proven({}, {}, container_state=state), state
                proof['physical_exit'] = {k: state[k] for k in ('Status', 'ExitCode', 'OOMKilled')}
                accepted = len(h.ledger()['accepted'])
                docker('start', bot)
                poll(lambda: http(port, '/startup')[0] == 200, timeout=200, description='fresh container starts after OOM')
            else:
                old = pids[role]
                after = poll(lambda: (c if (c := children(bot)).get(role) not in (None, old) else None),
                             timeout=90, description='kernel-killed role replaced')
                events = memory_events(bot)
                assert kernel_oom_proven(counters, events)
                assert after['engine' if role == 'api' else 'api'] == pids['engine' if role == 'api' else 'api']
                assert json.loads(docker('inspect', bot).stdout)[0]['State']['StartedAt'] == initial_start
                proof.update(memory_events_after=events, peer_pid_preserved=True)
                accepted = len(h.ledger()['accepted'])
            if scenario == 'api':
                h.wait_running(port)
                row = h.state()
                assert row['owner_uuid'] == before['owner_uuid'] and row['owner_generation'] == before['owner_generation']
                assert row['mm_session']['id'] == before['mm_session']['id']
                assert not row['recovery_state'].get('halt')
                proof['owner_session_preserved'] = True
                h.mm_command(port, 'stop')
            else:
                poll(lambda: h.state()['owner_generation'] > before['owner_generation']
                     and (h.state().get('status') or {}).get('phase') == 'halted',
                     timeout=180, description='replacement preserves unresolved-effect HALT')
                poll(lambda: h.sql('SELECT count(*) FROM trade_fills') == '1', description='restart retains/backfills one venue fill')
                assert len(h.ledger()['accepted']) == accepted
                assert http(port, '/ready')[0] == 503
                assert h.state()['desired_state'] == 'running'
                proof.update(halt_preserved=True, no_automatic_resubmission=True)
            proof['fills'] = exact_fills(h)
            assert len(proof['fills']) == 1
            proof['final_shutdown'] = stop_after_oom(h, bot)
            proof['passed'] = True
        except BaseException as error:
            proof['failure_type'] = type(error).__name__
            raise
        finally:
            (directory / 'result.json').write_text(json.dumps(proof, indent=2))
            h.cleanup()
    return proof


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--scenario', choices=('all', 'api', 'engine', 'container'), default='all')
    parser.add_argument('--memory-mib', type=int, choices=(1024, 1536, 2048), default=1024)
    args = parser.parse_args()
    image = pin_manifest(args.image)
    directory = ROOT / 'build/mm-oom' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    directory.mkdir(parents=True)
    result = dict(image=image, diagnostic=True, passed=False, cases={})
    try:
        for scenario in (('api', 'engine', 'container') if args.scenario == 'all' else (args.scenario,)):
            target = directory / scenario
            target.mkdir()
            print('Real OOM case: ' + scenario, flush=True)
            try:
                result['cases'][scenario] = run_case(image, scenario, target, args.memory_mib)
            except BaseException:
                if (target / 'result.json').exists():
                    result['cases'][scenario] = json.loads((target / 'result.json').read_text())
                raise
        result['passed'] = True
    finally:
        (directory / 'result.json').write_text(json.dumps(result, indent=2))
        print('OOM evidence: ' + str(directory / 'result.json'), flush=True)
    return 0


if __name__ == '__main__':
    main()
