"""Reporting updates copy mutable paths once and retain their JSON boundary."""
import copy
from decimal import Decimal
from types import SimpleNamespace

import pytest

from hummingbot.cli.admin_observations import capture_reporting


def core(connectors=None):
    return SimpleNamespace(strategy=None, connector_manager=SimpleNamespace(connectors=connectors or {}))


def balance(value='10', observed_at=1001):
    return dict(cash_balance=Decimal(value), available_cash=Decimal(value), equity=Decimal(value),
                available_equity=Decimal(value), observed_at=observed_at, source='okx_account_balance')


def prior():
    return {
        'accounts': [dict(account_id='account', environment='demo', identity_verified=True, balances=[
            dict(currency='LIFE', connector='okx', present_in_latest_snapshot=True, **balance('20', 1000))])],
        'positions': {'okx_perpetual': dict(items=[dict(pair='LIFE-USDT', amount='1')],
                                            pair_observations={'LIFE-USDT': 1000}, observed_at=1000)},
        'performance': {'old-controller': {'report': {'close_type_counts': {'6': 2}}}},
    }


def test_retained_position_history_is_traversed_once_for_json_detachment():
    class CountingHistory(list):
        traversals = 0

        def __iter__(self):
            type(self).traversals += 1
            return super().__iter__()

    previous = prior()
    history = CountingHistory(previous['positions']['okx_perpetual']['items'])
    previous['positions']['okx_perpetual']['items'] = history
    snapshot = capture_reporting(core(), previous)
    assert CountingHistory.traversals == 1
    assert snapshot['positions']['okx_perpetual']['items'] == [{'pair': 'LIFE-USDT', 'amount': '1'}]
    history[0]['amount'] = '99'
    assert snapshot['positions']['okx_perpetual']['items'][0]['amount'] == '1'


def test_balance_replacement_and_missing_assets_leave_previous_and_live_data_untouched():
    previous = prior()
    before = copy.deepcopy(previous)
    observations = {'USDT': balance('30')}
    connector = SimpleNamespace(account_uid='account', trading_environment='demo', account_balance_observations=observations)
    snapshot = capture_reporting(core({'okx': connector}), previous)
    assert previous == before
    values = {row['currency']: row for row in snapshot['accounts'][0]['balances']}
    assert values['LIFE']['present_in_latest_snapshot'] is False
    assert values['USDT']['cash_balance'] == '30'
    assert 'currency' not in observations['USDT']
    observations['USDT']['cash_balance'] = Decimal('999')
    assert values['USDT']['cash_balance'] == '30'
    values['LIFE']['cash_balance'] = 'mutated'
    assert previous == before


def test_current_positions_and_retained_performance_are_both_detached():
    previous = prior()
    before = copy.deepcopy(previous)
    times = {'LIFE-USDT': 1001}
    raw = {'position': dict(observed_at=1001, source='okx_account_positions', metadata={'ids': ['one']})}
    position = SimpleNamespace(trading_pair='LIFE-USDT', position_side=SimpleNamespace(name='LONG'),
                               amount=Decimal('2'), entry_price=Decimal('.1'), unrealized_pnl=Decimal('.01'), leverage=2)
    connector = SimpleNamespace(account_uid='account', trading_environment='demo', account_balance_observations={},
                                position_snapshot_times=times, position_observations=raw, account_positions={'position': position})
    snapshot = capture_reporting(core({'okx_perpetual': connector}), previous)
    assert previous == before
    assert snapshot['positions']['okx_perpetual']['items'][0]['amount'] == '2'
    times['LIFE-USDT'] = 9999
    raw['position']['metadata']['ids'].append('two')
    assert snapshot['positions']['okx_perpetual']['pair_observations']['LIFE-USDT'] == 1001
    assert snapshot['positions']['okx_perpetual']['items'][0]['metadata']['ids'] == ['one']
    snapshot['performance']['old-controller']['report']['close_type_counts']['6'] = 999
    assert previous == before


def test_reporting_failure_after_marking_missing_assets_does_not_mutate_previous():
    previous = prior()
    previous_usdt = dict(currency='USDT', connector='okx', present_in_latest_snapshot=True, **balance('10', 1000))
    previous['accounts'][0]['balances'].append(previous_usdt)
    before = copy.deepcopy(previous)
    connector = SimpleNamespace(account_uid='account', trading_environment='demo',
                                account_balance_observations={'USDT': {'missing_timestamp': True}})
    with pytest.raises(KeyError):
        capture_reporting(core({'okx': connector}), previous)
    assert previous == before


@pytest.mark.parametrize('invalid', [float('nan'), float('inf'), -float('inf')])
def test_strict_json_boundary_still_rejects_nonfinite_connector_samples(invalid):
    previous = prior()
    before = copy.deepcopy(previous)
    observation = balance()
    observation['cash_balance'] = invalid
    connector = SimpleNamespace(account_uid='account', trading_environment='demo', account_balance_observations={'USDT': observation})
    with pytest.raises(ValueError):
        capture_reporting(core({'okx': connector}), previous)
    assert previous == before


def test_verified_identity_replacement_does_not_edit_retained_unverified_balances():
    previous = prior()
    previous['accounts'][0].update(account_id=None, identity_verified=False)
    previous['accounts'][0]['balances'].append(dict(currency='USDT', connector='okx_perpetual',
                                                    present_in_latest_snapshot=True, **balance('15', 1000)))
    before = copy.deepcopy(previous)
    connector = SimpleNamespace(account_uid='verified', trading_environment='demo',
                                account_balance_observations={'USDT': balance('30')})
    snapshot = capture_reporting(core({'okx': connector}), previous)
    accounts = {row['account_id']: row for row in snapshot['accounts']}
    assert previous == before
    assert accounts['verified']['identity_verified'] is True
    assert accounts['verified']['balances'][0]['cash_balance'] == '30'
    assert [row['connector'] for row in accounts[None]['balances']] == ['okx_perpetual']
    assert accounts[None]['identity_verified'] is False
