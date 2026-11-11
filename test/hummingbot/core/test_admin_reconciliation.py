from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from hummingbot.core.admin_reconciliation import ReconciliationRequest, check_market, expected_positions, perform_check
from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType


def fixture():
    now = datetime.now(timezone.utc)
    snapshot = dict(truncated=False, fingerprint="before", orders=[dict(
        id="order-1", exchange_order_id="17", connector="okx", pair="LIFE-USDT", side="BUY", order_type="LIMIT_MAKER",
        amount=Decimal("10"), price=Decimal("0.1"), status="BuyOrderCompleted", created_at=now - timedelta(seconds=10))],
        fills=[dict(connector="okx", order_id="order-1", id="fill-1", amount="10", price="0.1",
                    fee={"percent": "0", "flat_fees": [{"token": "USDT", "amount": "0.01"}]})])
    detail = dict(ordId="17", clOrdId="order-1", instId="LIFE-USDT", side="buy", ordType="post_only",
                  sz="10", px="0.1", state="filled", accFillSz="10", uTime=str(int(now.timestamp() * 1000) - 1000))
    fill = dict(ordId="17", clOrdId="order-1", tradeId="fill-1", billId="bill-1", fillSz="10", fillPx="0.1",
                fee="-0.01", feeCcy="USDT", ts=str(int(now.timestamp() * 1000) - 1000))
    connector = SimpleNamespace(client_order_id_prefix="order-", _api_get=AsyncMock(return_value={"code": "0", "data": []}),
                                name="okx", _request_order_update=AsyncMock(return_value={"code": "0", "data": [detail]}),
                                _request_order_fills=AsyncMock(return_value={"code": "0", "data": [fill]}),
                                exchange_symbol_associated_to_pair=AsyncMock(return_value="LIFE-USDT"))
    return now, snapshot, connector


@pytest.mark.asyncio
async def test_observational_check_matches_quantities_and_original_fees_without_mutating_connector():
    now, snapshot, connector = fixture()
    result = await check_market(connector, "spot", snapshot, ReconciliationRequest(market="spot"), (None, False), now)
    assert result["state"] == "matched" and result["complete"]
    assert result["orders_checked"] == 1 and result["fills_checked"] == 1
    assert set(vars(connector)) == {"client_order_id_prefix", "_api_get", "name", "_request_order_update", "_request_order_fills",
                                    "exchange_symbol_associated_to_pair"}


def open_order_fixture():
    now, snapshot, connector = fixture()
    snapshot["orders"][0]["status"] = "BuyOrderCreated"
    snapshot["fills"] = []
    connector._request_order_update.return_value["data"][0].update(state="live", accFillSz="0")
    connector._request_order_fills.return_value = {"code": "0", "data": []}
    return now, snapshot, connector


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,kind", [
    ("instId", "BTC-USDT", "order_instrument"),
    ("side", "sell", "order_side"),
    ("sz", "10.00000001", "order_quantity"),
    ("px", "0.10000001", "order_price"),
    ("ordType", "limit", "order_type"),
])
async def test_open_order_with_matching_ids_but_wrong_working_terms_is_a_mismatch(field, value, kind):
    now, snapshot, connector = open_order_fixture()
    connector._request_order_update.return_value["data"][0][field] = value
    result = await check_market(connector, "spot", snapshot, ReconciliationRequest(market="spot"), (None, False), now)
    assert result["state"] == "mismatch", result
    assert result["complete"]
    assert kind in {row["kind"] for row in result["differences"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["instId", "side", "sz", "px", "ordType"])
async def test_missing_working_order_terms_cannot_report_match(field):
    now, snapshot, connector = open_order_fixture()
    del connector._request_order_update.return_value["data"][0][field]
    result = await check_market(connector, "spot", snapshot, ReconciliationRequest(market="spot"), (None, False), now)
    assert result["state"] == "incomplete", result
    assert "order_details_incomplete" in result["reasons"]


@pytest.mark.asyncio
async def test_market_order_has_base_currency_size_and_no_limit_price():
    now, snapshot, connector = open_order_fixture()
    snapshot["orders"][0].update(order_type="MARKET", price=Decimal("NaN"))
    connector._request_order_update.return_value["data"][0].update(ordType="market", px="", tgtCcy="base_ccy")
    result = await check_market(connector, "spot", snapshot, ReconciliationRequest(market="spot"), (None, False), now)
    assert result["state"] == "matched", result


@pytest.mark.asyncio
@pytest.mark.parametrize("size_currency,state", [("quote_ccy", "mismatch"), ("", "incomplete")])
async def test_spot_market_order_size_currency_is_checked(size_currency, state):
    now, snapshot, connector = open_order_fixture()
    snapshot["orders"][0].update(order_type="MARKET", price=Decimal("NaN"))
    connector._request_order_update.return_value["data"][0].update(ordType="market", px="", tgtCcy=size_currency)
    result = await check_market(connector, "spot", snapshot, ReconciliationRequest(market="spot"), (None, False), now)
    assert result["state"] == state, result
    if size_currency:
        assert "order_size_currency" in {row["kind"] for row in result["differences"]}


@pytest.mark.asyncio
async def test_perpetual_order_quantity_uses_existing_contract_conversion():
    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative

    now, snapshot, fake = open_order_fixture()
    snapshot["orders"][0]["connector"] = "okx_perpetual"
    fake._request_order_update.return_value["data"][0].update(instId="LIFE-USDT-SWAP", sz="100")
    connector = OkxPerpetualDerivative(trading_pairs=["LIFE-USDT"], trading_required=False)
    connector._contract_sizes["LIFE-USDT"] = Decimal("0.1")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT-SWAP")
    connector._api_get = fake._api_get
    connector._request_order_update = fake._request_order_update
    connector._request_order_fills = fake._request_order_fills
    result = await check_market(connector, "perpetual", snapshot, ReconciliationRequest(market="perpetual"), ({}, False), now)
    assert result["state"] == "matched", result


@pytest.mark.asyncio
async def test_order_amendment_between_reads_is_incomplete_even_if_timestamp_is_unchanged():
    now, snapshot, connector = open_order_fixture()
    detail = connector._request_order_update.return_value["data"][0]
    connector._request_order_update.side_effect = [
        {"code": "0", "data": [detail]}, {"code": "0", "data": [{**detail, "px": "0.2"}]}]
    result = await check_market(connector, "spot", snapshot, ReconciliationRequest(market="spot"), (None, False), now)
    assert result["state"] == "incomplete", result
    assert "exchange_changed_during_check" in result["reasons"]


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,kind", [("fillSz", "9", "fill_quantity"), ("fillPx", "0.2", "fill_price"), ("fee", "-0.03", "fill_fee")])
async def test_fill_differences_are_reported(field, value, kind):
    now, snapshot, connector = fixture()
    connector._request_order_fills.return_value["data"][0][field] = value
    result = await check_market(connector, "spot", snapshot, ReconciliationRequest(market="spot"), (None, False), now)
    assert result["state"] in ("mismatch", "incomplete")
    assert kind in {row["kind"] for row in result["differences"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("field,kind", [("fillSz", "fill_quantity"), ("fillPx", "fill_price")])
async def test_precision_sub_micro_difference_is_not_reported_as_matched(field, kind):
    now, snapshot, connector = fixture()
    original = connector._request_order_fills.return_value["data"][0][field]
    connector._request_order_fills.return_value["data"][0][field] = str(Decimal(original) + Decimal("0.00000001"))
    result = await check_market(connector, "spot", snapshot, ReconciliationRequest(), (None, False), now)
    assert result["state"] != "matched"
    assert kind in {row["kind"] for row in result["differences"]}


@pytest.mark.asyncio
async def test_full_fill_page_is_incomplete_and_uses_bill_cursor():
    now, snapshot, connector = fixture()
    fill = connector._request_order_fills.return_value["data"][0]
    connector._request_order_fills.return_value = {"code": "0", "data": [{**fill, "tradeId": str(index), "billId": str(index)} for index in range(100)]}
    result = await check_market(connector, "spot", snapshot, ReconciliationRequest(max_pages=1), (None, False), now)
    assert result["state"] == "incomplete" and not result["complete"]
    assert "fill_page_limit_or_gap" in result["reasons"]


@pytest.mark.asyncio
async def test_local_fill_race_and_unavailable_other_market_cannot_report_match():
    now, snapshot, connector = fixture()
    store = MagicMock()
    store.reconciliation_scope.side_effect = [snapshot, {**snapshot, "fingerprint": "after"}]
    core = SimpleNamespace(strategy=None, connector_manager=SimpleNamespace(connectors={"okx": connector}))
    report = await perform_check(store, core, ReconciliationRequest(market="all"))
    assert report["state"] == "unavailable"
    assert report["markets"][0]["state"] == "incomplete"
    assert report["markets"][1]["state"] == "unavailable"


def test_expected_perpetual_exposure_includes_active_and_held_both_sides():
    hold = SimpleNamespace(connector_name="okx_perpetual", trading_pair="LIFE-USDT", net_amount_base=Decimal("-4"))
    executor = SimpleNamespace(config=SimpleNamespace(connector_name="okx_perpetual", trading_pair="LIFE-USDT", side=TradeType.BUY),
                               is_closed=False, amount_to_close=Decimal("6"))
    core = SimpleNamespace(strategy=SimpleNamespace(executor_orchestrator=SimpleNamespace(
        positions_held={"life": [hold]}, active_executors={"life": [executor]})))
    expected, unsettled = expected_positions(core)
    assert expected == {("LIFE-USDT", "LONG"): Decimal("6"), ("LIFE-USDT", "SHORT"): Decimal("4")}
    assert not unsettled


@pytest.mark.asyncio
async def test_missing_exchange_fill_page_and_concurrent_exchange_fill_are_incomplete():
    now, snapshot, connector = fixture()
    connector._request_order_fills.return_value = {"code": "0", "data": []}
    result = await check_market(connector, "spot", snapshot, ReconciliationRequest(), (None, False), now)
    assert result["state"] == "incomplete"
    assert "exchange_fill_coverage_incomplete" in result["reasons"]
    now, snapshot, connector = fixture()
    connector._request_order_fills.return_value["data"][0]["ts"] = str(int(now.timestamp() * 1000) + 1)
    result = await check_market(connector, "spot", snapshot, ReconciliationRequest(), (None, False), now)
    assert result["state"] == "incomplete"


@pytest.mark.asyncio
async def test_one_slow_market_does_not_erase_the_other_completed_result():
    import asyncio
    now, snapshot, connector = fixture()

    async def stalled(*args, **kwargs):
        await asyncio.sleep(1)
    slow = SimpleNamespace(name="okx_perpetual", client_order_id_prefix="order-", _api_get=AsyncMock(side_effect=stalled))
    store = MagicMock()
    store.reconciliation_scope.return_value = snapshot
    core = SimpleNamespace(strategy=None, connector_manager=SimpleNamespace(connectors={"okx": connector, "okx_perpetual": slow}))
    report = await perform_check(store, core, ReconciliationRequest(market="all"), timeout=0.05)
    assert report["state"] == "unavailable"
    assert report["markets"][0]["state"] == "matched"
    assert report["markets"][1]["state"] == "unavailable"


@pytest.mark.asyncio
async def test_perpetual_comparison_uses_contract_metadata_and_keeps_both_legs_without_mutation():
    from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
    connector = OkxPerpetualDerivative(trading_pairs=["LIFE-USDT"], trading_required=False)
    connector._contract_sizes["LIFE-USDT"] = Decimal("0.1")
    connector.exchange_symbol_associated_to_pair = AsyncMock(return_value="LIFE-USDT-SWAP")

    async def read(path_url, **kwargs):
        return {"code": "0", "data": [] if path_url.endswith("orders-pending") else [
            dict(instId="LIFE-USDT-SWAP", posSide="long", pos="60"),
            dict(instId="LIFE-USDT-SWAP", posSide="short", pos="40")]}
    connector._api_get = AsyncMock(side_effect=read)
    snapshot = dict(truncated=False, orders=[], fills=[])
    result = await check_market(connector, "perpetual", snapshot, ReconciliationRequest(),
                                ({("LIFE-USDT", "LONG"): Decimal("6"), ("LIFE-USDT", "SHORT"): Decimal("4")}, False), datetime.now(timezone.utc))
    assert result["state"] == "matched" and result["positions_checked"] == 2
    assert connector.account_positions == {} and connector.position_observations == {}


def test_expected_positions_uses_the_actual_executor_runtime_interface():
    from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
    from hummingbot.strategy_v2.models.base import RunnableStatus
    executor = object.__new__(PositionExecutor)
    executor.config = SimpleNamespace(connector_name="okx_perpetual", trading_pair="LIFE-USDT", side=TradeType.BUY)
    executor._status = RunnableStatus.RUNNING
    executor._open_order = executor._close_order = None
    core = SimpleNamespace(strategy=SimpleNamespace(executor_orchestrator=SimpleNamespace(
        positions_held={}, active_executors={"life": [executor]})))
    assert expected_positions(core) == ({}, False)


def native_closing_scope(side=TradeType.SELL, *, filled='.4', held='2', identity='close'):
    from hummingbot.core.data_type.in_flight_order import InFlightOrder
    from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
    from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
    from hummingbot.strategy_v2.models.base import RunnableStatus
    from hummingbot.strategy_v2.models.executors import TrackedOrder
    executor = object.__new__(OrderExecutor)
    executor.config = OrderExecutorConfig(
        id=identity, timestamp=1000, controller_id='maker',
        connector_name='okx_perpetual', trading_pair='LIFE-USDT', side=side,
        amount=Decimal('2'), price=Decimal('100'), position_action=PositionAction.CLOSE,
        execution_strategy=ExecutionStrategy.LIMIT_MAKER,
        mm_session_id='session', mm_generation=0, level_id='close')
    executor._status = RunnableStatus.RUNNING
    executor.close_type = None
    order = InFlightOrder(
        identity + '-order', 'LIFE-USDT', OrderType.LIMIT_MAKER, side, Decimal('2'),
        creation_timestamp=1000, price=Decimal('100'), position=PositionAction.CLOSE)
    order.executed_amount_base = Decimal(filled)
    executor._order = TrackedOrder(identity + '-order')
    executor._order.order = order
    executor._partial_filled_orders, executor._failed_orders, executor._canceled_orders = [], [], []
    sign = 1 if side == TradeType.SELL else -1
    hold = SimpleNamespace(connector_name='okx_perpetual', trading_pair='LIFE-USDT', net_amount_base=Decimal(held) * sign)
    runtime = SimpleNamespace(
        positions_held={'maker': [hold]}, active_executors={'maker': [executor]}, executors_ids_position_held=set())
    return SimpleNamespace(strategy=SimpleNamespace(executor_orchestrator=runtime)), executor, hold, runtime


@pytest.mark.parametrize('side', [TradeType.BUY, TradeType.SELL])
@pytest.mark.parametrize('stage', ['active', 'terminal_unapplied', 'applied'])
def test_native_partial_close_reconciles_the_opposite_held_leg_exactly_once(side, stage):
    from hummingbot.strategy_v2.models.base import RunnableStatus
    from hummingbot.strategy_v2.models.executors import CloseType
    core, executor, hold, runtime = native_closing_scope(side)
    if stage != 'active':
        executor._status, executor.close_type = RunnableStatus.TERMINATED, CloseType.POSITION_HOLD
    if stage == 'applied':
        hold.net_amount_base *= Decimal('.8')
        runtime.executors_ids_position_held.add(executor.config.id)
    before = hold.net_amount_base, executor.executed_amount_base, set(runtime.executors_ids_position_held)
    expected = Decimal('2') if stage == 'terminal_unapplied' else Decimal('1.6')
    positions, unsettled = expected_positions(core)
    assert positions == {('LIFE-USDT', 'SHORT' if side == TradeType.BUY else 'LONG'): expected}
    assert unsettled is (stage == 'terminal_unapplied')
    assert before == (hold.net_amount_base, executor.executed_amount_base, runtime.executors_ids_position_held)


def test_unfilled_closing_order_does_not_subtract_its_reserved_quantity():
    core, _, _, _ = native_closing_scope(filled='0')
    assert expected_positions(core) == ({('LIFE-USDT', 'LONG'): Decimal('2')}, False)


@pytest.mark.parametrize('filled', ['2.1', '-.1', 'NaN'])
def test_invalid_or_excess_close_cannot_be_reconciled_as_a_clamped_flat_position(filled):
    core, _, _, _ = native_closing_scope(filled=filled)
    assert expected_positions(core) == (None, False)


def test_closing_projection_cannot_borrow_another_controllers_hold():
    core, executor, _, runtime = native_closing_scope()
    executor.config.controller_id = 'other'
    runtime.active_executors = {'other': [executor]}
    assert expected_positions(core) == (None, False)


def test_unmanaged_order_executor_still_requires_an_explicit_supported_projection():
    core, executor, _, _ = native_closing_scope()
    executor.config.mm_session_id = executor.config.mm_generation = None
    assert expected_positions(core) == (None, False)


def test_concurrent_closes_keep_hedge_legs_separate_and_include_untransferred_open_fills():
    core, _, _, runtime = native_closing_scope()
    _, short_close, short_hold, _ = native_closing_scope(TradeType.BUY, filled='.7', held='3', identity='short')
    opened = SimpleNamespace(config=SimpleNamespace(connector_name='okx_perpetual', trading_pair='LIFE-USDT', side=TradeType.BUY),
                             is_closed=False, amount_to_close=Decimal('.2'))
    runtime.positions_held['maker'].append(short_hold)
    runtime.active_executors['maker'].extend([short_close, opened])
    assert expected_positions(core) == ({('LIFE-USDT', 'LONG'): Decimal('1.8'),
                                        ('LIFE-USDT', 'SHORT'): Decimal('2.3')}, False)


def test_combined_filled_closes_cannot_exceed_the_same_owned_hedge_bucket():
    core, _, _, runtime = native_closing_scope(filled='.6', held='1')
    _, other, _, _ = native_closing_scope(filled='.6', held='1', identity='other')
    runtime.active_executors['maker'].append(other)
    assert expected_positions(core) == (None, False)


def test_a_fully_filled_active_close_projects_flat_before_its_held_transfer():
    core, _, _, _ = native_closing_scope(filled='2')
    assert expected_positions(core) == ({}, False)


@pytest.mark.asyncio
async def test_account_pending_orders_from_other_market_do_not_pollute_scope():
    now, snapshot, connector = fixture()
    connector._api_get.return_value = {"code": "0", "data": [dict(instId="LIFE-USDT-SWAP", clOrdId="order-perpetual", ordId="18")]}
    result = await check_market(connector, "spot", snapshot, ReconciliationRequest(), (None, False), now)
    assert result["state"] == "matched", result
