"""Flow aggregation keeps background overlap and intervals independent."""
from dataclasses import replace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.retained_flow_profile import RetainedFlowProfile

from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _Trace


def test_retained_flow_does_not_add_background_overlap_to_financial_time():
    profile = RetainedFlowProfile()
    sample = replace(_Trace('spot_settle_inventory', True).finish('success'),
                     elapsed_ms=100, phases_ms=(('execute', 60), ('commit', 40)))
    profile.record(sample)
    profile.record(replace(sample, elapsed_ms=80, phases_ms=(('execute', 50), ('commit', 30))))
    profile.record(replace(sample, operation='process_lease_heartbeat', elapsed_ms=90))
    row = profile.checkpoint(32)
    groups = {(g['lane'], g['operation']): g for g in row['groups']}
    foreground = groups['foreground', 'spot_settle_inventory']
    assert foreground['count'] == 2 and foreground['elapsed_ms'] == 180 and foreground['max_ms'] == 100
    assert foreground['phases_ms'] == {'execute': 110, 'commit': 70}
    assert foreground['phase_max_ms'] == {'execute': 60, 'commit': 40}
    assert groups['background', 'process_lease_heartbeat']['elapsed_ms'] == 90
    profile.reset()
    profile.record(replace(sample, outcome='error'))
    row = profile.checkpoint(33)
    assert (row['from_completed'], row['to_completed']) == (32, 33)
    assert len(row['groups']) == 1 and row['groups'][0]['errors'] == 1
    assert row['groups'][0]['count'] == 1


def test_retained_flow_reports_truncated_operation_coverage():
    profile = RetainedFlowProfile()
    for n in range(129):
        profile.record(_Trace('operation_' + str(n), True).finish('success'))
    row = profile.checkpoint(0)
    assert len(row['groups']) == 128 and row['dropped'] == 1
