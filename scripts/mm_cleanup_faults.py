"""Partial native closes and targeted PostgreSQL failures on isolated test data."""
import json
from decimal import Decimal

from scripts.mm_managed_environment import financial_digest
from scripts.mm_managed_lifecycle import (
    audit,
    disconnect,
    initialize,
    operation,
    seed_exposure,
    slot,
    spot_digest,
    verify_cleanup,
)
from scripts.test_e2e_offline import docker, http, poll

SCENARIOS = ('managed-close-partial-ws', 'managed-close-partial-cancel',
             'managed-close-partial-live-crash', 'managed-close-partial-cancel-crash',
             'managed-cleanup-fill-db-fault', 'managed-cleanup-removal-db-fault')


def install_database_fault(harness, target):
    """A real PostgreSQL exception, scoped to one kind of cleanup transaction."""
    if not harness.sql('SELECT current_database()').startswith('hbot_test_'):
        raise ValueError('Cleanup faults require an explicitly isolated test database')
    table, condition = {
        'fill': ('trade_fills', "NEW.position = 'CLOSE'"),
        'removal': ('admin_audit_events', "NEW.action = 'credentials_disconnect' AND NEW.outcome = 'succeeded'"),
    }[target]
    harness.sql(f"""
        CREATE SCHEMA e2e_cleanup_fault;
        CREATE SEQUENCE e2e_cleanup_fault.attempts;
        CREATE FUNCTION e2e_cleanup_fault.reject_write() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM nextval('e2e_cleanup_fault.attempts');
            RAISE EXCEPTION 'offline cleanup transaction failure' USING ERRCODE = '40001';
        END $$;
        CREATE TRIGGER e2e_cleanup_transaction_failure BEFORE INSERT ON {table}
        FOR EACH ROW WHEN ({condition}) EXECUTE FUNCTION e2e_cleanup_fault.reject_write();
    """)


def database_fault_attempts(harness):
    # Sequence increments survive the failed transaction; a rolled-back table
    # counter would conceal whether the intended native write ever reached DB.
    return int(harness.sql('SELECT CASE WHEN is_called THEN last_value ELSE 0 END FROM e2e_cleanup_fault.attempts'))


def disarm_database_fault(harness, target):
    if target not in ('fill', 'removal') or not harness.sql('SELECT current_database()').startswith('hbot_test_'):
        raise ValueError('Refusing cleanup fault removal outside test scope')
    harness.sql('DROP SCHEMA IF EXISTS e2e_cleanup_fault CASCADE')


def close_sends(harness):
    return [row['order'] for row in harness.ledger()['requests'] if row.get('order', {}).get('ordType') == 'market']


def partial_scenario(harness, outcome, *, crash=False):
    print('Offline E2E: native partial close with WS loss, outcome=' + outcome, flush=True)
    bot, port = initialize(harness)
    seed_exposure(harness, port)
    generation = harness.state()['owner_generation']
    spot = spot_digest(harness)
    harness.control(cleanup_market_closes=True, cleanup_partial=outcome, cleanup_barrier='verified_fill')
    root = disconnect(harness, port)
    event = poll(lambda: harness.ledger()['cleanup_partial_event'], description='venue partial close and WS interruption')
    assert event['outcome'] == outcome and event['closed_socket_count'] > 0
    poll(lambda: harness.sql('SELECT count(*) FROM trade_fills') == '4', timeout=45,
         description='partial close recovered from REST and committed exactly once')
    first = next(row for row in harness.ledger()['orders'] if row['clOrdId'] == event['clOrdId'])
    assert Decimal(first['accFillSz']) == Decimal('.4') and Decimal(first['sz']) == 1
    assert first['state'] == ('partially_filled' if outcome == 'live' else 'canceled')
    assert len(close_sends(harness)) == 1, 'No residual close before terminal readback and renewed position proof'
    assert operation(harness, port, root['id'])['status'] != 'succeeded'
    assert harness.sql("SELECT count(*) FROM exchange_credentials WHERE connector='okx_perpetual' AND envelope IS NOT NULL AND state='revoking'") == '1'
    expected_side = 'BUY' if first['posSide'] == 'long' else 'SELL'
    assert Decimal(harness.sql("SELECT amount FROM positions WHERE connector_name='okx_perpetual' AND side='" + expected_side + "'")) == Decimal('.006')
    checkpoint = dict(operation_id=root['id'], outcome=outcome, partial_close_id=first['clOrdId'],
                      wire_filled=first['accFillSz'], base_remaining='.006', credential_retained=True,
                      physical_attempts_before_release=len(close_sends(harness)), ws_connections_dropped=event['closed_socket_count'])
    (harness.report / 'partial-checkpoint.json').write_text(json.dumps(checkpoint, indent=2))
    # Replay the same identity as WS reconnects; neither the ledger nor the saved
    # remaining position may apply this already committed partial fill twice.
    trade = next(row for row in harness.ledger()['fills'] if row['clOrdId'] == first['clOrdId'])
    harness.control(replay_trade_id=trade['tradeId'])
    if crash:
        docker('kill', '--signal', 'KILL', bot)
        assert json.loads(docker('inspect', bot).stdout)[0]['State']['ExitCode'] == 137
        # The original live remainder must be cancelled and read back before a
        # replacement closes exactly .6 contracts, never the original full size.
        assert len(close_sends(harness)) == 1
        harness.control(cleanup_barrier='none')
        docker('start', bot)
        poll(lambda: http(port, '/startup')[0] == 200, timeout=200, description='partial-close crash recovery startup')
        poll(lambda: harness.state()['owner_generation'] > generation, description='partial-close replacement owner')
        checkpoint.update(hard_kill_exit_code=137, generation_before=generation,
                          generation_after=harness.state()['owner_generation'])
    if outcome == 'live' and not crash:
        harness.control(fill=dict(clOrdId=first['clOrdId'], amount='.6', fee='.000001'), duplicate=True)
        expected = [('long', 'sell', Decimal('1')), ('short', 'buy', Decimal('1'))]
    else:
        expected = [(first['posSide'], first['side'], Decimal('1')), (first['posSide'], first['side'], Decimal('.6')),
                    ('short', 'buy', Decimal('1')) if first['posSide'] == 'long' else ('long', 'sell', Decimal('1'))]
    harness.control(cleanup_barrier='none')
    ledger = verify_cleanup(harness, port, root, spot, expected_sends=expected, fill_count=6)
    evidence = audit(harness, port, root['id'])
    history = financial_digest(harness)
    harness.terminate(bot)
    resumed, port = harness.start('partial-cleanup-restart', auto_migrate=False)
    assert financial_digest(harness) == history and slot(harness, port, 'okx_perpetual')['status'] == 'disconnected'
    assert harness.state()['desired_state'] == 'stopped'
    harness.terminate(resumed)
    harness.metrics['managed_partial_cleanup'] = dict(**checkpoint, ledger=ledger, audit=evidence,
                                                      no_duplicate_fill=True, residual_only=True, restart_removed=True,
                                                      local_kms=True, actual_okx=False)


def database_scenario(harness, target):
    print('Offline E2E: real PostgreSQL cleanup transaction failure, target=' + target, flush=True)
    bot, port = initialize(harness)
    seed_exposure(harness, port)
    spot = spot_digest(harness)
    harness.control(cleanup_market_closes=True)
    generation = harness.state()['owner_generation']
    install_database_fault(harness, target)
    try:
        root = disconnect(harness, port)
        poll(lambda: database_fault_attempts(harness) > 0, timeout=60, description='native write reaches guarded PG transaction fault')
        attempts = database_fault_attempts(harness)
        assert harness.sql("SELECT count(*) FROM exchange_credentials WHERE connector='okx_perpetual' AND envelope IS NOT NULL AND state='revoking'") == '1'
        assert harness.sql("SELECT count(*) FROM admin_audit_events WHERE action='credentials_disconnect' AND outcome='succeeded'") == '0'
        observed = operation(harness, port, root['id'])
        assert observed['status'] != 'succeeded'
        assert harness.sql('SELECT count(*) FROM trade_fills') == ('3' if target == 'fill' else '5')
        if target == 'fill':
            assert Decimal(harness.sql("SELECT sum(amount) FROM positions WHERE connector_name='okx_perpetual'")) == Decimal('.02')
        else:
            assert harness.sql("SELECT count(*) FROM positions WHERE connector_name='okx_perpetual' AND amount <> 0") == '0'
        checkpoint = dict(target=target, operation_id=root['id'], fault_attempts=attempts,
                          generation_before=generation, ciphertext_retained=True, false_success=False,
                          stage=observed['result']['stage'], reason_code=observed['result']['reason_code'],
                          unique_committed_fills=int(harness.sql('SELECT count(*) FROM trade_fills')))
        (harness.report / 'database-fault-checkpoint.json').write_text(json.dumps(checkpoint, indent=2))
    finally:
        disarm_database_fault(harness, target)
    if target == 'fill':
        # Recorder rollback permanently fences that process. Use an ordinary
        # process replacement, never flip persistence_healthy or edit a ledger.
        docker('stop', '--time', '10', bot)
        docker('start', bot)
        poll(lambda: http(port, '/startup')[0] == 200, timeout=200, description='fresh process after recorder DB failure')
        poll(lambda: harness.state()['owner_generation'] > generation, description='DB-failure recovery obtains a new owner')
    retries = []

    def recovered():
        row = operation(harness, port, root['id'])
        assert row['status'] != 'failed', row['error']
        if row['result']['operator_action_required']:
            assert not retries, ('Cleanup remained blocked after one normal recovery Retry', row['result']['reason_code'])
            request = dict(expected_version=slot(harness, port, 'okx_perpetual')['version'],
                           expected_control_version=harness.state()['control_version'],
                           expected_operation_version=row['result']['operation_version'])
            code, retry = harness.admin(port, '/credentials/operations/' + root['id'] + '/retry', request,
                                        key='retry-after-database-repair')
            assert code == 202, (code, retry.get('code'))
            retries.append((retry['id'], 'credentials_cleanup_retry'))
        return row if row['status'] == 'succeeded' else None
    poll(recovered, timeout=180, description='normal owner/readback/Retry completes the original DB-failed cleanup')
    ledger = verify_cleanup(harness, port, root, spot)
    evidence = audit(harness, port, root['id'], children=retries)
    history = financial_digest(harness)
    harness.terminate(bot)
    resumed, port = harness.start('database-cleanup-restart', auto_migrate=False)
    assert financial_digest(harness) == history and slot(harness, port, 'okx_perpetual')['status'] == 'disconnected'
    assert harness.state()['desired_state'] == 'stopped'
    harness.terminate(resumed)
    harness.metrics['managed_cleanup_database_fault'] = dict(**checkpoint, ledger=ledger, audit=evidence,
                                                             recovery_process_replacement=target == 'fill',
                                                             restart_removed=True, local_kms=True, actual_okx=False)
