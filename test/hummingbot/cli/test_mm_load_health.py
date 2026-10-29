from test.e2e.offline.load_health import response_problem

import pytest


@pytest.mark.parametrize('path,status,body,problem', [
    ('/statistics/daily', 200, {'freshness': 'stale'}, 'daily_statistics_degraded'),
    ('/statistics/daily?market=spot', 200, {'freshness': None}, 'daily_statistics_degraded'),
    ('/statistics/daily', 200, None, 'daily_statistics_degraded'),
    ('/statistics/daily', 200, {'freshness': 'fresh'}, None),
    ('/statistics/daily', 200, {'markets': []}, None),
    ('/status', 200, {'observation': {'stale': True}}, None),
    ('/statistics/daily', 503, {}, 'unexpected_http_status'),
])
def test_healthy_load_cannot_pass_an_explicitly_degraded_daily_snapshot(path, status, body, problem):
    assert response_problem(path, status, body) == problem
