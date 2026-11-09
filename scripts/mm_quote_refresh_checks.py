"""Packaged native rolling refresh, observed through an independent venue ledger."""
import json
import time

import yaml


def scenario(harness):
    from scripts.test_e2e_offline import poll

    print('Offline E2E: bounded rolling refresh retains working peers and withdraws stale quotes', flush=True)
    path = harness.assets / 'life_mm_profiles.yaml'
    profiles = yaml.safe_load(path.read_text())
    for market in profiles['markets'].values():
        market.update(executor_refresh_time=15, quote_refresh_max_delay_seconds=5)
    path.write_text(yaml.safe_dump(profiles))
    harness.env.update(ADMIN_API_ENABLED='true', ADMIN_BASIC_AUTH_ENABLED='true',
                       ADMIN_API_USERNAME='operator', ADMIN_API_PASSWORD='offline-admin')
    _, port = harness.start('rolling-refresh')
    harness.mm_command(port, 'start', market_mode='both', selection='fixed', profile_id='sideways', duration_seconds=600)

    def counts():
        orders = harness.ledger()['orders']
        return {instrument + ':' + side: sum(order['instId'] == instrument and order['side'] == side
                                             and order['state'] in ('live', 'partially_filled') for order in orders)
                for instrument in ('LIFE-USDT', 'LIFE-USDT-SWAP') for side in ('buy', 'sell')}

    poll(lambda: all(counts().values()), description='both-sided acknowledged venue quotes')
    samples, absent = [], []
    started = time.monotonic()
    while time.monotonic() - started < 65:
        observed = counts()
        row = dict(elapsed_seconds=time.monotonic() - started, orders=observed)
        samples.append(row)
        if not all(observed.values()):
            absent.append(row)
        time.sleep(.1)
    (harness.report / 'rolling-venue-observations.json').write_text(json.dumps(samples, indent=2))
    ledger = harness.ledger()
    assert len(ledger['accepted']) >= 24, 'Ordinary refresh did not make progress'
    assert all(row['ordType'] == 'post_only' for row in ledger['accepted'])
    assert not ledger['fills'], 'This continuity fixture intentionally injects no fills'
    assert not absent, 'Rolling refresh lost an acknowledged side: ' + json.dumps(absent[:3])
    harness.control(stale_pairs=['LIFE-USDT'])
    poll(lambda: not any(counts().values()), timeout=25, description='stale input withdraws all sides despite rolling refresh')
    harness.control(stale_pairs=[])
    poll(lambda: all(counts().values()), timeout=60, description='fresh market data restores admitted quotes')
    harness.mm_command(port, 'stop')
    assert not any(counts().values())
    harness.metrics['rolling_refresh'] = dict(
        interval_seconds=15, maximum_delay_seconds=5, observed_seconds=time.monotonic() - started,
        samples=len(samples), missing_sides=len(absent), accepted_post_only_orders=len(ledger['accepted']),
        stale_withdrawal_verified=True, separate_stop_verified=True)
