import json
from test.hummingbot.cli.test_managed_lifecycle_fixture import exchange, signed
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_busy_fault_is_http429_without_venue_acceptance_and_can_be_disabled():
    venue = exchange()
    assert (await venue.control({'submission_busy': 'true'})).status == 400
    assert not venue.submission_busy
    await venue.control({'submission_busy': True})
    terms = dict(instId='LIFE-USDT', clOrdId='busy', side='sell', tdMode='cash', ordType='post_only', sz='1', px='.101')
    response = await venue.handle(signed('/api/v5/trade/order', method='POST', data=terms))
    assert response.status == 429 and json.loads(response.text)['code'] == '50013'
    assert not venue.orders and not venue.accepted and not venue.fills
    request = signed('/api/v5/trade/order')
    request.query = {'instId': 'LIFE-USDT', 'clOrdId': 'busy'}
    absent = await venue.handle(request)
    assert json.loads(absent.text)['code'] == '51603'
    assert venue.requests[-2]['injected_submission_busy']
    await venue.control({'submission_busy': False})
    assert (await venue.handle(signed('/api/v5/trade/order', method='POST', data=terms))).status == 200
    assert len(venue.accepted) == 1


@pytest.mark.parametrize('get_arrives,owner_changes', [(True, False), (False, False), (True, True)])
def test_busy_scenario_waits_for_worker_get_after_reconciling_publication(monkeypatch, get_arrives, owner_changes):
    from scripts import mm_submission_uncertainty_checks as checks, test_e2e_offline as runner

    state = dict(owner_uuid='same-owner', owner_generation=1, desired_state='running',
                 recovery_state=dict(automatic_recovery=dict(phase='reconciling')))
    reads = []

    def current_state():
        if reads and owner_changes:
            state['owner_generation'] = 2
        return state

    def ledger():
        reads.append(None)
        requests = [dict(path='/api/v5/trade/order', method='POST', injected_submission_busy=True)]
        if get_arrives and len(reads) >= 2:
            requests.append(dict(path='/api/v5/trade/order', method='GET'))
        return dict(requests=requests, accepted=[], fills=[])

    def bounded_poll(check, *, timeout, description):
        assert timeout == 60  # Phase publication and GET share the original bound.
        for _ in range(3):
            if result := check():
                return result
        raise AssertionError('worker GET not observed')

    def command(port, action, **kwargs):
        if action == 'stop':
            state['desired_state'] = 'stopped'

    harness = SimpleNamespace(env={}, metrics={}, control=lambda **kwargs: None,
                              start=lambda name: ('bot', 1), state=current_state,
                              ledger=ledger, sql=lambda statement: '1', mm_command=command,
                              admin=lambda port, path: (200, dict(recovery=dict(halted=False),
                                                                  automatic_recovery=dict(phase='reconciling'))))
    monkeypatch.setattr(runner, 'poll', bounded_poll)
    monkeypatch.setattr(runner, 'http', lambda port, path: (503 if path == '/ready' else 200, {}))
    monkeypatch.setattr(checks.time, 'sleep', lambda seconds: None)
    if owner_changes:
        with pytest.raises(AssertionError, match='owner changed'):
            checks.scenario(harness)
        assert state['desired_state'] == 'running'
    elif get_arrives:
        checks.scenario(harness)
        assert state['desired_state'] == 'stopped'
        assert harness.metrics['submission_uncertainty']['no_automatic_resubmission']
    else:
        with pytest.raises(AssertionError, match='worker GET not observed'):
            checks.scenario(harness)
        assert state['desired_state'] == 'running'
