from copy import deepcopy

import pytest

from hummingbot.core.recovery import RecoveryBlocked, fingerprint, recovery_blockers, replay_positions


def snapshot():
    return dict(state=dict(desired_state='stopped', control_version=3, acknowledged_version=3,
                           recovery_state={'halt': True, 'reason': 'Strategy initialization/reconciliation failed; inspect sanitized logs.'}),
                orders=[], bindings=[], fills=[dict(market='okx', symbol='BTC-USDT', order_id='a', exchange_trade_id='1',
                                                    trade_type='BUY', position='NIL', price='100', amount='1', trade_fee_in_quote='.1',
                                                    trade_fee={'percent': '0', 'flat_fees': [{'token': 'USDT', 'amount': '.1'}]}, timestamp=1)],
                positions=[dict(id='p', controller_id='spot', connector_name='okx', trading_pair='BTC-USDT', side='BUY',
                                amount='0', volume_traded_quote='0', breakeven_price='0', realized_pnl_quote='0', cum_fees_quote='0', unrealized_pnl_quote='0')])


def test_replay_extends_known_prefix_and_preserves_original_snapshot():
    data = snapshot(); before = deepcopy(data)
    plan = replay_positions(data)
    assert plan[0]['after']['amount'] == '1'
    assert plan[0]['after']['cum_fees_quote'] == '0.1'
    assert plan[0]['before']['amount'] == '0'
    assert data == before


def test_unknown_opening_baseline_is_not_silently_overwritten():
    data = snapshot(); data['positions'][0]['amount'] = '9'
    with pytest.raises(RecoveryBlocked, match='position_baseline_unproven'):
        replay_positions(data)


def test_already_complete_aggregate_does_not_get_repaired_again():
    data = snapshot(); data['positions'][0].update(replay_positions(data)[0]['after'])
    assert replay_positions(data) == []


@pytest.mark.parametrize('field,value,code', [
    ('desired_state', 'running', 'recovery_stop_required'),
    ('acknowledged_version', 2, 'recovery_stop_incomplete'),
])
def test_runtime_intent_must_be_stopped_and_acknowledged(field, value, code):
    data = snapshot(); data['state'][field] = value
    assert code in recovery_blockers(data['state'])


@pytest.mark.parametrize('key,value,code', [
    ('pending_mutations', [{'token': 'x'}], 'recovery_pending_mutations'),
    ('credential_operation', {'id': 'x'}, 'credential_operation_in_progress'),
    ('reason', 'drawdown_controller_limit', 'recovery_risk_stop'),
    ('strategy_checkpoints', {'life_mm_profiles.yaml': {'_is_stop_triggered': True}}, 'recovery_risk_stop'),
    ('strategy_checkpoints', {'life_mm_profiles.yaml': {'_is_stop_triggered': 'false'}}, 'recovery_checkpoint_invalid'),
])
def test_protected_recovery_state_blocks_reset(key, value, code):
    data = snapshot(); data['state']['recovery_state'][key] = value
    assert code in recovery_blockers(data['state'])


def test_fingerprint_ignores_observational_publication_but_not_financial_changes():
    data = snapshot(); before = fingerprint(data)
    data['state']['heartbeat'] = 'later'; data['state']['status'] = {'reporting': 1}
    data['state']['recovery_state']['funding_sync'] = {'last_success_ms': 99}
    assert fingerprint(data) == before
    data['fills'][0]['amount'] = '2'
    assert fingerprint(data) != before


def test_original_fee_currency_must_be_provable_without_new_conversion():
    data = snapshot(); data['fills'][0]['trade_fee']['flat_fees'][0]['token'] = 'BTC'
    with pytest.raises(RecoveryBlocked, match='recovery_fee_basis_unsupported'):
        replay_positions(data)


def test_original_spot_base_fee_uses_verified_fill_price_and_preserves_gross_quantity():
    data = snapshot()
    data['fills'][0]['trade_fee']['flat_fees'] = [{'token': 'BTC', 'amount': '.001'}]
    plan = replay_positions(data)
    assert plan[0]['after']['amount'] == '1'
    assert plan[0]['after']['cum_fees_quote'] == '0.100'
    assert plan[0]['before']['amount'] == '0'


def test_mixed_original_base_and_quote_fees_require_exact_recorded_conversion():
    data = snapshot()
    data['fills'][0]['trade_fee']['flat_fees'] = [{'token': 'BTC', 'amount': '.001'},
                                                  {'token': 'USDT', 'amount': '.02'}]
    data['fills'][0]['trade_fee_in_quote'] = '.12'
    assert replay_positions(data)[0]['after']['cum_fees_quote'] == '0.120'
    data['fills'][0]['trade_fee_in_quote'] = '.12000001'
    with pytest.raises(RecoveryBlocked, match='recovery_fee_basis_unsupported'):
        replay_positions(data)


@pytest.mark.parametrize('connector,symbol,token', [('okx', 'ETH-USDT', 'BTC'),
                                                    ('okx_perpetual', 'BTC-USDT', 'BTC')])
def test_third_asset_and_perpetual_base_fees_remain_unsupported(connector, symbol, token):
    data = snapshot()
    data['positions'][0].update(connector_name=connector, trading_pair=symbol)
    data['fills'][0].update(market=connector, symbol=symbol, position='OPEN' if connector == 'okx_perpetual' else 'NIL')
    data['fills'][0]['trade_fee']['flat_fees'] = [{'token': token, 'amount': '.001'}]
    with pytest.raises(RecoveryBlocked, match='recovery_fee_basis_unsupported'):
        replay_positions(data)


def test_multiple_spot_rows_do_not_guess_controller_attribution():
    data = snapshot(); other = deepcopy(data['positions'][0]); other['id'] = 'q'; other['controller_id'] = 'other'
    data['positions'].append(other)
    with pytest.raises(RecoveryBlocked, match='recovery_position_scope_ambiguous'):
        replay_positions(data)


@pytest.mark.parametrize('checkpoint', [{'max_global_pnl': '-1'}, {'mm_drawdown_policy': {}},
                                        {'max_pnl_by_controller': {'spot': 'NaN'}}])
def test_invalid_or_negative_risk_baseline_cannot_be_recovered(checkpoint):
    data = snapshot()
    data['state']['recovery_state']['strategy_checkpoints'] = {'life_mm_profiles.yaml': checkpoint}
    assert 'recovery_checkpoint_invalid' in recovery_blockers(data['state'])


def test_drawdown_scope_prevents_reassigning_another_controllers_fills():
    data = snapshot()
    data['state']['recovery_state']['strategy_checkpoints'] = {'x': {'mm_drawdown_policy': {
        'scope': {'other': dict(connector_name='okx', trading_pair='BTC-USDT')}}}}
    with pytest.raises(RecoveryBlocked, match='recovery_position_scope_ambiguous'):
        replay_positions(data)


def test_hedge_close_cannot_turn_a_long_aggregate_into_the_short_leg():
    data = snapshot()
    data['positions'][0]['connector_name'] = 'okx_perpetual'
    data['fills'][0].update(market='okx_perpetual', position='OPEN')
    closing = deepcopy(data['fills'][0])
    closing.update(order_id='b', exchange_trade_id='2', trade_type='SELL', position='CLOSE', amount='2', timestamp=2)
    data['fills'].append(closing)
    with pytest.raises(RecoveryBlocked, match='recovery_position_scope_ambiguous'):
        replay_positions(data)
