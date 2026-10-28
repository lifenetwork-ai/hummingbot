import json
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import mm_load_faults, test_e2e_offline


def command_harness(status):
    receipt = dict(id='command-1', status=status, error='StrategyStartFailed', effective_version=1,
                   payload={'password': 'private-input'})

    def admin(port, path, data=None, key=None):
        if data is not None:
            return 202, {'id': receipt['id']}
        if path.startswith('/commands/'):
            return 200, receipt
        return 200, dict(phase='waiting_for_mm_readiness', error=None,
                         markets=[dict(market='spot', reason='stale_reference', private='private-input')])
    return SimpleNamespace(state=lambda: {'control_version': 0}, admin=admin)


def poll_once(check, **kwargs):
    if not check():
        raise AssertionError('Timed out: MM start actual command completion')


def test_position_preservation_compares_exposure_instead_of_market_valuation():
    original = dict(instId='LIFE-USDT-SWAP', posSide='long', pos='2', avgPx='.1',
                    lever='2', notionalUsd='.2', upl='0')
    marked = dict(original, notionalUsd='.16', upl='-.04')
    snapshot = test_e2e_offline.position_exposure([original])
    assert test_e2e_offline.position_exposure([marked]) == snapshot
    for field, value in [('pos', '1'), ('posSide', 'short'), ('avgPx', '.09'), ('lever', '3')]:
        assert test_e2e_offline.position_exposure([dict(marked, **{field: value})]) != snapshot
    assert test_e2e_offline.position_exposure([]) != snapshot


def test_cancel_upper_bound_uses_first_terminal_proof_not_later_duplicate_time():
    baseline = [dict(clOrdId='one', instId='LIFE-USDT'), dict(clOrdId='two', instId='LIFE-USDT-SWAP')]
    observed = [dict(row, state='canceled', cancel_terminal_ms=stamp, uTime='99000')
                for row, stamp in zip(baseline, [1100, 1200])]
    assert mm_load_faults.cancel_upper_bound(baseline, observed, 1000) == {'LIFE-USDT': 100, 'LIFE-USDT-SWAP': 200}
    assert mm_load_faults.cancel_upper_bound(baseline, observed, 1300) == {'LIFE-USDT': 0, 'LIFE-USDT-SWAP': 0}
    observed[0].pop('cancel_terminal_ms')
    with pytest.raises(AssertionError, match='terminal proof'):
        mm_load_faults.cancel_upper_bound(baseline, observed, 1000)


def test_docker_startup_probe_timeout_remains_unavailable_without_retrying_mutations(monkeypatch):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('docker exec', 10)
    monkeypatch.setattr(test_e2e_offline, 'docker', timeout)
    assert test_e2e_offline.http('bot', '/startup') == (None, None)
    for path, payload in [('/control', {'fill': 'one'}), ('/startup', {'mutation': True}), ('/ledger', None)]:
        with pytest.raises(subprocess.TimeoutExpired):
            test_e2e_offline.http('bot', path, payload)


@pytest.mark.parametrize('status', ['failed', 'superseded'])
def test_command_terminal_failure_is_immediate_and_includes_receipt(monkeypatch, status):
    monkeypatch.setattr(test_e2e_offline, 'poll', poll_once)
    with pytest.raises(AssertionError, match=f'MM start {status}') as caught:
        test_e2e_offline.Harness.mm_command(command_harness(status), 8080, 'start')
    assert 'StrategyStartFailed' in str(caught.value)
    assert 'command-1' in str(caught.value)
    assert 'private-input' not in str(caught.value)


def test_command_timeout_retains_readiness_reason_without_dumping_payload(monkeypatch):
    monkeypatch.setattr(test_e2e_offline, 'poll', poll_once)
    with pytest.raises(AssertionError) as caught:
        test_e2e_offline.Harness.mm_command(command_harness('applying'), 8080, 'start')
    message = str(caught.value)
    assert 'command-1' in message and 'applying' in message
    assert 'waiting_for_mm_readiness' in message and 'stale_reference' in message
    assert 'private-input' not in message


def test_completed_fault_is_saved_when_a_later_window_fails(monkeypatch, tmp_path):
    from scripts.test_mm_load import save_fault_progress

    timer = SimpleNamespace(start=lambda: None, join=lambda **kwargs: None, is_alive=lambda: False)
    monkeypatch.setattr(mm_load_faults.threading, 'Timer', lambda *args: timer)
    harness = SimpleNamespace(ledger=lambda: {'fills': [{}]}, mm_command=lambda *args, **kwargs: None)
    run = MagicMock(side_effect=[dict(statuses={'200': 900}, errors=[]), RuntimeError('later fault failed')])
    evidence = {'windows': {'soak': {'violations': []}}}
    with pytest.raises(RuntimeError, match='later fault failed'):
        mm_load_faults.fault_windows(harness, 'bot', 8080, 'client', tmp_path, run, lambda *args: None,
                                     on_result=lambda name, data: save_fault_progress(tmp_path, evidence, name, data))
    persisted = json.loads((tmp_path / 'result.json').read_text())
    assert set(persisted['faults']) == {'feed'}
    assert persisted.get('passed') is not True
    assert persisted['windows']['soak']['violations'] == []
    case = json.loads((tmp_path / 'fault-feed.json').read_text())
    assert case['classification'] == 'injected_fault_excluded_from_healthy_latency'
