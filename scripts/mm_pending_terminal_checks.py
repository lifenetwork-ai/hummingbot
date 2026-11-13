"""Full service: missing terminal WS, live account feed, native polling and receipts."""
import json
import re
import time

import yaml


def scenario(harness):
    path = harness.assets / 'life_mm_profiles.yaml'
    original = path.read_bytes()
    try:
        return _run(harness)
    finally:
        path.write_bytes(original)


def _run(harness):
    from scripts.mm_load_ledger import ledger_violations
    from scripts.test_e2e_offline import ROOT, docker, poll

    for name in ('load_entrypoint.py', 'load_service.py', 'load_process_metrics.py', 'load_incidents.py',
                 'load_trace.py', 'load_mm_activity.py'):
        (harness.assets / name).write_bytes((ROOT / 'test/e2e/offline' / name).read_bytes())
    path = harness.assets / 'life_mm_profiles.yaml'
    profiles = yaml.safe_load(path.read_text())
    for market in profiles['markets'].values():
        market.update(executor_refresh_time=20, cooldown_time=0)
    path.write_text(yaml.safe_dump(profiles))
    (harness.report / 'effective-pending-profiles.yaml').write_bytes(path.read_bytes())
    harness.env.update(ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin',
                       HBOT_PENDING_POLL_DIAGNOSTICS='1')
    bot, port = harness.start('pending-terminal-ws', instrumented=True, cpus=2, memory_mib=1024)
    harness.mm_command(port, 'start', market_mode='both', selection='fixed', profile_id='follow_market', duration_seconds=600)
    initial = harness.state()
    session = initial['mm_session']
    owner = initial['owner_uuid'], initial['owner_generation']

    def engine_metrics():
        result = docker('exec', bot, 'cat', '/tmp/load-metrics.json', check=False)
        return json.loads(result.stdout) if result.returncode == 0 else None

    engine_pid = poll(engine_metrics, description='native engine measurement export')['process_id']
    instruments = ('LIFE-USDT', 'LIFE-USDT-SWAP')

    def working():
        return [row for row in harness.ledger()['orders'] if row['state'] in ('live', 'partially_filled')]

    def both_sides():
        rows = working()
        return rows if all(sum(row['instId'] == instrument and row['side'] == side for row in rows) >= 2
                           for instrument in instruments for side in ('buy', 'sell')) else None

    original = poll(both_sides, description='eight acknowledged native maker quotes')
    assert len(original) == 8
    identities = [row['clOrdId'] for row in original]
    assert all(re.fullmatch(r'[A-Za-z0-9]{1,64}', identity) for identity in identities)
    held = [next(row for row in original if row['instId'] == instrument and row['side'] == 'buy')
            for instrument in instruments]
    held_ids = [row['clOrdId'] for row in held]
    selected = ','.join("'" + identity + "'" for identity in held_ids)
    all_selected = ','.join("'" + identity + "'" for identity in identities)
    evidence = harness.metrics['pending_terminal_ws'] = dict(
        stage='arm', targets=identities, withheld=held_ids, process_id=engine_pid, simulated=True)
    harness.control(suppress_terminal_ws_for=identities, withhold_fills_for=held_ids, private_keepalive=True,
                    fill_on_cancel=[dict(clOrdId=row['clOrdId'], amount=row['sz'], fee='0.00001') for row in held])
    armed = time.monotonic()

    def terminal_at_venue():
        ledger = harness.ledger()
        actual = [row for row in ledger['orders'] if row['clOrdId'] in identities]
        return ledger if len(actual) == 8 and all(row['state'] in ('filled', 'canceled') for row in actual) else None

    ledger = poll(terminal_at_venue, timeout=40, description='ordinary refresh reaches actual venue terminal states')
    evidence.update(stage='proof_pending', stimulus_to_venue_terminal_seconds=time.monotonic() - armed)
    assert set(identities) <= {row['clOrdId'] for row in ledger['suppressed_terminal_events']}

    def observed_native_fallback():
        metrics = engine_metrics()
        if metrics is None:
            return None
        totals = metrics['totals']
        return metrics if all(totals.get('pending_fresh_ws_short_ticks.' + name, 0) >= 2
                              and totals.get('pending_fresh_ws_account_reads.' + name, 0) >= 2
                              for name in ('okx', 'okx_perpetual')) else None

    observed = poll(observed_native_fallback, timeout=20,
                    description='native short polls repeat despite fresh unrelated private WS frames')
    (harness.report / 'pending-native-metrics.json').write_text(json.dumps(observed, indent=2))
    ledger = harness.ledger()
    assert ledger['private_keepalive_messages'] > 0
    assert all(ledger['withheld_fill_reads'].get(identity, 0) >= 2 for identity in held_ids)
    assert harness.sql(f"SELECT count(*) FROM trade_fills WHERE order_id IN ({selected})") == '0'
    assert harness.sql(f"SELECT count(*) FROM orders WHERE id IN ({selected}) "
                       "AND last_status IN ('OrderCancelled','BuyOrderCompleted','OrderFailure')") == '0'
    target_levels = json.loads(harness.sql(f"SELECT json_agg(context->'executor') FROM submission_attempts WHERE order_id IN ({selected})"))
    assert len(target_levels) == 2 and all(row.get('level_id') and row.get('connector_name') for row in target_levels)
    assert harness.sql(f"""WITH target AS (SELECT context->'executor' AS config FROM submission_attempts
        WHERE order_id IN ({selected})) SELECT count(*) FROM submission_attempts a JOIN target t ON
        a.context->'executor'->>'connector_name'=t.config->>'connector_name' AND
        a.context->'executor'->>'level_id'=t.config->>'level_id' WHERE a.order_id NOT IN ({selected})""") == '0', \
        'A withheld target level was replaced before complete financial proof'
    state = harness.state()
    assert state['mm_session']['id'] == session['id'] and not state['recovery_state'].get('halt')
    evidence.update(stage='release', retained_without_fills=True, no_early_level_replacement=True,
                    native_fallback_totals=observed['totals'], private_keepalives=ledger['private_keepalive_messages'])
    (harness.report / 'pending-proof-checkpoint.json').write_text(json.dumps(evidence, indent=2))
    released = time.monotonic()
    harness.control(withhold_fills_for=[])

    def recorded():
        return harness.sql(f"SELECT count(*) FROM orders WHERE id IN ({all_selected}) AND NOT creation_pending "
                           "AND last_status IN ('OrderCancelled','BuyOrderCompleted')") == '8'

    poll(recorded, timeout=20, description='native terminal proof commits every original order')
    poll(both_sides, timeout=20, description='fresh proposals restore both native quote sides')
    rows = json.loads(harness.sql("SELECT json_agg(t) FROM (SELECT market,order_id,exchange_trade_id,amount::text AS amount,"
                                  "price::text AS price,trade_fee,trade_fee_in_quote::text AS trade_fee_in_quote FROM trade_fills) t"))
    ledger = harness.ledger()
    assert len(rows) == len(ledger['fills']) == 2 and not ledger_violations(ledger, rows)
    assert len({row['clOrdId'] for row in ledger['accepted']}) == len(ledger['accepted'])
    assert not ledger['unknown'] and engine_metrics()['process_id'] == engine_pid
    final = harness.state()
    assert final['mm_session']['id'] == session['id']
    assert (final['owner_uuid'], final['owner_generation']) == owner
    evidence.update(stage='stop', release_to_quotes_seconds=time.monotonic() - released, exact_fill_receipts=2,
                    original_terminal_messages_dropped=len(ledger['suppressed_terminal_events']), same_owner_process=True)
    harness.control(private_keepalive=False, suppress_terminal_ws_for=[])
    harness.mm_command(port, 'stop')
    assert not working()
    evidence.update(stage='complete', stopped=True, passed=True)
