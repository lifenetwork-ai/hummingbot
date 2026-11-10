"""Native post-only cancellation before ACK must not pin a managed MM level."""
import time
from decimal import Decimal

import yaml


def scenario(harness):
    path = harness.assets / 'life_mm_profiles.yaml'
    original = path.read_bytes()
    try:
        return _run(harness)
    finally:
        path.write_bytes(original)


def _run(harness):
    from scripts.test_e2e_offline import poll
    print('Offline E2E: market moves after preparation; native post-only cancel and fresh entry', flush=True)
    path = harness.assets / 'life_mm_profiles.yaml'
    profiles = yaml.safe_load(path.read_text())
    for market in profiles['markets'].values():
        market['spread_policy'] = dict(fee_floor=True)
    path.write_text(yaml.safe_dump(profiles))
    (harness.report / 'effective-maker-profiles.yaml').write_bytes(path.read_bytes())
    instruments = ('LIFE-USDT', 'LIFE-USDT-SWAP')
    before = {key: dict(bids=[['.103', '100000']], asks=[['.104', '100000']]) for key in instruments}
    after = {key: dict(bids=[['.105', '100000']], asks=[['.106', '100000']]) for key in instruments}
    harness.control(enforce_post_only=True, book_overrides=before, post_only_move_on_submit=after)
    harness.env.update(ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin')
    _, port = harness.start('maker-entry')
    harness.mm_command(port, 'start', market_mode='both', selection='fixed', profile_id='follow_market', duration_seconds=600)
    session = harness.state()['mm_session']

    def ready():
        orders = harness.ledger()['orders']
        return orders if all(sum(row['instId'] == instrument and row['side'] == side and row['state'] == 'live'
                                 for row in orders) >= 2 for instrument in instruments for side in ('buy', 'sell')) else None

    poll(lambda: all(any(row['instId'] == instrument and row.get('cancelSource') == '31'
                         for row in harness.ledger()['orders']) for instrument in instruments),
         description='both markets really exercise venue cancel before REST ACK')
    started = time.monotonic()
    orders = poll(ready, timeout=20, description='both markets restore passive quotes after confirmed zero cancel')
    for row in [row for row in orders if row['state'] == 'live']:
        assert row['ordType'] == 'post_only'
        assert Decimal(row['px']) < Decimal('.106') if row['side'] == 'buy' else Decimal(row['px']) > Decimal('.105')
    assert all(any(row['state'] == 'live' and row['side'] == 'sell' and row['instId'] == instrument
                   and Decimal(row['px']) >= Decimal('.106') for row in orders) for instrument in instruments)
    assert not harness.ledger()['fills']
    assert not harness.state()['recovery_state'].get('halt')
    assert harness.state()['mm_session']['id'] == session['id']
    cancellations = [row for row in orders if row.get('cancelSource') == '31']
    assert len(cancellations) <= 8, 'Same-price cancellation loop continued'
    assert all(row['accFillSz'] == '0' for row in cancellations)
    assert len({row['clOrdId'] for row in orders}) == len(orders)
    harness.mm_command(port, 'stop')
    assert not ready() and not any(row['state'] in ('live', 'partially_filled') for row in harness.ledger()['orders'])
    harness.metrics['maker_entry'] = dict(native_zero_cancels=len(cancellations), passive_requote_seconds=time.monotonic() - started,
                                          both_sides_restored=True, no_same_identity_replay=True, fills=0,
                                          preserved_session=True, stopped=True, simulated=True)
