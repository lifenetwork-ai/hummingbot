from decimal import Decimal
from test.hummingbot.strategy_v2.controllers.test_quote_guard import book, observation, order
from types import SimpleNamespace

import pytest

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.utils.listing_bootstrap import BootstrapConfig, ListingBootstrap
from hummingbot.strategy_v2.utils.quote_guard import MarketObservation, QuoteGuardConfig, observe_market


def config():
    return BootstrapConfig(duration_seconds=30, max_anchor_move_bps="500", max_quote="50",
                           min_external_depth_quote="100", max_market_spread_bps="250", qualification_seconds="2")


def guard():
    policy = QuoteGuardConfig(enabled=True, mode="bootstrap_then_market", max_deviation_bps="200",
                              resume_deviation_bps="100", recovery_delay_seconds="0", bootstrap=config())
    item = ListingBootstrap(policy, anchor=Decimal("100"))
    item.restore(dict(started_at=None, expires_at=None, market_qualified=False), bound=True)
    return item


def empty(now=1000):
    connector = SimpleNamespace(name="okx", in_flight_orders={}, _order_tracker=SimpleNamespace(cached_orders={}, lost_orders={}))
    return observe_market(connector, book(bids=[], asks=[], timestamp=now), "LIFE-USDT", now=now,
                          max_age_seconds=10, max_levels=5, max_orders=20)


def test_empty_or_own_only_observation_is_explicitly_bootstrap_eligible_but_never_market_qualified():
    value = empty()
    assert value.bootstrap_eligible and value.market_mid is None
    own_only = observation([order()], bids=[(99, 10)], asks=[])
    assert own_only.bootstrap_eligible and own_only.external_bid_quote == 0
    assert not observation(timestamp=980).bootstrap_eligible


def test_cold_bootstrap_candidate_waits_for_durable_first_admission_and_expires_exactly():
    item = guard()
    status = item.evaluate(empty(), Decimal("100"), 0, sample=True)
    assert not status["ready"] and status["can_admit"]
    assert status["bootstrap"]["price_source"] == "configured_anchor"
    item.restore(dict(started_at=1000, expires_at=1030, market_qualified=False), bound=True)
    assert item.evaluate(empty(1029), Decimal("100"), 29)["ready"]
    result = item.evaluate(empty(1030), Decimal("100"), 30)
    assert not result["ready"] and result["reason_code"] == "bootstrap_expired"
    assert result["bootstrap"]["state"] == "waiting"


def test_outer_band_is_inclusive_and_model_prices_are_not_market_confirmed():
    item = guard()
    item.restore(dict(started_at=1000, expires_at=1030, market_qualified=False), bound=True)
    assert item.evaluate(empty(), Decimal("105"), 0)["ready"]
    assert not item.evaluate(empty(), Decimal("105.000001"), 0)["ready"]
    assert item.evaluate(empty(), Decimal("101"), 0, reference_model=True)["bootstrap"]["price_source"] == "reference_model"


def test_external_depth_and_spread_are_known_order_filtered_and_bounded():
    value = observation([order()], bids=[(99, 12)], asks=[(101, 3)])
    assert value.external_bid_quote == Decimal("198")
    assert value.external_ask_quote == Decimal("303")
    assert value.market_spread_bps == Decimal("200")


def test_continuous_qualification_uses_distinct_control_samples_and_requires_commit():
    item = guard()
    market = observation()
    item.evaluate(market, Decimal("100"), 0, sample=True)
    for _ in range(100):
        assert not item.evaluate(market, Decimal("100"), 99)["qualification_pending"]
    result = item.evaluate(observation(timestamp=1002, now=1002), Decimal("100"), 2, sample=True)
    assert result["qualification_pending"] and not result["ready"]
    item.restore(dict(started_at=None, expires_at=None, market_qualified=True), bound=True)
    assert item.evaluate(observation(timestamp=1003, now=1003), Decimal("100"), 3)["ready"]
    assert not item.evaluate(empty(1004), Decimal("100"), 4)["ready"]
    assert item.evaluate(empty(1004), Decimal("100"), 4)["bootstrap"]["state"] == "waiting"


def test_thin_market_violation_cannot_use_unfinished_qualification_as_bootstrap_bypass():
    item = guard()
    item.restore(dict(started_at=1000, expires_at=1030, market_qualified=False), bound=True)
    thin = observation(bids=[(109, Decimal(".01"))], asks=[(111, Decimal(".01"))])
    result = item.evaluate(thin, Decimal("100"), 0, sample=True)
    assert not result["ready"] and result["reason_code"] == "reference_deviation"


def test_qualification_resets_on_a_gap_and_restart_never_restores_a_dwell_timer():
    item = guard()
    item.evaluate(observation(), Decimal("100"), 0, sample=True)
    item.evaluate(empty(1001), Decimal("100"), 1, sample=True)
    assert not item.evaluate(observation(timestamp=1002, now=1002), Decimal("100"), 2, sample=True)["qualification_pending"]
    restarted = guard()
    restarted.restore(dict(started_at=1000, expires_at=1030, market_qualified=True), bound=True)
    assert not restarted.evaluate(observation(timestamp=1003, now=1003), Decimal("100"), 3, sample=True)["ready"]


def test_invalid_book_cannot_be_bootstrapped_and_unbound_policy_cannot_admit():
    item = guard()
    invalid = MarketObservation(None, 1000, 1000, detail="inconsistent_observation")
    assert not item.evaluate(invalid, Decimal("100"), 0)["can_admit"]
    item.restore(dict(started_at=None, expires_at=None, market_qualified=False), bound=False)
    assert not item.evaluate(empty(), Decimal("100"), 0)["can_admit"]


def controller_fixture(monkeypatch, **updates):
    from test.hummingbot.strategy_v2.controllers.test_life_mm import configuration, make_controller
    q = guard().config.model_dump()
    q['bootstrap']['max_quote'] = Decimal('50')
    q['recovery_delay_seconds'] = Decimal('0')
    settings = dict(total_amount_quote=Decimal('100'), quote_guards=q, runtime_config_managed=True,
                    mm_session_id='session', mm_generation=0)
    settings.update(updates)
    controller, prices = make_controller(configuration(**settings))
    connector = controller.market_data_provider.get_connector_with_fallback.return_value
    connector.name = 'okx'
    connector._order_tracker = SimpleNamespace(cached_orders={}, lost_orders={})
    connector.quantize_order_price.side_effect = lambda pair, price: price
    target = book(bids=[], asks=[], timestamp=1000)
    controller.market_data_provider.get_order_book.side_effect = lambda name, pair: target if pair == 'LIFE-USDT' else SimpleNamespace(snapshot_uid=1000, last_diff_uid=1000)
    clock = [0]
    monkeypatch.setattr('controllers.market_making.life_mm.time.monotonic', lambda: clock[0])
    return controller, connector, target, prices, clock


def test_controller_admission_clock_budget_and_outer_quantized_quotes(monkeypatch):
    controller, connector, target, _, _ = controller_fixture(monkeypatch)
    controller.update_reference()
    assert controller.processed_data['reason'] == 'bootstrap_binding_unavailable'
    controller.restore_bootstrap_episode(dict(started_at=None, expires_at=None, market_qualified=False))
    controller.update_reference()
    assert not controller.processed_data['ready'] and controller.processed_data['quote_guard']['can_admit']
    assert controller.reference_state()['life'] == '0.1'
    controller.restore_bootstrap_episode(dict(started_at=1000, expires_at=1030, market_qualified=False))
    controller.update_reference()
    assert controller.processed_data['ready']
    levels = controller.get_not_active_levels_ids([])
    assert sum((p * a for p, a in (controller.get_price_and_amount(level) for level in levels))) <= Decimal('50')
    price, amount = controller.get_price_and_amount(levels[0])
    assert controller.get_executor_config(levels[0], price, amount).use_configured_entry_price
    connector.quantize_order_price.side_effect = lambda pair, price: Decimal('.106')
    controller.update_reference()
    assert not controller.processed_data['ready'] and controller.processed_data['reason'] == 'quote_out_of_bounds'


def test_controller_own_quotes_never_rebase_and_widened_levels_block(monkeypatch):
    controller, connector, target, _, _ = controller_fixture(monkeypatch)
    controller.restore_bootstrap_episode(dict(started_at=1000, expires_at=1030, market_qualified=False))
    controller.update_reference()
    own_bid = order(price='.099', amount='10')
    own_ask = order(price='.101', amount='10', side=TradeType.SELL)
    connector.in_flight_orders = {own_bid.client_order_id: own_bid, 'ask': own_ask}
    from hummingbot.core.data_type.order_book_row import OrderBookRow
    target.apply_snapshot([OrderBookRow(.099, 10, 1001)], [OrderBookRow(.101, 10, 1001)], 1001)
    controller.market_data_provider.time.return_value = 1001
    controller.update_reference(sample_guard=True)
    assert controller._life_anchor == Decimal('.1')
    assert not controller.processed_data['quote_guard']['qualification_pending']
    controller.processed_data['spread_multiplier'] = Decimal('10')
    controller.update_reference()
    assert controller.processed_data['reason'] == 'quote_out_of_bounds'


def test_bootstrap_cannot_be_used_in_unmanaged_cli(monkeypatch):
    with pytest.raises(ValueError, match='managed'):
        controller_fixture(monkeypatch, runtime_config_managed=False)


def test_sparse_samples_after_process_stall_do_not_prove_continuous_qualification():
    item = guard()
    item.evaluate(observation(), Decimal('100'), 0, sample=True)
    assert not item.evaluate(observation(timestamp=1100, now=1100), Decimal('100'), 100, sample=True)['qualification_pending']


def test_depth_limit_does_not_prove_empty_or_own_only_book():
    value = observation([order()], bids=[(99, 10), (98, 20)], max_levels=1)
    assert not value.bootstrap_eligible


def test_bootstrap_follow_uses_fresh_basket_returns_without_reanchoring_own_book(monkeypatch):
    controller, _, _, prices, _ = controller_fixture(monkeypatch, scenario='follow_market', references=[
        dict(pair='BTC-USDT', weight='.6'), dict(pair='ETH-USDT', weight='.4')])
    controller.restore_bootstrap_episode(dict(started_at=1000, expires_at=1030, market_qualified=False))
    controller.update_reference()
    prices.update({'BTC-USDT': Decimal('110'), 'ETH-USDT': Decimal('9')})
    controller.update_reference()
    assert controller.processed_data['reference_price'] == Decimal('.102')
    assert controller.processed_data['quote_guard']['bootstrap']['price_source'] == 'reference_model'
    controller.market_data_provider.time.return_value = 1040
    controller.update_reference()
    assert not controller.processed_data['ready']


def test_model_only_manual_range_applies_to_outer_quote_levels(monkeypatch):
    limits = dict(lower_price='.0995', upper_price='.1005', resume_lower_price='.0998', resume_upper_price='.1002')
    controller, _, _, _, _ = controller_fixture(monkeypatch, sideways_range=limits)
    controller.restore_bootstrap_episode(dict(started_at=1000, expires_at=1030, market_qualified=False))
    controller.update_reference()
    assert controller.processed_data['reason'] == 'quote_out_of_bounds'
    assert not controller.processed_data['quote_guard']['bootstrap']['market_breakout_available']


def test_bootstrap_status_serializes_source_bounds_clock_and_unknown_outside_price():
    from hummingbot.cli.admin_models import QuoteGuardStatus
    item = guard()
    item.restore(dict(started_at=1000, expires_at=1030, market_qualified=False), bound=True)
    result = QuoteGuardStatus.model_validate(item.evaluate(empty(1001), Decimal('100'), 1)).model_dump(mode='json')
    assert result['bootstrap']['price_source'] == 'configured_anchor'
    assert result['bootstrap']['anchor'] == '100'
    assert result['bootstrap']['remaining_seconds'] == 29
    assert result['market_mid'] is None and result['data_quality'] == 'unavailable'
    assert result['bootstrap']['expires_at'].endswith('Z')


def test_bootstrap_send_readiness_does_not_materialize_full_book_frames(monkeypatch):
    controller, _, target, _, _ = controller_fixture(monkeypatch)

    class BoundedBook:
        def __getattr__(self, name):
            if name == 'snapshot':
                raise AssertionError('full book DataFrame on hot path')
            return getattr(target, name)

    controller.market_data_provider.get_order_book.side_effect = lambda *args: BoundedBook()
    controller.restore_bootstrap_episode(dict(started_at=1000, expires_at=1030, market_qualified=False))
    controller.update_reference()
    assert controller.processed_data['ready']


@pytest.mark.parametrize('failure', ['stale', 'metadata'])
def test_armed_bootstrap_cannot_bypass_required_market_data(monkeypatch, failure):
    controller, connector, _, _, _ = controller_fixture(monkeypatch)
    controller.restore_bootstrap_episode(dict(started_at=1000, expires_at=1030, market_qualified=False))
    if failure == 'stale':
        controller.market_data_provider.time.return_value = 1011
    else:
        connector.trading_rules = {}
    controller.update_reference()
    assert not controller.processed_data['ready']
    assert not controller.processed_data['quote_guard']['can_admit']
