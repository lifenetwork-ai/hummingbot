import json
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import flow_trace
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.flow_diagnostic import diagnostic_flow
from types import SimpleNamespace

import pytest


def test_disabled_process_diagnostic_installs_no_hooks(monkeypatch, capsys):
    monkeypatch.delenv('LIFE_PG_PROCESS_FLOW_TRACE', raising=False)
    monkeypatch.setattr(flow_trace, 'QuoteFlowTrace', lambda _: pytest.fail('disabled hook installed'))
    with diagnostic_flow() as trace:
        assert trace is None
    assert capsys.readouterr().err == ''


def test_broken_diagnostic_cannot_replace_original_financial_failure(monkeypatch, capsys):
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import flow_diagnostic
    monkeypatch.setenv('LIFE_PG_PROCESS_FLOW_TRACE', '1')
    monkeypatch.setattr(flow_trace, 'QuoteFlowTrace', lambda *a, **k: SimpleNamespace(install=lambda: object()))

    def broken(*args):
        raise ValueError('synthetic-private-report-payload')
    monkeypatch.setattr(flow_diagnostic, 'report', broken)
    original = RuntimeError('original refusal')
    with pytest.raises(RuntimeError) as caught:
        with diagnostic_flow():
            raise original
    assert caught.value is original and original.__notes__ == ['PROCESS_FLOW_REPORT_FAILED']
    assert capsys.readouterr().err == ''


@pytest.mark.parametrize('failed', [False, True])
def test_process_diagnostic_preserves_failure_redacts_payload_and_removes_hooks(monkeypatch, capsys, failed):
    monkeypatch.setenv('LIFE_PG_PROCESS_FLOW_TRACE', '1')
    hooks = {}
    trace = SimpleNamespace(rows=lambda: [(1, 'shared_claim_release', 1, 2, 'error')], active_rows=lambda: [],
                            summaries=lambda: [], revocations=[], dropped=0, details_dropped=0, database_dropped=0, inflight_dropped=0)

    class Trace:
        def __init__(self, patch, *, capacity):
            assert capacity == 4096
            self.patch = patch

        def install(self):
            self.patch.setitem(hooks, 'active', True)
            return trace
    monkeypatch.setattr(flow_trace, 'QuoteFlowTrace', Trace)
    error = ValueError('synthetic-private-payload')
    try:
        with diagnostic_flow() as selected:
            assert selected is trace and hooks == {'active': True}
            if failed:
                raise error
    except ValueError as caught:
        assert failed and caught is error
    assert hooks == {}
    output = capsys.readouterr().err
    assert 'synthetic-private-payload' not in output
    assert json.loads(output.removeprefix('bounded_process_flow='))['rows'] == [[1, 'shared_claim_release', 1, 2, 'error']]
