"""Packaged native CLOSE policy on an isolated simulator; never exchange credentials."""
import json
from collections import Counter
from decimal import ROUND_DOWN, Decimal
from fractions import Fraction

SCENARIOS = ('perpetual-reduction-long', 'perpetual-reduction-short')


def scenario(harness, leg):
    import yaml

    from scripts.test_e2e_offline import poll as wait_until

    def poll(check, **kwargs):
        try:
            return wait_until(check, **kwargs)
        except AssertionError:
            harness.report.joinpath('reduction-wait-status.json').write_text(json.dumps(harness.admin(port, '/status')[1], indent=2))
            harness.report.joinpath('reduction-wait-checkpoints.json').write_text(harness.sql(
                "SELECT COALESCE(json_agg(t),'[]') FROM (SELECT config_file_path,financial_hash,payload FROM runtime_checkpoints) t"))
            harness.report.joinpath('reduction-wait-executors.json').write_text(harness.sql(
                "SELECT COALESCE(json_agg(t),'[]') FROM (SELECT id,status,close_type,custom_info FROM executors ORDER BY timestamp DESC LIMIT 20) t"))
            raise
    print('Offline E2E: maker reduction, lost ACK, partial, fee outage and cold restart: ' + leg, flush=True)
    path = harness.assets / 'life_mm_profiles.yaml'
    profiles = yaml.safe_load(path.read_text())
    market = profiles['markets']['perpetual']
    market.update(buy_spreads=[.01], sell_spreads=[.01], leverage=1,
                  max_long_base='600', max_short_base='600', max_gross_base='1200',
                  quote_guards=dict(enabled=True, max_deviation_bps='200', resume_deviation_bps='100', recovery_delay_seconds=0),
                  spread_policy=dict(fee_floor=True), inventory_reduction=dict(mode='close_first', price_mode='breakeven'))
    path.write_text(yaml.safe_dump(profiles))
    harness.env.update(ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin')
    for field, value in (('API_KEY', 'offline-key'), ('SECRET_KEY', 'offline-secret'), ('PASSPHRASE', 'offline-pass')):
        harness.env['HBOT_OKX_PERPETUAL_' + field] = value
    # This is a lifecycle/fault gate with independently available outside
    # liquidity. Retired own orders conservatively mask their original price;
    # a one-level book cannot establish an outside price after repeated closes.
    # Thin-book availability and the unchanged 99% target are separate gates.
    harness.control(include_own_orders=True, book_overrides={'LIFE-USDT-SWAP': {
        'bids': [['.099', '100000'], ['.098', '100000']],
        'asks': [['.101', '100000'], ['.102', '100000']]}})
    first, port = harness.start('reduction-' + leg)
    harness.mm_command(port, 'start', market_mode='perpetual', selection='fixed', profile_id='sideways', duration_seconds=600)

    def live():
        return [row for row in harness.ledger()['orders'] if row['state'] in ('live', 'partially_filled')]

    def positions():
        return {row['posSide']: Decimal(row['pos']) for row in harness.ledger()['positions'] if Decimal(row['pos'])}

    def closing():
        return [row for row in live() if row.get('posSide') == leg and row['side'] == ('sell' if leg == 'long' else 'buy')]

    poll(lambda: len(live()) == 2, description='flat account has two opening maker quotes')
    opening = next(row for row in live() if row['posSide'] == leg)
    harness.control(accepted_response_lost=1)
    opening_base = Decimal(opening['sz']) * Decimal('.01')
    opening_fee = opening_base * Decimal(opening['px']) * Decimal('.0002')
    harness.control(fill=dict(clOrdId=opening['clOrdId'], amount=opening['sz'], fee=str(opening_fee)), duplicate=True)

    def recovered_close():
        status = harness.state()
        lost = [r for r in harness.ledger()['requests'] if r.get('injected_accepted_response_lost')]
        return closing() if (lost and not status['recovery_state'].get('halt')
                             and status['recovery_state'].get('automatic_recovery', {}).get('phase') == 'idle') else None

    orders = poll(recovered_close, timeout=60, description='GET proof recovers the new closing quote without a second POST')
    assert sum(Decimal(row['sz']) for row in orders) <= Decimal(opening['sz'])
    assert positions() == {leg: Decimal(opening['sz'])}
    close = orders[0]
    assert close['ordType'] == 'post_only' and close['tdMode'] == 'cross'
    quantity = (Decimal(close['sz']) / 2).quantize(Decimal('1'), rounding=ROUND_DOWN)
    assert quantity > 0
    base = quantity * Decimal('.01')
    fee = base * Decimal(close['px']) * Decimal('.0002')
    trading_pnl = (Decimal(close['px']) - Decimal(opening['px'])) * base * (1 if leg == 'long' else -1)
    assert trading_pnl >= opening_fee * base / opening_base + fee
    harness.control(fill=dict(clOrdId=close['clOrdId'], amount=str(quantity), fee=str(fee)), duplicate=True)
    harness.mm_command(port, 'stop')
    assert not live() and positions() == {leg: Decimal(opening['sz']) - quantity}
    expected = opening_base - base
    held_side = 'BUY' if leg == 'long' else 'SELL'

    def checkpoint_matches():
        amount = harness.sql("SELECT amount::text FROM positions WHERE connector_name='okx_perpetual' AND side='" + held_side + "'")
        return bool(amount) and Decimal(amount) == expected

    poll(checkpoint_matches, description='partial close is durably applied to the correct hedge bucket')
    assert harness.sql("SELECT count(*) FROM trade_fills") == '2'
    payload = json.loads(harness.sql("SELECT payload FROM runtime_checkpoints WHERE config_file_path='life_mm_profiles.yaml'"))
    basis = next(row['basis'] for row in payload['reduction_basis']['positions'] if row['side'] == held_side)
    assert Decimal(basis['amount']) == expected
    assert Fraction(Decimal(basis['opening_fees_quote'])) >= Fraction(opening_fee) * Fraction(expected) / Fraction(opening_base)
    assert not harness.state()['recovery_state'].get('halt')
    harness.terminate(first)
    _, port = harness.start('reduction-restart-' + leg, auto_migrate=False)
    harness.mm_command(port, 'start', market_mode='perpetual', selection='fixed', profile_id='sideways', duration_seconds=600)
    poll(closing, timeout=60, description='cold owner restores owned remaining cost and maker closing quotes')
    assert positions() == {leg: Decimal(opening['sz']) - quantity}
    harness.control(reduction_fee_error=True, cancel_delay_seconds=1)
    poll(lambda: not closing(), timeout=40, description='lost fee authority cancels resting closing quotes')
    assert not harness.state()['recovery_state'].get('halt')
    harness.control(reduction_fee_error=False, cancel_delay_seconds=0)
    restored = poll(closing, timeout=40, description='fresh authenticated fee observation restores the closing quote')
    final = restored[0]
    assert Decimal(final['sz']) * Decimal('.01') == expected
    final_fee = expected * Decimal(final['px']) * Decimal('.0002')
    final_pnl = (Decimal(final['px']) - Decimal(opening['px'])) * expected * (1 if leg == 'long' else -1)
    assert Fraction(final_pnl) >= Fraction(Decimal(basis['opening_fees_quote'])) + Fraction(final_fee)
    harness.control(fill=dict(clOrdId=final['clOrdId'], amount=final['sz'], fee=str(final_fee)), duplicate=True)

    def flat_open_quotes():
        orders = live()
        return (not positions() and len(orders) == 2
                and {(row['side'], row['posSide']) for row in orders} == {('buy', 'long'), ('sell', 'short')})

    poll(flat_open_quotes, timeout=40, description='fully closed inventory returns to two opening maker quotes')
    harness.mm_command(port, 'stop')
    assert not live() and not positions()
    assert Decimal(harness.sql("SELECT amount::text FROM positions WHERE connector_name='okx_perpetual' AND side='" + held_side + "'")) == 0
    payload = json.loads(harness.sql("SELECT payload FROM runtime_checkpoints WHERE config_file_path='life_mm_profiles.yaml'"))
    cleared = [row['basis'] for row in payload['reduction_basis']['positions'] if row['side'] == held_side]
    assert not cleared or all(Decimal(row[field]) == 0 for row in cleared for field in ('amount', 'principal_quote', 'opening_fees_quote'))
    ledger = harness.ledger()
    lost = [r for r in ledger['requests'] if r.get('injected_accepted_response_lost')]
    assert len(lost) == 1 and lost[0]['order']['posSide'] == leg
    assert lost[0]['order']['side'] == ('sell' if leg == 'long' else 'buy')
    assert len(ledger['fills']) == 3 and not ledger['unknown']
    submitted = [row['order']['clOrdId'] for row in ledger['requests']
                 if row.get('path') == '/api/v5/trade/order' and row.get('method') == 'POST']
    assert all(count == 1 for count in Counter(submitted).values())
    assert harness.sql('SELECT count(*) FROM runtime_journal WHERE kind=\'fill\' AND NOT applied') == '0'
    assert harness.sql('SELECT count(*) FROM trade_fills') == '3'
    assert Decimal(harness.sql('SELECT sum(trade_fee_in_quote)::text FROM trade_fills')) == opening_fee + fee + final_fee
    assert Decimal(harness.sql("SELECT realized_pnl_quote::text FROM positions WHERE connector_name='okx_perpetual' AND side='" + held_side + "'")) == trading_pnl + final_pnl
    harness.metrics['perpetual_reduction'] = dict(
        leg=leg, opening_base=str(opening_base), partial_closed_base=str(base), closed_base=str(opening_base), remaining_base='0',
        original_fees=str(opening_fee + fee + final_fee), realized_trading_pnl=str(trading_pnl + final_pnl),
        partial_applied_once=True, maker_only=True, lost_ack_without_duplicate_post=True,
        cold_restart=True, fee_outage_canceled_quotes=True, fee_recovery=True, full_close_returns_to_open=True,
        outside_book='two_levels_each_side_with_own_quotes', simulated=True)
