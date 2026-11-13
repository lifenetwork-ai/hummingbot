"""Populated-quarantine lifecycle faults on an internal, credential-free network."""
import json

from scripts.test_e2e_offline import docker, http, poll

SCENARIOS = ('scoped-rollover', 'scoped-disconnect', 'scoped-oom')


def configure(harness):
    import yaml

    path = harness.assets / 'life_mm_profiles.yaml'
    original = path.read_bytes()
    profiles = yaml.safe_load(original)
    profiles['spot_outflow'] = dict(window_seconds=300, max_net_outflow_quote='100', fee_allowance_rate='.01')
    path.write_text(yaml.safe_dump(profiles))
    harness.env.update(MM_SCOPED_RECOVERY_ENABLED='true', ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin')
    harness.control(submission_busy_side='buy')
    return path, original


def degraded(harness):
    state, ledger = harness.state(), harness.ledger()
    recovery = state['recovery_state'].get('automatic_recovery', {})
    working = [row for row in ledger['orders'] if row['side'] == 'sell' and row['state'] == 'live']
    return (state, ledger) if recovery.get('isolated') and working and not state['recovery_state'].get('halt') else None


def pending_ids(harness):
    rows = [r['order']['clOrdId'] for r in harness.ledger()['requests'] if r.get('injected_submission_busy')]
    assert rows and len(rows) == len(set(rows)), 'Unknown POST was repeated'
    return set(rows)


def retained(harness, identities):
    assert pending_ids(harness) == identities, 'A new BUY escaped quarantine admission'
    rows = json.loads(harness.sql("SELECT COALESCE(json_agg(t),'[]') FROM (SELECT id,creation_pending,exchange_order_id "
                                  "FROM orders WHERE creation_pending) t"))
    assert {r['id'] for r in rows} == identities
    assert all(r['creation_pending'] and r['exchange_order_id'] is None for r in rows)
    assert not harness.ledger()['unknown']


def rollover(harness):
    import yaml

    path, original = configure(harness)
    profiles = yaml.safe_load(path.read_text())
    profiles['random'] = dict(profile_ids=['follow_market', 'sideways'], step_duration_seconds=30)
    path.write_text(yaml.safe_dump(profiles))
    bot, port = harness.start('scoped-rollover', cpus=2, memory_mib=1024)
    harness.mm_command(port, 'start', wait=False, market_mode='spot', selection='random',
                       duration_mode='until_stopped')
    before, _ = poll(lambda: degraded(harness), timeout=60, description='unknown BUY and working SELL before timed rollover')
    identities = pending_ids(harness)
    first_generation = before['mm_session']['generation']

    def renewed():
        current = degraded(harness)
        return current if current and current[0]['mm_session']['generation'] > first_generation else None

    after, _ = poll(renewed, timeout=75, description='timed profile rollover restores funded SELL with old BUY reserved')
    assert after['mm_session']['id'] == before['mm_session']['id']
    assert after['control_version'] == before['control_version']
    assert (after['owner_uuid'], after['owner_generation']) == (before['owner_uuid'], before['owner_generation'])
    retained(harness, identities)
    assert http(port, '/startup')[0] == http(port, '/live')[0] == 200
    assert http(port, '/ready')[0] == 503
    harness.mm_command(port, 'stop')
    assert not harness.state()['recovery_state'].get('halt')
    harness.terminate(bot)
    harness.metrics['scoped_rollover'] = dict(unresolved_count=len(identities), no_new_buy=True,
                                              generation_before=first_generation, generation_after=after['mm_session']['generation'],
                                              owner_preserved=True, funded_sell=True, simulated=True)
    path.write_bytes(original)


def disconnect(harness):
    from scripts.mm_managed_lifecycle import initialize, operation, slot

    path, original = configure(harness)
    bot, port = initialize(harness, cpus=2, memory_mib=1024)
    harness.mm_command(port, 'start', wait=False, market_mode='spot', selection='fixed',
                       profile_id='follow_market', duration_mode='until_stopped')
    before, _ = poll(lambda: degraded(harness), timeout=60, description='managed-key quarantine before Disconnect')
    identities = pending_ids(harness)
    connector = 'okx'
    payload = dict(expected_version=slot(harness, port, connector)['version'],
                   expected_control_version=harness.state()['control_version'])
    route = '/credentials/okx/' + connector + '/revoke'
    code, receipt = harness.admin(port, route, payload, key='scoped-disconnect')
    assert code == 202
    code, replay = harness.admin(port, route, payload, key='scoped-disconnect')
    assert code == 202 and replay['id'] == receipt['id']

    def waiting():
        observed = operation(harness, port, receipt['id'])
        assert observed['status'] != 'succeeded', 'Unknown order cannot authorize credential erasure'
        result = observed.get('result') or {}
        return observed if (result.get('stage') == 'waiting_for_dependency'
                            and result.get('reason_code') == 'cleanup_orders_pending') else None

    observed = poll(waiting, timeout=60, description='Disconnect reports pending order proof without a service HALT')
    assert not observed['result']['operator_action_required'] and observed['result']['next_retry_at']
    # Observe a second coordinator cycle: retaining credentials must also survive
    # retry, rather than reporting a clean first stop followed by restart/HALT.
    retry_at = observed['updated_at']
    observed = poll(lambda: (row if (row := waiting()) and row['updated_at'] != retry_at else None),
                    timeout=30, description='Disconnect retries same operation with uncertainty intact')
    state = harness.state()
    assert state['desired_state'] == 'stopped' and not state['recovery_state'].get('halt')
    assert (state['owner_uuid'], state['owner_generation']) == (before['owner_uuid'], before['owner_generation'])
    retained(harness, identities)
    assert harness.sql("SELECT count(*) FROM exchange_credentials WHERE connector='okx' "
                       "AND state='revoking' AND envelope IS NOT NULL") == '1'
    assert harness.sql("SELECT count(*) FROM admin_audit_events WHERE action='credentials_disconnect' AND outcome='succeeded'") == '0'
    assert http(port, '/startup')[0] == http(port, '/live')[0] == 200
    harness.terminate(bot)
    harness.metrics['scoped_disconnect'] = dict(waiting_for_proof=True, ciphertext_retained=True,
                                                no_false_success=True, owner_preserved=True, retries=True, local_kms=True,
                                                unresolved_count=len(identities), simulated=True)
    path.write_bytes(original)


def oom(harness):
    from scripts.test_mm_oom import (
        arm,
        children,
        exact_fills,
        kernel_oom_proven,
        memory_events,
        release,
        stop_after_oom,
    )

    path, original = configure(harness)
    bot, port = harness.start('scoped-oom', instrumented=True, instrumented_entrypoint='oom_entrypoint.py',
                              cpus=2, memory_mib=1024)
    harness.mm_command(port, 'start', wait=False, market_mode='spot', selection='fixed',
                       profile_id='follow_market', duration_mode='until_stopped')
    before, ledger = poll(lambda: degraded(harness), timeout=60, description='unknown BUY and working SELL before cgroup OOM')
    identities = pending_ids(harness)
    sale = next(row for row in ledger['orders'] if row['side'] == 'sell' and row['state'] == 'live')
    harness.control(fill=dict(clOrdId=sale['clOrdId'], amount='.01', fee='.000001'), duplicate=True)
    poll(lambda: harness.sql('SELECT count(*) FROM trade_fills') == '1',
         timeout=30, description='one partial native SELL fill and original fee committed before OOM')
    fills = exact_fills(harness)
    pids, counters = children(bot), memory_events(bot)
    observer = harness.prefix + '-oom-observer'
    docker('run', '-d', '--name', observer, '--pid', 'container:' + bot, '--network', 'none', '--user', '10001:10001',
           '--read-only', '--memory', '32m', '--memory-swap', '32m', '--cpus', '.1', '--entrypoint', 'sh',
           'postgres:14-alpine', '-c', 'while :; do sleep 3600; done')
    harness.containers.append(observer)
    assert children(observer) == pids
    started = json.loads(docker('inspect', bot).stdout)[0]['State']['StartedAt']
    arm(bot, 'engine', 'now')
    marker = release(bot, 'engine')
    assert marker['pid'] == pids['engine']
    proof = dict(marker=marker, memory_events_before=counters, pids_before=pids)
    (harness.report / 'oom-boundary.json').write_text(json.dumps(proof, indent=2))

    def replacement():
        state = json.loads(docker('inspect', bot).stdout)[0]['State']
        if state['Status'] == 'exited':
            return dict(container_exit={key: state[key] for key in ('Status', 'ExitCode', 'OOMKilled')})
        try:
            row = children(observer)
        except RuntimeError:
            # PID1 and its namespace observer can disappear between the two
            # independent Docker reads. Only an actual exited bot permits this.
            current = json.loads(docker('inspect', bot).stdout)[0]['State']
            if current['Status'] != 'exited':
                raise
            return dict(container_exit={key: current[key] for key in ('Status', 'ExitCode', 'OOMKilled')})
        return dict(pids=row) if row.get('engine') not in (None, pids['engine']) else None

    outcome = poll(replacement, timeout=90, description='physical cgroup OOM outcome is observed outside the pressured cgroup')
    if 'container_exit' in outcome:
        assert kernel_oom_proven({}, {}, container_state=outcome['container_exit'])
        # Cloud Run replaces an OOM-killed container. The separate namespace
        # observer exits with PID1; restarting this fixture models that cold boot.
        docker('start', bot)
        poll(lambda: http(port, '/startup')[0] == 200, timeout=200, description='fresh container starts after proven OOM')
        after, events = children(bot), memory_events(bot)
        proof.update(oom_scope='container', physical_exit=outcome['container_exit'])
    else:
        after, events = outcome['pids'], memory_events(bot)
        assert kernel_oom_proven(counters, events)
        assert json.loads(docker('inspect', bot).stdout)[0]['State']['StartedAt'] == started
        proof.update(oom_scope='engine')
    proof.update(memory_events_after=events, pids_after=after)
    (harness.report / 'oom-boundary.json').write_text(json.dumps(proof, indent=2))

    def recovered():
        current = degraded(harness)
        return current if (current and current[0]['owner_generation'] > before['owner_generation']
                           and current[0]['status'].get('running')
                           and current[0]['status'].get('phase') == 'reconciling_orders') else None

    resumed, _ = poll(recovered, timeout=90, description='fresh owner restores populated quarantine after OOM')
    assert resumed['owner_generation'] > before['owner_generation']
    assert resumed['mm_session']['id'] == before['mm_session']['id']
    retained(harness, identities)
    assert exact_fills(harness) == fills
    assert harness.sql("SELECT count(*) FROM runtime_journal WHERE kind='fill' AND applied") == '1'
    assert http(port, '/startup')[0] == http(port, '/live')[0] == 200
    assert http(port, '/ready')[0] == 503
    harness.mm_command(port, 'stop')
    assert not harness.state()['recovery_state'].get('halt')
    stop_after_oom(harness, bot)
    harness.metrics['scoped_oom'] = dict(cpus=2, memory_mib=1024, memory_events_before=counters,
                                         memory_events_after=events, pids_before=pids, pids_after=after,
                                         original_fee_matched=True, unique_fills=len(fills),
                                         unresolved_count=len(identities), kernel_oom_proven=True, oom_scope=proof['oom_scope'], simulated=True)
    path.write_bytes(original)


def scenario(harness, name):
    {'scoped-rollover': rollover, 'scoped-disconnect': disconnect, 'scoped-oom': oom}[name](harness)
