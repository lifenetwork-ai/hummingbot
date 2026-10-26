"""Owner adapter for spot cashflow; recorder rows remain the only fill ledger."""
import asyncio
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.event.events import BuyOrderCreatedEvent, MarketEvent, OrderFilledEvent
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.core.spot_outflow import MAX_FILLS, ZERO, SpotOutflowConfig, SpotOutflowProjection
from hummingbot.model.order import Order
from hummingbot.model.trade_fill import TradeFill


class SpotOutflowGuard:
    def __init__(self, owner, policy):
        self.owner, self.policy = owner, policy
        self.config = SpotOutflowConfig.model_validate({key: value for key, value in policy.items() if key != "trading_pair"})
        self.binding = None
        self.projection = None
        self.pending = []
        self.pending_overflow = False
        self.orders = {}
        self.terminal_pending = set()
        self.initializing = False
        self.reason_code = "spot_outflow_initializing"
        self.last_buy_denial = None

    def identity(self):
        identities = {(getattr(connector, "account_uid", None), getattr(connector, "trading_environment", None))
                      for name, connector in self.owner.connectors.items() if name in ("okx", "okx_perpetual")}
        if not identities or any(not isinstance(uid, str) or not uid or environment not in ("live", "demo")
                                 for uid, environment in identities):
            raise ValueError("spot_outflow_identity_unavailable")
        if len(identities) != 1:
            raise ValueError("spot_outflow_binding_mismatch")
        uid, environment = identities.pop()
        return dict(account_id=uid, environment=environment, trading_pair=self.policy["trading_pair"])

    async def initialize(self):
        row = self.owner.check_owner()
        try:
            binding = self.identity()
        except ValueError as error:
            self.reason_code = str(error)
            if self.reason_code == "spot_outflow_binding_mismatch":
                self.owner.require_recovery(self.reason_code)
            return False
        previous = (row["recovery_state"] or {}).get("spot_outflow")
        if previous is not None and (previous.get("binding") != binding or previous.get("policy") != self.policy):
            self.reason_code = "spot_outflow_binding_mismatch"
            self.owner.require_recovery(self.reason_code)
            return False
        now_ms = int(row["database_now"] * 1000)
        if previous is None:
            with self.owner.engine.connect() as connection:
                legacy = connection.execute(select(Order.id).where(Order.market == "okx").limit(1)).first() is not None
            previous = dict(binding=binding, policy=self.policy,
                            coverage_start_ms=now_ms if legacy else now_ms - self.config.window_seconds * 1000)

            def bind(state):
                if state.get("spot_outflow") not in (None, previous):
                    raise SubmissionBlocked("spot_outflow policy changed during initialization")
                state["spot_outflow"] = previous
            if not self.owner._mutate_recovery(bind, expected_version=self.owner.version):
                return False
        self.binding = binding
        self.initializing = True
        try:
            try:
                projection = await asyncio.to_thread(self._load, previous["coverage_start_ms"], now_ms)
            except (SQLAlchemyError, OSError):
                self.owner.check_owner()
                self.reason_code = "spot_outflow_projection_unavailable"
                return False
            if self.owner.check_owner()["control_version"] != self.owner.version:
                return False
            for fill in self.pending:
                projection.add_fill(fill)
            if self.pending_overflow:
                projection.failure = "spot_outflow_projection_limit"
            self.pending.clear()
            self.projection = projection
            self.reason_code = None
            return True
        finally:
            self.initializing = False

    def _load(self, coverage, now_ms):
        projection = SpotOutflowProjection(self.config, self.policy["trading_pair"], coverage_start_ms=coverage)
        projection.advance(now_ms)
        columns = (TradeFill.market, TradeFill.symbol, TradeFill.order_id, TradeFill.exchange_trade_id,
                   TradeFill.timestamp, TradeFill.trade_type, TradeFill.price, TradeFill.amount, TradeFill.trade_fee)
        # Existing market/symbol/timestamp index; streamed once, never per tick/send.
        query = select(*columns).where(
            TradeFill.market == "okx", TradeFill.symbol == self.policy["trading_pair"],
            TradeFill.timestamp >= now_ms - self.config.window_seconds * 1000).limit(MAX_FILLS + 1)
        with self.owner.engine.connect().execution_options(yield_per=1000) as connection:
            for row in connection.execute(query).mappings():
                projection.add_fill(dict(row))
                if projection.failure:
                    break
        return projection

    def fill_committed(self, row):
        if row["market"] != "okx" or row["symbol"] != self.policy["trading_pair"]:
            return
        if self.projection is None or self.initializing:
            if len(self.pending) >= MAX_FILLS:
                self.reason_code = "spot_outflow_projection_limit"
                self.pending_overflow = True
            else:
                self.pending.append(row)
        else:
            self.projection.add_fill(row)

    def _identity_matches(self):
        try:
            current = self.identity()
        except ValueError as error:
            self.reason_code = str(error)
            return False
        if self.binding is not None and current != self.binding:
            self.reason_code = "spot_outflow_binding_mismatch"
            self.owner.require_recovery(self.reason_code)
            return False
        return True

    def _flat_fee(self, connector, order):
        base, quote = order.trading_pair.split("-")
        fee = connector.get_fee(base, quote, order.order_type, order.trade_type, order.amount, order.price, is_maker=True)
        percent = Decimal(fee.percent)
        if not percent.is_finite() or abs(percent) > 1:
            raise ValueError("spot_outflow_fee_unavailable")
        token = fee.percent_token or (quote if isinstance(fee, AddedToCostTradeFee) else base)
        if percent and token not in (base, quote):
            raise ValueError("spot_outflow_fee_unavailable")
        if token == quote and percent > self.config.fee_allowance_rate:
            raise ValueError("spot_outflow_fee_allowance_insufficient")
        flat = ZERO
        for item in fee.flat_fees:
            amount = Decimal(item.amount)
            if not item.token or not amount.is_finite():
                raise ValueError("spot_outflow_fee_unavailable")
            if item.token == quote:
                flat += max(ZERO, amount)
        return flat

    def before_order(self, connector, order, row):
        if not self._identity_matches():
            raise SubmissionBlocked(self.reason_code)
        if connector.name != "okx" or order.trade_type != TradeType.BUY:
            return
        if self.projection is not None:
            self.projection.advance(int(row["database_now"] * 1000))
        self._mark_terminal_orders()
        try:
            if order.trading_pair != self.policy["trading_pair"] or order.order_type != OrderType.LIMIT_MAKER:
                raise ValueError("spot_outflow_order_scope_unavailable")
            if self.projection is None or self.reason_code:
                raise ValueError(self.reason_code or "spot_outflow_initializing")
            if self.terminal_pending:
                raise ValueError("spot_outflow_reconciling")
            flat = self._flat_fee(connector, order)
            if not self.projection.reserve(order, flat):
                raise ValueError(self.projection.reason() or "spot_outflow_limit")
        except (ValueError, TypeError, ArithmeticError) as error:
            self.last_buy_denial = str(error)
            raise SubmissionBlocked(self.last_buy_denial) from None
        self.last_buy_denial = None
        self.orders[order.client_order_id] = (connector, order)

    def preview(self, connector, amount, price):
        """A cheap proposal filter; pre-intent/final-send remain authoritative."""
        self._mark_terminal_orders()
        if (not self._identity_matches() or self.projection is None or self.reason_code
                or self.projection.reason() or self.terminal_pending):
            return False
        order = SimpleNamespace(trading_pair=self.policy["trading_pair"], amount=amount, price=price,
                                order_type=OrderType.LIMIT_MAKER, trade_type=TradeType.BUY)
        try:
            required = amount * price * (1 + self.config.fee_allowance_rate) + self._flat_fee(connector, order)
            allowed = max(ZERO, self.projection.net) + self.projection.reserved() + required <= self.config.max_net_outflow_quote
            self.last_buy_denial = None if allowed else "spot_outflow_limit"
            return allowed
        except (ValueError, TypeError, ArithmeticError):
            self.last_buy_denial = "spot_outflow_fee_unavailable"
            return False

    def authorize(self, data, row):
        if not self._identity_matches():
            raise SubmissionBlocked(self.reason_code)
        client_id = data.get("clOrdId")
        if client_id in self.orders:
            connector, order = self.orders[client_id]
            if (data.get("side") != "buy" or data.get("instId") != order.trading_pair or data.get("tdMode") != "cash"
                    or Decimal(data.get("sz", "NaN")) != order.amount or Decimal(data.get("px", "NaN")) != order.price):
                raise SubmissionBlocked("spot_outflow order terms changed after reservation")
            self.before_order(connector, order, row)
        elif data.get("instId") == self.policy["trading_pair"] and data.get("side") == "buy":
            raise SubmissionBlocked("spot_outflow reservation is missing")

    def release_unsent(self, client_id):
        if self.projection is not None:
            self.projection.release_unsent(client_id)
        self.orders.pop(client_id, None)
        self.terminal_pending.discard(client_id)

    def mark_terminal_pending(self, client_id):
        self.terminal_pending.add(client_id)

    def _mark_terminal_orders(self):
        for client_id, (_, order) in self.orders.items():
            if order.is_done:
                self.mark_terminal_pending(client_id)

    async def reconcile_terminal(self, *, all_orders=False):
        for client_id, (_, order) in list(self.orders.items()):
            if order.is_done or all_orders:
                self.mark_terminal_pending(client_id)
        for client_id in list(self.terminal_pending)[:MAX_FILLS if all_orders else 4]:
            try:
                if not await asyncio.wait_for(self._confirm(client_id), timeout=10):
                    continue
            except (ValueError, OSError, asyncio.TimeoutError, KeyError, ArithmeticError):
                continue  # Retain the reservation and explicit BUY-only wait.
            self.terminal_pending.discard(client_id)
            self.orders.pop(client_id, None)
        if all_orders and self.terminal_pending:
            raise SubmissionBlocked("spot_outflow terminal fill reconciliation is incomplete")

    async def _confirm(self, client_id):
        from hummingbot.connector.markets_recorder import MarketsRecorder
        connector, order = self.orders[client_id]
        response = await connector._request_order_update(order)
        if str(response.get("code")) != "0" or len(response.get("data", [])) != 1:
            raise ValueError("Incomplete terminal order")
        first = response["data"][0]
        if first.get("state") not in ("filled", "canceled"):
            return False
        if (first.get("clOrdId") != client_id or str(first.get("ordId")) != order.exchange_order_id
                or first.get("instId") != order.trading_pair or first.get("side") != "buy"
                or Decimal(first["sz"]) != order.amount or Decimal(first["px"]) != order.price):
            raise ValueError("Terminal order identity/terms mismatch")
        fills, after, cursors = {}, None, set()
        seen_amount = ZERO
        for page_index in range(100):
            response = await connector._request_order_fills(order, after=after, limit=100)
            if str(response.get("code")) != "0" or not isinstance(response.get("data"), list):
                raise ValueError("Incomplete terminal fills")
            page = response["data"]
            for fill in page:
                identity = str(fill["tradeId"])
                if identity in fills or str(fill["ordId"]) != order.exchange_order_id:
                    raise ValueError("Overlapping fill identities")
                size = Decimal(fill["fillSz"])
                if not size.is_finite() or size <= 0:
                    raise ValueError("Invalid fill quantity")
                seen_amount += size
                fills[identity] = fill
            if len(page) < 100 or seen_amount == Decimal(first["accFillSz"]):
                break
            after = str(page[-1].get("billId") or "")
            if not after or after in cursors or page_index == 99:
                raise ValueError("Incomplete fill pagination")
            cursors.add(after)
        total = sum((Decimal(fill["fillSz"]) for fill in fills.values()), ZERO)
        if total != Decimal(first["accFillSz"]) or (first["state"] == "filled" and total != order.amount):
            raise ValueError("Terminal cumulative fills are incomplete")
        final = await connector._request_order_update(order)
        keys = ("ordId", "clOrdId", "instId", "side", "sz", "px", "state", "accFillSz", "uTime")
        if (str(final.get("code")) != "0" or len(final.get("data", [])) != 1
                or any(final["data"][0].get(key) != first.get(key) for key in keys)):
            raise ValueError("Order changed during terminal proof")
        recorder = MarketsRecorder._shared_instance
        recorder._did_create_order(MarketEvent.BuyOrderCreated.value, connector, BuyOrderCreatedEvent(
            timestamp=int(first["uTime"]) / 1000, type=order.order_type, trading_pair=order.trading_pair,
            amount=order.amount, price=order.price, order_id=client_id, creation_timestamp=order.creation_timestamp,
            exchange_order_id=order.exchange_order_id))
        for raw in fills.values():
            fill = connector._trade_update_from_fill(order, raw)
            connector._order_tracker.process_trade_update(fill)
            recorder._did_fill_order(MarketEvent.OrderFilled.value, connector, OrderFilledEvent(
                timestamp=fill.fill_timestamp, order_id=client_id, trading_pair=order.trading_pair,
                trade_type=order.trade_type, order_type=order.order_type, price=fill.fill_price,
                amount=fill.fill_base_amount, trade_fee=fill.fee, exchange_trade_id=fill.trade_id,
                leverage=order.leverage, position=order.position.value))
        recorder.sql_manager.assert_persistence_healthy()
        self.projection.confirm_terminal(client_id, total)
        return True

    def status(self):
        self._mark_terminal_orders()
        value = self.projection.status() if self.projection is not None else dict(
            ready=False, recorded_net_outflow_quote=None, reserved_quote=ZERO, remaining_quote=None,
            non_usdt_fees={}, window_start_ms=None, window_end_ms=None, coverage_start_ms=None, fill_count=0)
        reason = self.reason_code or value.get("reason_code") or ("spot_outflow_reconciling" if self.terminal_pending else None)
        return {**value, "ready": value["ready"] and reason is None, "reason_code": reason,
                "binding": self.binding, "policy": self.policy, "last_buy_denial": self.last_buy_denial,
                "pending_terminal_orders": len(self.terminal_pending), "stale": self.projection is None,
                "observed_at": self.projection.now_ms / 1000 if self.projection is not None else None,
                "buy_allowed": value["ready"] and reason is None and (value["remaining_quote"] or ZERO) > 0}


def checkpoint_outflow_status(checkpoint):
    if not checkpoint:
        return None
    return dict(ready=False, stale=True, buy_allowed=False, reason_code="spot_outflow_not_running", observed_at=None,
                binding=checkpoint["binding"], policy=checkpoint["policy"], recorded_net_outflow_quote=None,
                reserved_quote=None, remaining_quote=None, non_usdt_fees={}, window_start_ms=None, window_end_ms=None,
                coverage_start_ms=checkpoint["coverage_start_ms"], fill_count=0, last_buy_denial=None, pending_terminal_orders=0)
