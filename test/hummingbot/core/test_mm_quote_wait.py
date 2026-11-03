"""Read-only stage evidence, bounded sampled age and operator guidance."""
import asyncio
import json
from test.hummingbot.core.test_mm_liquidity import connector
from test.hummingbot.core.test_mm_quote_activity import observation, sample
from test.hummingbot.strategy_v2.controllers.test_quote_guard import order

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.mm_liquidity import LiquidityObserver, liquidity_view, own_quotes


def pending(*, create=0, cancel=0):
    value = observation(empty=True)
    value['pending_orders'] = {'BUY': dict(submission_pending=create, cancel_confirmation=cancel)}
    return value


def test_pending_tracker_states_are_filtered_by_side_and_pair_in_one_scan():
    market = connector()
    market.in_flight_orders = {
        'create': order(state=OrderState.PENDING_CREATE, exchange_id=None),
        'cancel': order(identity='cancel', side=TradeType.SELL, state=OrderState.PENDING_CANCEL),
        'other': order(identity='other', state=OrderState.PENDING_CREATE, exchange_id=None),
    }
    market.in_flight_orders['other'].trading_pair = 'OTHER-USDT'
    original_values = market.in_flight_orders.values

    class SingleScan(dict):
        def values(self):
            assert not getattr(self, 'read', False)
            self.read = True
            return original_values()

    market.in_flight_orders = SingleScan(market.in_flight_orders)
    value = own_quotes(market, 'LIFE-USDT', max_orders=20)
    assert value['pending_orders'] == {
        'BUY': dict(submission_pending=1, cancel_confirmation=0),
        'SELL': dict(submission_pending=0, cancel_confirmation=1),
    }
    assert value['buy_order_count'] == value['sell_order_count'] == 0


@pytest.mark.parametrize('stage', ['submission_pending', 'cancel_confirmation'])
def test_pending_stage_age_escalation_recovery_and_changed_stage(stage):
    observer = LiquidityObserver()
    value = pending(create=int(stage == 'submission_pending'), cancel=int(stage == 'cancel_confirmation'))
    first = sample(observer, 1000, value)['sides']['BUY']['diagnosis']
    assert first['stage'] == stage and first['source'] == 'order_tracker'
    assert first['observed_seconds'] == 0
    sample(observer, 1003, value)
    result = sample(observer, 1006, value)['sides']['BUY']['diagnosis']
    assert result['observed_seconds'] == 6 and 'support' in result['next_action'].lower()
    other = pending(cancel=1) if stage == 'submission_pending' else pending(create=1)
    assert sample(observer, 1007, other)['sides']['BUY']['diagnosis']['observed_seconds'] == 0
    assert sample(observer, 1008, observation())['sides']['BUY']['diagnosis'] is None


@pytest.mark.parametrize('boundary', ['gap', 'generation', 'owner', 'restart', 'unknown', 'stopped'])
def test_wait_age_does_not_cross_unknown_inactive_or_changed_owner_boundaries(boundary):
    observer = LiquidityObserver()
    value = pending(create=1)
    sample(observer, 1000, value)
    assert sample(observer, 1003, value)['sides']['BUY']['diagnosis']['observed_seconds'] == 3
    if boundary == 'restart':
        observer = LiquidityObserver(observer.frame)
    changes = {'generation': 2} if boundary == 'generation' else {'owner': 2} if boundary == 'owner' else {}
    if boundary == 'stopped':
        changes['status'] = 'stopped'
    result = sample(observer, 1010 if boundary == 'gap' else 1006,
                    observation(empty=True, valid=False) if boundary == 'unknown' else value, **changes)
    diagnosis = result['sides']['BUY']['diagnosis']
    if boundary in ('unknown', 'stopped'):
        assert diagnosis is None
    else:
        assert diagnosis['observed_seconds'] == 0


def test_one_sided_wait_and_pending_report_do_not_inflate_total_absence_or_leak_internal_fields():
    observer = LiquidityObserver()
    value = observation()
    value.update(sell_order_count=0, ask_ids=[], awaiting_executor_report=True)
    sample(observer, 1000, value)
    result = sample(observer, 1003, value)
    assert result['state'] == 'one_sided' and not result['warning_active']
    assert result['sides']['BUY']['diagnosis'] is None
    diagnosis = result['sides']['SELL']['diagnosis']
    assert diagnosis['stage'] == 'executor_report' and diagnosis['source'] == 'executor_event'
    assert diagnosis['observed_seconds'] == 3
    assert 'awaiting_executor_report' not in observer.frame['markets']['spot']
    assert 'pending_orders' not in observer.frame['markets']['spot']


def test_fresh_error_class_and_blocker_guidance_expire_without_leaking_raw_error_text():
    value = observation(empty=True, reasons={'BUY': ['proposal_evaluation_unavailable'], 'SELL': ['insufficient_base_balance']})
    value['proposal'].update(error_type='ValueError', message='SECRET=do-not-publish')
    observer = LiquidityObserver()
    result = sample(observer, 1000, value)
    assert result['sides']['BUY']['diagnosis']['error_type'] == 'ValueError'
    assert 'balance' in result['sides']['SELL']['diagnosis']['next_action'].lower()
    assert 'SECRET' not in json.dumps(observer.frame, default=str)
    result = sample(observer, 1006, value)
    assert result['sides']['BUY']['reason_codes'] == ['quote_reason_unavailable']
    assert result['sides']['BUY']['diagnosis']['stage'] == 'unknown'
    assert result['sides']['BUY']['diagnosis']['error_type'] is None


@pytest.mark.parametrize('bad', ['NaN', float('nan'), -1, True])
def test_invalid_cached_wait_duration_never_invents_age_or_breaks_observation(bad):
    observer = LiquidityObserver()
    sample(observer, 1000, pending(create=1))
    observer.frame['markets']['spot']['quote_activity']['sides']['BUY']['diagnosis']['observed_seconds'] = bad
    result = sample(observer, 1003, pending(create=1))
    assert result['sides']['BUY']['diagnosis']['observed_seconds'] == 0


@pytest.mark.parametrize('bad', ['ValueError: SECRET', 'ValueError\nSECRET', 'x' * 81])
def test_error_class_rejects_messages_and_unbounded_values(bad):
    value = observation(empty=True, reasons={'BUY': ['proposal_evaluation_unavailable']})
    value['proposal']['error_type'] = bad
    assert sample(LiquidityObserver(), 1000, value)['sides']['BUY']['diagnosis']['error_type'] is None


def test_stale_or_stopped_api_view_clears_diagnosis_without_mutating_checkpoint():
    observer = LiquidityObserver()
    sample(observer, 1000, pending(cancel=1))
    row = dict(database_now=1001, owner_uuid='owner', heartbeat_age_s=20, desired_state='running',
               mm_session=dict(id='session', generation=1, status='running'), status=dict(mm_liquidity=observer.frame))
    assert liquidity_view(row)['markets']['spot']['quote_activity']['sides']['BUY']['diagnosis'] is None
    row.update(heartbeat_age_s=1, desired_state='stopped')
    assert liquidity_view(row)['markets']['spot']['quote_activity']['sides']['BUY']['diagnosis'] is None
    assert observer.frame['markets']['spot']['quote_activity']['sides']['BUY']['diagnosis']['stage'] == 'cancel_confirmation'


def test_warning_log_exposes_stages_without_changing_warning_rate_limit(caplog):
    caplog.set_level('INFO')
    observer = LiquidityObserver()
    for at in (1000, 1003, 1006, 1009):
        sample(observer, at, pending(create=1))
    records = [r for r in caplog.records if getattr(r, 'event', None) == 'mm_no_quotes_warning']
    assert len(records) == 1
    assert 'BUY_stage=submission_pending' in records[0].getMessage()
    assert 'SELL_stage=unknown' in records[0].getMessage()
    from hummingbot.logger.service_logging import ServiceFormatter
    structured = json.loads(ServiceFormatter('json', {}).format(records[0]))
    assert structured['buy_wait_stage'] == 'submission_pending'
    assert structured['sell_wait_stage'] == 'unknown'


@pytest.mark.asyncio
async def test_capture_uses_current_executor_event_without_repeating_trading_evaluation(monkeypatch):
    from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    import hummingbot.core.mm_liquidity as module

    controller, _ = make_controller(configuration())
    await controller.update_processed_data()
    controller.executors_update_event.clear()
    source = controller.market_data_provider.get_connector_with_fallback.return_value
    source._user_stream_tracker.last_recv_time = 1000
    source.TICK_INTERVAL_LIMIT = 120
    controller.determine_executor_actions = MagicMock(side_effect=AssertionError('Do not repeat trading work'))
    owner = SimpleNamespace(generation=1, store=MagicMock())
    owner.store.read.return_value = {}
    session = SimpleNamespace(id='session', generation=1, profile_id='sideways', started_at=1000,
                              status='running', expires_at=1600)
    host = SimpleNamespace(mm_session=session, owner=owner, phase='running', _mm_controllers=lambda: [controller])
    monkeypatch.setattr(module, 'time', SimpleNamespace(time=lambda: 1000, monotonic=lambda: 1000))
    result = module.capture_liquidity(host)['markets']['spot']['quote_activity']
    assert isinstance(controller.executors_update_event, asyncio.Event)
    assert result['sides']['BUY']['diagnosis']['stage'] == 'executor_report'
    controller.determine_executor_actions.assert_not_called()
    source._api_request.assert_not_called()
