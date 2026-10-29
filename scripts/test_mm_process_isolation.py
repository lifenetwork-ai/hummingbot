"""Diagnostic only: one container, combined versus isolated API/engine loops.

Reuses the deployment image, existing SQL/API/engine and isolated exchange
harness. Nothing in process_isolation/ is a production entrypoint or endpoint.
"""
import argparse
import hashlib
import json
import math
import platform
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from test.e2e.offline.build_fixtures import enable_volatility_defense  # noqa: E402

from scripts.mm_load_budgets import violations  # noqa: E402
from scripts.test_e2e_offline import Harness, docker, http, poll  # noqa: E402
from scripts.test_mm_load import assets_for, command, seed_history, verify_ledger  # noqa: E402


def prepare(assets, mode):
    assets_for(assets)
    prototypes = ROOT / 'test/e2e/offline/process_isolation'
    for path in prototypes.glob('*.py'):
        (assets / path.name).write_bytes(path.read_bytes())
    # Earlier immutable images lack the promoted module. Mount its exact pure
    # policy rather than maintaining a second implementation for diagnostics.
    (assets / 'lifecycle.py').write_bytes((ROOT / 'hummingbot/cli/process_supervision.py').read_bytes())
    (assets / 'load_service.py').write_bytes((prototypes / ('supervisor.py' if mode == 'split' else 'engine.py')).read_bytes())
    path = assets / 'life_mm_profiles.yaml'
    profiles = yaml.safe_load(path.read_text())
    profiles.update(max_global_drawdown_quote='1000', max_controller_drawdown_quote='500',
                    spot_outflow=dict(window_seconds=86400, max_net_outflow_quote='100000', fee_allowance_rate='.01'))
    for market in profiles['markets'].values():
        market.update(initial_anchor='.1')
        bootstrap = dict(duration_seconds=300, max_anchor_move_bps='1000', max_quote=market['total_amount_quote'],
                         min_external_depth_quote='50', max_market_spread_bps='250', qualification_seconds='1')
        market['quote_guards'] = dict(enabled=True, max_deviation_bps='200', resume_deviation_bps='100',
                                      recovery_delay_seconds='1', mode='bootstrap_then_market', bootstrap=bootstrap)
    profiles['markets']['spot']['inventory_skew'] = dict(enabled=True, target_base_pct='.01', inventory_range_multiplier='10')
    enable_volatility_defense(profiles)
    path.write_text(yaml.safe_dump(profiles))


def client_trace_options(directory, phase):
    """Each client process owns its trace/summary; retain both load windows."""
    if directory is None:
        return []
    assert phase in ('warmup', 'healthy')
    path = directory / phase
    path.mkdir()
    path.chmod(0o777)
    return ['--env', f'HBOT_LOAD_TRACE_DIRECTORY=/trace-output/{phase}']


def working_quotes_after_recovery(harness):
    # The fixture refreshes at 60s; cancellation and replacement are separate
    # controller ticks. API recovery can finish between them. Permanent loss
    # still fails; partially filled orders remain executable working quotes.
    return poll(lambda: [order for order in harness.ledger()['orders']
                         if order['state'] in ('live', 'partially_filled')],
                timeout=30, description='working quotes return after API recovery/normal refresh')


def recovery_faults(harness, bot, port, case, *, pids_provider=None):
    """Real process faults: no HALT reset, no reconstructed trading authority."""
    def pids():
        if pids_provider is not None:
            return pids_provider()
        return json.loads(docker('exec', bot, 'cat', '/tmp/architecture-children.json').stdout)

    def signal_child(pid, signum):
        docker('exec', bot, 'python', '-c', 'import os,sys;os.kill(int(sys.argv[1]),int(sys.argv[2]))', pid, signum)

    def replaced(index, old):
        values = pids()
        return values if values[index] not in (None, old) else None

    parent_start = json.loads(docker('inspect', bot).stdout)[0]['State']['StartedAt']
    command(harness, port, 'switch', market_mode='both', selection='fixed', profile_id='follow_market', duration_seconds=3600)
    poll(lambda: len([o for o in harness.ledger()['orders'] if o['state'] == 'live']) == 12,
         description='fixed session before recovery faults')
    baseline = harness.state()
    engine_pid = pids()[0]
    positions = harness.ledger()['positions']
    case['api_restarts'] = []
    for signum, name in ((9, 'crash'), (9, 'repeated_crash'), (19, 'unresponsive_loop')):
        print('split supervised API: ' + name, flush=True)
        old = pids()[1]
        started = time.monotonic()
        signal_child(old, signum)
        new_pids = poll(lambda: replaced(1, old), timeout=90, description='API child replaced independently')
        poll(lambda: http(port, '/startup')[0] == 200, timeout=180, description='restarted API schema/engine ready')
        if name == 'unresponsive_loop':
            # Deterministically exercise the boundary that made the former
            # instantaneous assertion flaky, using the actual controller cycle.
            poll(lambda: not any(o['state'] in ('live', 'partially_filled') for o in harness.ledger()['orders']),
                 timeout=90, description='ordinary quote refresh cancellation boundary')
            case['api_refresh_boundary_exercised'] = True
        working = working_quotes_after_recovery(harness)
        status = harness.admin(port, '/status')[1]
        state = harness.state()
        assert new_pids[0] == engine_pid and pids()[0] == engine_pid
        assert state['owner_generation'] == baseline['owner_generation'] and state['owner_uuid'] == baseline['owner_uuid']
        assert state['mm_session']['id'] == baseline['mm_session']['id']
        assert status['running'] and status['owner_available']
        assert harness.ledger()['positions'] == positions
        case['api_restarts'].append(dict(fault=name, recovery_seconds=time.monotonic() - started,
                                         engine_identity_preserved=True, owner_and_session_preserved=True,
                                         positions_preserved=True, working_quotes_recovered=True,
                                         working_quote_count=len(working), quote_check='bounded_after_normal_refresh'))

    poll(lambda: harness.sql('SELECT count(*) FROM orders WHERE creation_pending') == '0',
         description='committed order acknowledgments before engine crash')
    accepted = len(harness.ledger()['accepted'])
    api_pid = pids()[1]
    case['engine_restarts'] = []
    for signum, name in ((9, 'active_engine_crash'), (19, 'halted_engine_unresponsive_loop')):
        print('split supervised engine: ' + name, flush=True)
        old = pids()[0]
        old_generation = harness.state()['owner_generation']
        started = time.monotonic()
        signal_child(old, signum)
        assert http(port, '/live')[0] == 200
        new_pids = poll(lambda: replaced(0, old), timeout=90, description='engine child replaced after physical exit')
        assert new_pids[1] == api_pid
        poll(lambda: (harness.state().get('status') or {}).get('phase') == 'halted'
             and harness.state()['owner_generation'] > old_generation,
             timeout=180, description='restarted owner retains unresolved-effect HALT')
        state = harness.state()
        status = harness.admin(port, '/status')[1]
        assert status['owner_available'] and not status['running'] and state['recovery_state']['halt']
        assert len(harness.ledger()['accepted']) == accepted
        assert harness.ledger()['positions'] == positions
        assert not harness.ledger()['unknown']
        assert docker('exec', bot, 'test', '!', '-d', f'/proc/{old}', check=False).returncode == 0
        case['engine_restarts'].append(dict(fault=name, recovery_seconds=time.monotonic() - started,
                                            old_process_absent=True, api_identity_preserved=True,
                                            owner_generation_advanced=True, halt_preserved=True,
                                            no_additional_orders=True, positions_preserved=True))
    assert json.loads(docker('inspect', bot).stdout)[0]['State']['StartedAt'] == parent_start
    case['parent_restarts'] = 0
    started = time.monotonic()
    harness.terminate(bot)
    case['parent_shutdown_seconds'] = time.monotonic() - started
    state = json.loads(docker('inspect', bot).stdout)[0]['State']
    assert state['ExitCode'] == 0 and not state['OOMKilled']
    assert harness.state()['owner_uuid'] is None
    case['parent_shutdown_owner_released'] = True


def run_case(image, args, mode, directory, case):
    with tempfile.TemporaryDirectory(prefix='hbot-process-isolation-') as temporary:
        assets = Path(temporary)
        prepare(assets, mode)
        case['fixture_sha256'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in assets.iterdir()}
        executed = directory / 'executed-prototype'
        executed.mkdir()
        for path in (ROOT / 'test/e2e/offline/process_isolation').glob('*.py'):
            (executed / path.name).write_bytes((assets / path.name).read_bytes())
        (directory / 'effective-profiles.yaml').write_bytes((assets / 'life_mm_profiles.yaml').read_bytes())
        harness = Harness(image, assets, directory, 'demo')
        harness.env.update(ADMIN_API_ENABLED='true', ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin',
                           FUNDING_SYNC_INTERVAL_SECONDS='5',
                           ADMIN_STATISTICS_CACHE_TTL_SECONDS=str(args.daily_cache_ttl_seconds))
        if args.supervision == 'restart':
            harness.env['ARCH_EXPERIMENT_SUPERVISION'] = 'restart'
        monitor_stop, monitor, monitor_errors = threading.Event(), None, []
        try:
            harness.setup()
            harness.control(include_own_orders=True)
            for name, cpus in ((harness.simulator, 1), (harness.pg, 2)):
                docker('update', '--cpus', cpus, '--memory', '512m', '--memory-swap', '512m', name)
            # instrumented selects the explicit test wrapper path; it does not
            # install load_service metrics because prepare replaced that file.
            trace_directory = directory / 'trace' if args.trace else None
            if trace_directory is not None:
                trace_directory.mkdir()
                trace_directory.chmod(0o777)
            bot, port = harness.start('load-bot', instrumented=True, trace_directory=trace_directory,
                                      instrumented_entrypoint='load_service.py')
            docker('update', '--cpus', args.cpus, '--memory', '2g', '--memory-swap', '2g', bot)
            poll(lambda: bool(harness.state().get('status')), description='published owner observation')
            generation = harness.state()['owner_generation']
            command(harness, port, 'start', market_mode='spot', selection='fixed', profile_id='sideways', duration_seconds=600)
            poll(lambda: bool(harness.state()['recovery_state'].get('spot_outflow', {}).get('binding')), description='empty-history binding')
            command(harness, port, 'stop')
            seed_history(harness, args.history_rows, active_outflow=True)
            controls = {}
            for action in ('start', 'switch', 'stop'):
                values = {} if action == 'stop' else dict(market_mode='both', selection='fixed', profile_id='follow_market', duration_seconds=86400)
                controls[action], _ = command(harness, port, action, **values)
            command(harness, port, 'start', market_mode='both', selection='fixed', profile_id='follow_market', duration_seconds=86400)
            poll(lambda: len([o for o in harness.ledger()['orders'] if o['state'] == 'live']) == 12, description='twelve working quotes')
            client = harness.prefix + '-load-client'
            client_mounts = ['--mount', f'type=bind,source={trace_directory},target=/trace-output'] if args.trace else []
            client_env = {'HBOT_LOAD_TRACE_DIRECTORY': '/trace-output',
                          'HBOT_LOAD_TRACE_DATABASE_URL': harness.env['DATABASE_URL']} if args.trace else None
            harness.run_container(client, ['--platform', 'linux/amd64', '--cpus', '1', '--memory', '512m', '--memory-swap', '512m',
                                           '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m', '--mount',
                                           f'type=bind,source={assets},target=/test-assets,readonly', *client_mounts],
                                  command=['python', '-c', 'import time;time.sleep(20000)'], env=client_env)
            if args.warmup_seconds:
                case['memory_samples'] = []

                def sample_memory():
                    while not monitor_stop.is_set():
                        try:
                            value = memory_sample(bot)
                        except Exception as error:
                            monitor_errors.append(type(error).__name__)
                            return
                        case['memory_samples'].append(value)
                        (directory / 'memory-minutes.json').write_text(json.dumps(case['memory_samples'], indent=2) + '\n')
                        print(mode, 'memory sample', len(case['memory_samples']), value['roles'], flush=True)
                        monitor_stop.wait(60)

                monitor = threading.Thread(target=sample_memory, daemon=True)
                monitor.start()
                print(f'{mode}: {args.warmup_seconds}s warmup diagnostic', flush=True)
                warmup = docker('exec', *client_trace_options(trace_directory, 'warmup'), client,
                                'python', '/test-assets/load_client.py', '--duration', args.warmup_seconds,
                                '--output', '/tmp/warmup.json', '--operations', timeout=args.warmup_seconds + 60, check=False)
                (directory / 'warmup-client.log').write_text(warmup.stdout + warmup.stderr)
                assert warmup.returncode == 0, 'Inspect retained warmup log'
                row = json.loads(docker('exec', client, 'cat', '/tmp/warmup.json').stdout)
                row.update(command_seconds=row['control_results'].get('command_seconds', controls),
                           runtime={'runtime_phase': harness.admin(port, '/status')[1]['phase']},
                           peak_rss_bytes=int(docker('exec', bot, 'cat', '/sys/fs/cgroup/memory.peak').stdout),
                           memory_measurement='cgroup peak upper bound, not process RSS',
                           ledger=verify_ledger(harness, port, require_running=False))
                row['violations'] = violations(row)
                case['warmup'] = row
                (directory / 'warmup.json').write_text(json.dumps(row, indent=2) + '\n')
                print(mode, 'warmup', row['read_p99_ms'], row['violations'], flush=True)
            print(f'{mode}: {args.duration}s healthy diagnostic', flush=True)
            before = cpu_counters(bot)
            started = time.monotonic()
            call = docker('exec', *client_trace_options(trace_directory, 'healthy'), client,
                          'python', '/test-assets/load_client.py', '--duration', args.duration,
                          '--output', '/tmp/healthy.json', '--operations', timeout=args.duration + 60, check=False)
            (directory / 'client.log').write_text(call.stdout + call.stderr)
            assert call.returncode == 0, 'Inspect retained client log'
            row = json.loads(docker('exec', client, 'cat', '/tmp/healthy.json').stdout)
            case['healthy'] = row
            (directory / 'healthy-client.json').write_text(json.dumps(row, indent=2) + '\n')
            after = cpu_counters(bot)
            row.update(cgroup_cpu_delta={key: after[key] - before[key] for key in before},
                       cgroup_sample_span_seconds=time.monotonic() - started,
                       command_seconds=row['control_results'].get('command_seconds', controls),
                       runtime={'runtime_phase': harness.admin(port, '/status')[1]['phase']},
                       ledger=verify_ledger(harness, port, require_running=False),
                       peak_rss_bytes=int(docker('exec', bot, 'cat', '/sys/fs/cgroup/memory.peak').stdout),
                       memory_measurement='cgroup memory.peak: conservative RSS upper bound including page cache; diagnostic only')
            row['violations'] = violations(row)
            case['healthy'] = row
            monitor_stop.set()
            if monitor is not None:
                monitor.join(timeout=10)
                assert not monitor.is_alive() and not monitor_errors, 'Memory sampling incomplete; inspect retained diagnostics'
            code = (assets / 'stall_client.py').read_text()
            if mode == 'single':
                code = code.replace('bot:8081/diagnostic', 'bot:8080/diagnostic')
            docker('exec', client, 'python', '-c', code, timeout=30)
            case['stall'] = json.loads(docker('exec', client, 'cat', '/tmp/stall-result.json').stdout)
            status = harness.admin(port, '/status')[1]
            assert status['owner_available'] and status['running']
            assert harness.state()['owner_generation'] == generation
            case['engine_survived_stall'] = True
            if mode == 'split' and args.supervision == 'restart':
                recovery_faults(harness, bot, port, case)
                case['completed'] = True
                return
            command(harness, port, 'stop')
            if mode == 'split':
                pids = json.loads(docker('exec', bot, 'cat', '/tmp/architecture-children.json').stdout)
                probe = ("from pathlib import Path;import json,sys;"
                         "keys=[p.split('=',1)[0] for p in Path('/proc/'+sys.argv[1]+'/environ').read_bytes().decode().split(chr(0)) if p];"
                         "print(json.dumps([k for k in keys if k.startswith('HBOT_OKX')]))")
                case['api_exchange_credential_keys'] = json.loads(docker('exec', bot, 'python', '-c', probe, pids[1]).stdout)
                assert not case['api_exchange_credential_keys']
                started = time.monotonic()
                docker('exec', bot, 'python', '-c', 'import os,signal,sys;os.kill(int(sys.argv[1]),signal.SIGKILL)', pids[0])
                poll(lambda: json.loads(docker('inspect', bot).stdout)[0]['State']['Status'] == 'exited',
                     timeout=9.5, description='whole-container exit after engine death')
                state = json.loads(docker('inspect', bot).stdout)[0]['State']
                case['engine_death'] = dict(whole_container_exit_seconds=time.monotonic() - started,
                                            exit_code=state['ExitCode'], oom_killed=state['OOMKilled'])
                assert state['ExitCode'] != 0 and not state['OOMKilled']
                # Restart only after the test's acknowledged Stop. An API death
                # must also terminate this prototype's peer without leaving an
                # owner session or an orphan process behind.
                restarted, restarted_port = harness.start('api-crash', instrumented=True, instrumented_entrypoint='load_service.py')
                docker('update', '--cpus', args.cpus, '--memory', '2g', '--memory-swap', '2g', restarted)
                assert harness.admin(restarted_port, '/status')[1]['desired_state'] == 'stopped'
                command(harness, restarted_port, 'start', market_mode='both', selection='fixed',
                        profile_id='follow_market', duration_seconds=600)
                poll(lambda: len([o for o in harness.ledger()['orders'] if o['state'] == 'live']) == 12,
                     description='working quotes before API death')
                positions = harness.ledger()['positions']
                api_pid = json.loads(docker('exec', restarted, 'cat', '/tmp/architecture-children.json').stdout)[1]
                started = time.monotonic()
                docker('exec', restarted, 'python', '-c', 'import os,signal,sys;os.kill(int(sys.argv[1]),signal.SIGKILL)', api_pid)
                poll(lambda: json.loads(docker('inspect', restarted).stdout)[0]['State']['Status'] == 'exited',
                     timeout=9.5, description='whole-container exit after API death')
                state = json.loads(docker('inspect', restarted).stdout)[0]['State']
                case['api_death'] = dict(whole_container_exit_seconds=time.monotonic() - started,
                                         exit_code=state['ExitCode'], owner_released=harness.state()['owner_uuid'] is None,
                                         positions_preserved=harness.ledger()['positions'] == positions,
                                         working_orders_cancelled=not any(o['state'] == 'live' for o in harness.ledger()['orders']))
                assert state['ExitCode'] != 0 and case['api_death']['owner_released']
                assert case['api_death']['positions_preserved'] and case['api_death']['working_orders_cancelled']
            else:
                harness.terminate(bot)
            case['completed'] = True
        except BaseException as error:
            case['failure_type'] = type(error).__name__
            raise
        finally:
            monitor_stop.set()
            if monitor is not None:
                monitor.join(timeout=10)
            harness.cleanup()


def cpu_counters(bot):
    return {key: int(value) for key, value in
            (line.split() for line in docker('exec', bot, 'cat', '/sys/fs/cgroup/cpu.stat').stdout.splitlines())}


def memory_sample(bot):
    code = """import json,time
from pathlib import Path
children=Path('/tmp/architecture-children.json')
pids=list(zip(('supervisor','engine','api'),[1,*json.loads(children.read_text())])) if children.is_file() else [('engine',1)]
roles={}
for role,pid in pids:
    if pid is None:
        continue
    try:
        status=Path('/proc/'+str(pid)+'/status').read_text()
        row={line.split(':')[0]:int(line.split()[1]) for line in status.splitlines() if line.startswith(('VmRSS:','VmHWM:','Threads:'))}
        row['fds']=len(list(Path('/proc/'+str(pid)+'/fd').iterdir()))
        roles[role]=row
    except FileNotFoundError:
        pass
print(json.dumps(dict(at=time.time(),roles=roles,cgroup_memory_bytes=int(Path('/sys/fs/cgroup/memory.current').read_text()))))"""
    return json.loads(docker('exec', bot, 'python', '-c', code).stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default='hummingbot-cloudrun:local')
    parser.add_argument('--cpus', type=int, choices=(1, 2), default=1)
    parser.add_argument('--mode', choices=('both', 'single', 'split'), default='both')
    parser.add_argument('--duration', type=int, default=90)
    parser.add_argument('--history-rows', type=int, default=100000)
    parser.add_argument('--warmup-seconds', type=int, choices=(0, 300), default=0)
    parser.add_argument('--trace', action='store_true', help='Existing client/DB sampler plus bounded API loop/GC probe')
    parser.add_argument('--daily-cache-ttl-seconds', type=float, default=5,
                        help='Diagnostic comparison using the existing reporting cache; production default unchanged')
    parser.add_argument('--supervision', choices=('fail-fast', 'restart'), default='fail-fast',
                        help='Test-only independent API/engine restart and sustained-stall recovery')
    args = parser.parse_args()
    if not math.isfinite(args.daily_cache_ttl_seconds) or not 0 <= args.daily_cache_ttl_seconds <= 60:
        parser.error('daily-cache-ttl-seconds must be finite and between 0 and 60')
    if not 30 <= args.duration <= 900 or not 0 <= args.history_rows <= 1000000:
        parser.error('duration must be 30..900 seconds and history-rows 0..1000000')
    if args.supervision == 'restart' and args.mode != 'split':
        parser.error('restart supervision requires --mode split')
    if args.trace and args.mode != 'split':
        parser.error('trace requires --mode split')
    metadata = json.loads(docker('image', 'inspect', args.image).stdout)[0]
    assert metadata['Architecture'] == 'amd64'
    directory = ROOT / 'build/mm-process-isolation' / (time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    directory.mkdir(parents=True)
    evidence = dict(scope='Diagnostic single-service/single-container process isolation experiment; not release acceptance',
                    image_id=metadata['Id'], host_architecture=platform.machine(), image_architecture='amd64',
                    diagnostic=True, release_scope_complete=False, cases={},
                    bot_total_cpus=args.cpus, bot_total_memory_mib=2048, total_db_pool_slots=5,
                    postgres_cpus=2, postgres_memory_mib=512, history_fills=args.history_rows, duration_seconds=args.duration)
    evidence.update(supervision=args.supervision, warmup_seconds=args.warmup_seconds, tracing=args.trace,
                    daily_cache_ttl_seconds=args.daily_cache_ttl_seconds)
    try:
        for mode in (('single', 'split') if args.mode == 'both' else (args.mode,)):
            case = evidence['cases'][mode] = {}
            case_dir = directory / mode
            case_dir.mkdir()
            run_case(metadata['Id'], args, mode, case_dir, case)
            print(mode, case['healthy']['read_p99_ms'], case['healthy']['violations'], case['stall']['p99_ms'], flush=True)
    finally:
        (directory / 'result.json').write_text(json.dumps(evidence, indent=2) + '\n')
        print(f'Evidence: {directory}', flush=True)
    # Healthy latency violations remain visible; a successful fault experiment
    # must not turn them into a passing load result.
    return int(any(case['healthy']['violations'] or case.get('warmup', {}).get('violations')
                   for case in evidence['cases'].values()))


if __name__ == '__main__':
    sys.exit(main())
