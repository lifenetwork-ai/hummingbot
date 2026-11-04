"""History fixture preparation for disposable offline load databases only."""
import json
import time

from scripts.test_e2e_offline import docker, http, poll


def _stopped(row):
    assert row['desired_state'] == 'stopped', 'Fixture preparation requires a stopped trading intent'
    assert row['acknowledged_version'] == row['control_version'], 'Stop must be acknowledged before preparation'
    state = row.get('recovery_state') or {}
    assert not any(state.get(k) for k in ('halt', 'pending_mutations', 'runtime_active')), 'Unresolved recovery state'
    status = row.get('status') or {}
    engine = status.get('engine') or {}
    assert status.get('running') is False, 'Strategy must be stopped before preparation'
    assert all(engine.get(k) is False for k in ('strategy_running', 'clock_running')), 'Trading core must be stopped'


def _no_unexpected_restart(bot):
    logs = docker('logs', bot)
    for line in (logs.stdout + logs.stderr).splitlines():
        if not line.startswith('{'):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        assert row.get('event') not in {'service_failed', 'child_restart_scheduled'}, 'Unexpected service failure/restart during preparation'


def prepare_history(harness, bot, count, *, active_outflow, seed, evidence):
    assert type(count) is int and 0 <= count <= 1000000, 'Invalid fixture history count'
    evidence.update(history_rows=count, mode='full_service_offline_seed')
    if count == 0:
        evidence['stage'] = 'skipped'
        return
    evidence['stage'] = 'verify_stop'
    before = harness.state()
    _stopped(before)
    assert before['owner_uuid'], 'Initial service owner is unavailable'
    _no_unexpected_restart(bot)
    ledger = harness.ledger()
    assert not ledger['unknown'], 'Unknown simulator effects before offline preparation'
    assert all(o['state'] in {'filled', 'canceled'} for o in ledger['orders']), 'Venue orders must be stopped'
    evidence.update(owner_generation_before=before['owner_generation'], control_version=before['control_version'])

    evidence['stage'] = 'stop_service'
    harness.terminate(bot)
    physical = json.loads(docker('inspect', bot).stdout)[0]['State']
    assert physical['Status'] == 'exited' and physical['ExitCode'] == 0 and not physical['OOMKilled'], 'Service is not cleanly offline'
    baseline = harness.state()
    assert baseline['owner_uuid'] is None, 'Stopped service did not release its owner'
    _stopped(baseline)
    assert baseline['control_version'] == before['control_version'], 'Control state changed during shutdown'
    from hummingbot.core.bot_ownership import OWNER_KEY, OWNER_NAMESPACE
    quiet = json.loads(harness.sql(f"""SELECT json_build_object(
        'application_connections', (SELECT count(*) FROM pg_stat_activity
            WHERE datname=current_database() AND application_name='hummingbot'),
        'ownership_locks', (SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND granted
            AND classid={OWNER_NAMESPACE} AND objid={OWNER_KEY} AND objsubid=2
            AND database=(SELECT oid FROM pg_database WHERE datname=current_database())),
        'pending_orders', (SELECT count(*) FROM orders WHERE creation_pending))"""))
    assert all(quiet[k] == 0 for k in ('application_connections', 'ownership_locks', 'pending_orders')), \
        'Offline fixture still has application connections, ownership or pending orders'
    evidence.update(offline_verified_at=time.time(), offline_checks=quiet, stage='seed_history')

    # A fixture failure leaves the service stopped. Do not restart in finally,
    # reset recovery, or change any runtime connection/statement deadline.
    seed(harness, count, active_outflow=active_outflow, sql_timeout=180)
    evidence.update(seed_completed_at=time.time(), stage='checkpoint')
    harness.sql('CHECKPOINT', timeout=180)
    assert harness.state() == baseline, 'Bot state changed during offline fixture writes'
    counts = json.loads(harness.sql("""SELECT json_build_object(
        'orders', (SELECT count(*) FROM orders WHERE config_file_path='load-history.yaml'),
        'fills', (SELECT count(*) FROM trade_fills WHERE config_file_path='load-history.yaml'))"""))
    assert counts == {'orders': count, 'fills': count}, 'Incomplete history fixture'
    evidence.update(checkpoint_completed_at=time.time(), seeded_counts=counts, stage='restart_service')

    evidence['restarted_at'] = time.time()
    docker('start', bot)
    poll(lambda: http(bot, '/startup')[0] == 200, timeout=200, description='post-seed startup through normal migrations')
    assert http(bot, '/live')[0] == 200, 'Post-seed service is not live'

    def acquired():
        row = harness.state()
        if (not row['owner_uuid'] or row['owner_uuid'] == before['owner_uuid']
                or row['owner_generation'] <= before['owner_generation']):
            return None
        _stopped(row)
        assert row['control_version'] == baseline['control_version'], 'Control state changed after restart'
        assert row['mm_session'] == baseline['mm_session'], 'Session intent changed after restart'
        assert (row.get('recovery_state') or {}).get('spot_outflow') == (baseline.get('recovery_state') or {}).get('spot_outflow'), \
            'Verified outflow origin changed after restart'
        return row
    restored = poll(acquired, timeout=30, description='new post-seed owner generation and stopped recovery state')
    _no_unexpected_restart(bot)
    evidence.update(owner_generation_after=restored['owner_generation'], completed_at=time.time(), stage='complete')
