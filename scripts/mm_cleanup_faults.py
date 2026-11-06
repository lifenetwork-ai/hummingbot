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
from scripts.test_e2e_offline import poll

SCENARIOS = ('managed-close-partial-ws', 'managed-close-partial-cancel')


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


def partial_scenario(harness, outcome):
    print('Offline E2E: native partial close with WS loss, outcome=' + outcome, flush=True)
    bot, port = initialize(harness)
    seed_exposure(harness, port)
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
    if outcome == 'live':
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
