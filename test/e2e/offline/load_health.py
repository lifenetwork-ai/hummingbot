"""Semantic failures count even when an endpoint returns HTTP 200."""


def response_problem(path, status, body):
    if status != 200:
        return 'unexpected_http_status'
    if path.split('?', 1)[0] == '/statistics/daily':
        # Missing metadata preserves historical-image diagnostics; explicit
        # degradation in the new contract must never pass the healthy gate.
        if not isinstance(body, dict) or body.get('freshness', 'fresh') != 'fresh':
            return 'daily_statistics_degraded'
    return None
