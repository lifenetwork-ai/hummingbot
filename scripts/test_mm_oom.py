"""Opt-in real OOM faults on an internal network; never uses live credentials."""
import argparse
import hashlib
import json
import sys
import tempfile
import time
import uuid
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.mm_load_ledger import ledger_violations  # noqa: E402
from scripts.test_e2e_offline import Harness, docker, http, pin_manifest, poll  # noqa: E402
from scripts.test_mm_load import assets_for  # noqa: E402

OBSERVER_OOM_EXITS = {}


def children(bot):
    # A new Python interpreter competes with the deliberate allocator in the
    # same cgroup. Read /proc with tiny native tools and tolerate only a physical
    # SIGKILL of this test observer; kernel/owner/quote proofs still must pass.
    code = """for task_pid in $(cat /proc/1/task/1/children); do
printf '%s ' "$task_pid"
cat "/proc/$task_pid/cmdline" 2>/dev/null
printf '\\n'
done
"""
    result = docker('exec', bot, 'sh', '-c', code, check=False)
    if result.returncode == 137:
        OBSERVER_OOM_EXITS[bot] = OBSERVER_OOM_EXITS.get(bot, 0) + 1
        return {}
    if result.returncode:
        raise RuntimeError('OOM process observer failed with exit code ' + str(result.returncode)
                           + ': ' + (result.stdout + result.stderr)[-1000:])
    out = {}
    for line in result.stdout.splitlines():
        pid, _, command = line.partition(' ')
        args = command.split(chr(0))
        if '--role' in args:
            out[args[args.index('--role') + 1]] = int(pid)
    return out


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
            observer = bot
            if scenario not in ('container', 'container-mm'):
                # Independent observation must not allocate inside the cgroup
                # being exhausted. Share only this fixture's PID namespace;
                # retain separate hard memory/CPU limits and no network access.
                observer = h.prefix + '-oom-observer'
                docker('run', '-d', '--name', observer, '--pid', 'container:' + bot,
                       '--network', 'none', '--user', '10001:10001', '--read-only',
                       '--memory', '32m', '--memory-swap', '32m', '--cpus', '.1',
                       '--entrypoint', 'sh', 'postgres:14-alpine', '-c', 'while :; do sleep 3600; done')
                h.containers.append(observer)
                proof['independent_process_observer'] = dict(memory_bytes=32 * 1024**2, cpus=.1, network='none')
                assert children(observer) == pids
            initial_start = json.loads(docker('inspect', bot).stdout)[0]['State']['StartedAt']
            counters = memory_events(bot)
            if scenario != 'container':
                order = next(o for o in h.ledger()['orders'] if o['state'] == 'live' and o['side'] == 'buy'
                             and not o['instId'].endswith('-SWAP'))
                h.control(fill=dict(clOrdId=order['clOrdId'], amount='.01', fee='.000001'), duplicate=True)
                poll(lambda: h.sql('SELECT count(*) FROM trade_fills') == '1', description='seed fill committed')
                exact_fills(h)
            role = 'api' if scenario == 'api' else 'engine'
            boundary = {'api': 'now', 'engine': 'create', 'engine-fill': 'fill',
                        'engine-commit': 'fill-commit', 'engine-hold': 'hold', 'container': 'fill',
                        'container-mm': 'fill'}[scenario]
            expected_fills = 1
            arm(bot, role, boundary)
            if scenario in ('engine-fill', 'engine-commit'):
                order = next(o for o in h.ledger()['orders'] if o['state'] == 'live' and o['side'] == 'buy'
                             and not o['instId'].endswith('-SWAP'))
                h.control(fill=dict(clOrdId=order['clOrdId'], amount='.01', fee='.000001'), duplicate=True)
                expected_fills = 2
            if scenario in ('container', 'container-mm'):
                # OOM the namespace init while the engine transaction is paused.
                arm(bot, 'supervisor', 'now')
                order = next(o for o in h.ledger()['orders'] if o['state'] == 'live' and o['side'] == 'buy'
                             and not o['instId'].endswith('-SWAP'))
                h.control(fill=dict(clOrdId=order['clOrdId'], amount='.01', fee='.000001'), duplicate=True)
                expected_fills = 1 if scenario == 'container' else 2
                # Only release PID1 pressure; keep the recorder before COMMIT.
                marker_code = "from pathlib import Path;p=Path('/tmp/oom-engine-armed.json');print(p.read_text() if p.exists() else '')"
                fill_marker = poll(lambda: docker('exec', bot, 'python', '-c', marker_code).stdout.strip(),
                                   description='real fill flushed before container OOM')
                proof['fill_boundary'] = json.loads(fill_marker)
                assert h.sql('SELECT count(*) FROM trade_fills') == ('0' if scenario == 'container' else '1')
                role = 'supervisor'
            marker = release(bot, role)
            assert marker['pid'] == (1 if role == 'supervisor' else pids[role])
            proof.update(marker=marker, memory_events_before=counters)
            (directory / 'boundary.json').write_text(json.dumps(proof, indent=2))
            if scenario in ('container', 'container-mm'):
                state = poll(lambda: (s if (s := json.loads(docker('inspect', bot).stdout)[0]['State'])['Status'] == 'exited' else None),
                             timeout=30, description='kernel OOM kills the complete container')
                assert kernel_oom_proven({}, {}, container_state=state), state
                proof['physical_exit'] = {k: state[k] for k in ('Status', 'ExitCode', 'OOMKilled')}
                accepted = len(h.ledger()['accepted'])
                docker('start', bot)
                poll(lambda: http(port, '/startup')[0] == 200, timeout=200, description='fresh container starts after OOM')
            else:
                old = pids[role]
                after = poll(lambda: (c if (c := children(observer)).get(role) not in (None, old) else None),
                             timeout=90, description='kernel-killed role replaced')
                events = memory_events(bot)
                assert kernel_oom_proven(counters, events)
                peer = 'engine' if role == 'api' else 'api'
                # Siblings share one hard cgroup quota. Kernel OOM is not a
                # promise that only the allocating process will be killed.
                peer_preserved = after.get(peer) == pids[peer]
                assert json.loads(docker('inspect', bot).stdout)[0]['State']['StartedAt'] == initial_start
                proof.update(memory_events_after=events, peer_pid_preserved=peer_preserved,
                             pids_before=pids, pids_after=after)
                accepted = len(h.ledger()['accepted'])
            if scenario == 'api':
                h.wait_running(port)
                row = h.state()
                if peer_preserved:
                    assert row['owner_uuid'] == before['owner_uuid'] and row['owner_generation'] == before['owner_generation']
                else:
                    assert row['owner_generation'] > before['owner_generation']
                assert row['mm_session']['id'] == before['mm_session']['id']
                assert row['control_version'] == before['control_version']
                assert not row['recovery_state'].get('halt')
                proof.update(session_control_preserved=True, verified_running_after_oom=True)
            else:
                if scenario == 'container':
                    # Legacy V1 has no verified MM checkpoint/attempt scope.
                    poll(lambda: h.state()['owner_generation'] > before['owner_generation']
                         and (h.state().get('status') or {}).get('phase') == 'halted',
                         timeout=180, description='legacy replacement preserves unresolved-effect HALT')
                    assert len(h.ledger()['accepted']) == accepted and http(port, '/ready')[0] == 503
                    proof.update(halt_preserved=True, no_automatic_resubmission=True)
                else:
                    # Managed MM can resume only after its durable baseline and
                    # positive venue fills/status prove the affected outcomes.
                    poll(lambda: h.state()['owner_generation'] > before['owner_generation'],
                         timeout=180, description='replacement owns physical authority')
                    h.wait_running(port)
                    row = h.state()
                    assert not row['recovery_state'].get('halt')
                    assert row['mm_session']['id'] == before['mm_session']['id']
                    assert row['control_version'] == before['control_version']
                    assert row['desired_state'] == 'running'
                    if scenario == 'engine':
                        assert sum(r['method'] == 'POST' and r['path'] == '/api/v5/trade/order'
                                   and r.get('order', {}).get('clOrdId') == marker['order']
                                   for r in h.ledger()['requests']) == 1
                    proof.update(verified_automatic_resume=True, owner_generation_advanced=True,
                                 session_control_preserved=True)
                if scenario != 'container':
                    poll(lambda: h.sql('SELECT count(*) FROM trade_fills') == str(expected_fills),
                         description='restart retains/backfills each venue fill once')
            if scenario != 'container':
                generation = int(h.state()['owner_generation'])

                def owned_working_quotes():
                    ids = set(json.loads(h.sql(
                        "SELECT COALESCE(json_agg(order_id),'[]') FROM (SELECT order_id FROM submission_attempts "
                        "WHERE state='acknowledged' AND context->>'generation'='" + str(generation) + "' LIMIT 64) a")))
                    working = [o for o in h.ledger()['orders'] if o['clOrdId'] in ids
                               and o['state'] in ('live', 'partially_filled')]
                    families = {(o['instId'], o['side']) for o in working}
                    expected = {('LIFE-USDT', 'buy'), ('LIFE-USDT', 'sell'),
                                ('LIFE-USDT-SWAP', 'buy'), ('LIFE-USDT-SWAP', 'sell')}
                    return working if families == expected else None

                working = poll(owned_working_quotes, description='current owner restores working maker quotes on both markets')
                proof['working_quotes_after_recovery'] = [o['clOrdId'] for o in working]
                h.mm_command(port, 'stop')
            if scenario == 'container':
                # The unverified legacy scope must expose the rolled-back fill
                # as unresolved, not invent an automatic financial repair while
                # strategy bootstrap is deliberately withheld by the HALT.
                assert h.sql('SELECT count(*) FROM trade_fills') == '0'
                assert len(h.ledger()['fills']) == expected_fills and not h.ledger()['unknown']
                proof.update(durable_fills_after_fault=[], unresolved_venue_fills=h.ledger()['fills'],
                             operator_recovery_required=True)
            else:
                proof['fills'] = exact_fills(h)
                assert len(proof['fills']) == expected_fills
                positions = json.loads(h.sql("SELECT COALESCE(json_agg(p),'[]') FROM "
                                             "(SELECT connector_name,side,amount::text,volume_traded_quote::text,"
                                             "cum_fees_quote::text FROM positions) p"))
                # These cases seed only Spot BUYs. A matching fill table alone
                # must not hide a duplicate/lost held-position application.
                spot = [r for r in positions if r['connector_name'] == 'okx']
                assert len(spot) == 1 and spot[0]['side'] == 'BUY'
                fills = proof['fills']
                assert all(r['market'] == 'okx' for r in fills)
                assert Decimal(spot[0]['amount']) == sum(Decimal(r['amount']) for r in fills)
                assert Decimal(spot[0]['volume_traded_quote']) == sum(Decimal(r['amount']) * Decimal(r['price']) for r in fills)
                assert Decimal(spot[0]['cum_fees_quote']) == sum(Decimal(r['trade_fee_in_quote']) for r in fills)
                assert h.sql("SELECT count(*) FROM runtime_journal WHERE kind='fill' AND NOT applied") == '0'
                proof['native_position_applied_once'] = positions
            proof['final_shutdown'] = stop_after_oom(h, bot)
            proof['passed'] = True
        except BaseException as error:
            proof['failure_type'] = type(error).__name__
            # Keep the durable boundary visible before fixture teardown. Do not
            # fetch unbounded journal payloads or credential envelopes.
            try:
                proof['failure_state'] = h.state()
                proof['failure_journal'] = json.loads(h.sql(
                    "SELECT COALESCE(json_agg(j),'[]') FROM "
                    "(SELECT id,kind,order_id,applied FROM runtime_journal ORDER BY id DESC LIMIT 64) j"))
                proof['failure_positions'] = json.loads(h.sql(
                    "SELECT COALESCE(json_agg(p),'[]') FROM "
                    "(SELECT controller_id,connector_name,trading_pair,side,amount::text,"
                    "cum_fees_quote::text FROM positions LIMIT 16) p"))
            except Exception as diagnostic_error:
                proof['diagnostic_failure_type'] = type(diagnostic_error).__name__
            raise
        finally:
            proof['observer_sigkill_retries'] = sum(OBSERVER_OOM_EXITS.get(name, 0) for name in h.containers)
            (directory / 'result.json').write_text(json.dumps(proof, indent=2))
            h.cleanup()
    return proof


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--image', required=True)
    scenarios = ('api', 'engine', 'engine-fill', 'engine-commit', 'engine-hold', 'container-mm', 'container')
    parser.add_argument('--scenario', choices=('all', *scenarios), default='all')
    parser.add_argument('--memory-mib', type=int, choices=(1024, 1536, 2048), default=1024)
    args = parser.parse_args()
    image = pin_manifest(args.image)
    directory = ROOT / 'build/mm-oom' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    directory.mkdir(parents=True)
    result = dict(image=image, diagnostic=True, passed=False, cases={})
    result['harness_sha256'] = {name: hashlib.sha256((ROOT / 'scripts' / name).read_bytes()).hexdigest()
                                for name in ('test_mm_oom.py', 'test_e2e_offline.py', 'test_mm_load.py')}
    try:
        for scenario in (scenarios if args.scenario == 'all' else (args.scenario,)):
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
