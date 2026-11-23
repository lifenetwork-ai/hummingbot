"""Owner adapter for spot cashflow; recorder rows remain the only fill ledger."""
import asyncio
import logging
from decimal import Decimal
from itertools import islice
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.data_type.in_flight_order import OrderState
from hummingbot.core.data_type.trade_fee import AddedToCostTradeFee
from hummingbot.core.event.events import BuyOrderCreatedEvent, MarketEvent
from hummingbot.core.order_submission_guard import SpotOutflowBlocked, SpotOutflowPending, SubmissionBlocked
from hummingbot.core.spot_outflow import (
    ADVANCE_BATCH,
    MAX_FILLS,
    ZERO,
    SpotOutflowConfig,
    SpotOutflowPolicy,
    SpotOutflowProjection,
)
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
        # Insertion order is retry order. Failed proofs rotate behind orders
        # that have not had a turn; repeated observations do not reset it.
        self.terminal_pending = {}
        self.terminal_stages = {}
        self.terminal_failures = {}
        self.initializing = False
        self.reason_code = "spot_outflow_initializing"
        self.last_buy_denial = None

    def identity(self):
        from hummingbot.connector.okx_account_observations import account_identity
        try:
            return dict(**account_identity(self.owner.connectors), trading_pair=self.policy['trading_pair'])
        except ValueError as error:
            raise ValueError('spot_outflow_' + str(error)) from error

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

    async def advance(self, now_ms):
        """Catch up on the owner loop; partial projections never authorize BUY."""
        projection, version = self.projection, self.owner.version
        if projection is not None and (not isinstance(now_ms, int) or now_ms < projection.now_ms):
            projection.advance(now_ms, max_work=ADVANCE_BATCH)
            return False
        target = projection.now_ms if projection is not None and projection.catching_up() else now_ms
        yielded = False
        while projection is not None and self.projection is projection:
            projection.advance(target, max_work=ADVANCE_BATCH)
            if projection.clock_failure or (not projection.catching_up() and target >= now_ms):
                break
            await asyncio.sleep(0)
            yielded = True
            row = self.owner.check_owner()
            if row['control_version'] != version or (row['recovery_state'] or {}).get('halt'):
                break
            # Finish the prior clock (and its queued arrivals) before moving
            # forward. Decimal rounding must not reorder arrivals and expiry.
            target = projection.now_ms if projection.catching_up() else max(now_ms, projection.now_ms)
        return yielded

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

    def binding_blocker(self, *, spot_order):
        if not self._identity_matches():
            return self.reason_code
        if spot_order and self.binding is None:
            # The first durable Spot intent would otherwise turn this account
            # into legacy history while the coverage baseline is still unset.
            return 'spot_outflow_initializing'
        return None

    def _require_bound_identity(self, *, spot_order):
        blocker = self.binding_blocker(spot_order=spot_order)
        if blocker == 'spot_outflow_initializing':
            raise SpotOutflowPending(blocker)
        if blocker:
            raise SubmissionBlocked(blocker)

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
        self._require_bound_identity(spot_order=connector.name == 'okx')
        if connector.name != "okx" or order.trade_type != TradeType.BUY:
            return
        now_ms = int(row['database_now'] * 1000)
        if self.projection is not None and (not self.projection.catching_up() or now_ms < self.projection.now_ms):
            self.projection.advance(now_ms, max_work=ADVANCE_BATCH)
        self._mark_terminal_orders()
        try:
            if order.trading_pair != self.policy["trading_pair"] or order.order_type != OrderType.LIMIT_MAKER:
                raise ValueError("spot_outflow_order_scope_unavailable")
            if self.projection is None or self.reason_code:
                raise ValueError(self.reason_code or "spot_outflow_initializing")
            if self.projection.catching_up():
                if self.projection.failure or self.projection.clock_failure:
                    raise ValueError(self.projection.reason())
                self.last_buy_denial = 'spot_outflow_projection_catching_up'
                raise SpotOutflowPending(self.last_buy_denial)
            if self.terminal_pending:
                self.last_buy_denial = 'spot_outflow_reconciling'
                raise SpotOutflowPending(self.last_buy_denial)
            flat = self._flat_fee(connector, order)
            amount, price = Decimal(order.amount), Decimal(order.price)
            if any(not value.is_finite() or value <= ZERO for value in (amount, price)):
                raise ValueError("spot_outflow_order_terms_unavailable")
            existing = self.projection.reservations.get(order.client_order_id)
            if existing is not None and (existing.amount, existing.price, existing.flat_fee) != (amount, price, flat):
                raise ValueError("spot_outflow_order_terms_changed")
            if not self.projection.reserve(order, flat):
                reason = self.projection.reason()
                if reason:
                    raise ValueError(reason)
                self.last_buy_denial = "spot_outflow_limit"
                raise SpotOutflowBlocked(self.last_buy_denial)
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
        self._require_bound_identity(spot_order=data.get('instId') == self.policy['trading_pair'])
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
        self.terminal_pending.pop(client_id, None)
        self.terminal_stages.pop(client_id, None)
        self.terminal_failures.pop(client_id, None)

    def mark_terminal_pending(self, client_id):
        self.terminal_pending.setdefault(client_id, None)

    def _mark_terminal_orders(self):
        for client_id, (_, order) in self.orders.items():
            if order.is_done:
                self.mark_terminal_pending(client_id)

    async def reconcile_terminal(self, *, all_orders=False, deferred=()):
        for client_id, (_, order) in self.orders.items():
            if order.is_done or all_orders:
                self.mark_terminal_pending(client_id)
        candidates = tuple(islice((identity for identity in self.terminal_pending if identity not in deferred),
                                  MAX_FILLS if all_orders else 4))
        for client_id in candidates:
            if client_id not in self.terminal_pending:
                continue  # An earlier awaited proof may have removed an unsent order.
            # Rotate before awaiting so failures and cancellation retain proof
            # obligations without monopolizing the next bounded polling batch.
            self.terminal_pending.pop(client_id)
            self.terminal_pending[client_id] = None
            try:
                if not self._confirm_committed_fill(client_id) and not await asyncio.wait_for(self._confirm(client_id), timeout=10):
                    self._terminal_failure(client_id, 'not_terminal')
                    continue
            except (ValueError, OSError, asyncio.TimeoutError, KeyError, ArithmeticError) as error:
                self._terminal_failure(client_id, type(error).__name__)
                continue  # Retain the reservation and explicit BUY-only wait.
            self._forget_terminal_order(client_id)
        if all_orders and set(self.terminal_pending) - set(deferred):
            raise SubmissionBlocked("spot_outflow terminal fill reconciliation is incomplete")

    def _confirm_committed_fill(self, client_id):
        from hummingbot.connector.markets_recorder import MarketsRecorder
        from hummingbot.core.spot_terminal_proof import committed_full_fill
        connector, order = self.orders[client_id]
        if not committed_full_fill(self.projection, connector, order):
            return False
        recorder = MarketsRecorder._shared_instance
        if (self.reason_code is not None or recorder is None or recorder.sql_manager.engine is not self.owner.engine
                or not self._identity_matches()):
            return False
        self.owner.check_owner()
        recorder.sql_manager.assert_persistence_healthy()
        self.projection.confirm_terminal(client_id, order.amount)
        return True

    async def refresh_canceled_order(self, connector, order):
        """One fresh strong proof can serve shutdown and this cash reservation.

        Only an exact retained Spot BUY with a venue ID is supported. A cancel
        ACK may still be queued locally; the fresh venue proof, not that local
        state, establishes termination. No proof crosses checkpoint phases.
        """
        retained = self.orders.get(order.client_order_id)
        if (connector.name != 'okx' or order.trade_type != TradeType.BUY or not order.exchange_order_id
                or order.current_state not in (OrderState.CANCELED, OrderState.PENDING_CANCEL,
                                               OrderState.OPEN, OrderState.PARTIALLY_FILLED)
                or retained is None or retained[0] is not connector or retained[1] is not order):
            return False
        self._check_terminal_identity(connector, order)
        self.mark_terminal_pending(order.client_order_id)
        if not await self._confirm(order.client_order_id, apply_terminal=True):
            return False  # Stale/nonterminal venue data retains the native polling path.
        self._forget_terminal_order(order.client_order_id)
        return True

    def _check_terminal_identity(self, connector, order):
        self.owner.check_owner()
        retained = self.orders.get(order.client_order_id, (None, None))
        if not self._identity_matches() or retained[0] is not connector or retained[1] is not order:
            raise SubmissionBlocked('spot_outflow terminal order/account identity changed')

    def _forget_terminal_order(self, client_id):
        # Projection reservations are retired only by confirm_terminal/advance,
        # including committed fills whose exchange timestamp is still future.
        self.terminal_pending.pop(client_id, None)
        self.orders.pop(client_id, None)
        self.terminal_stages.pop(client_id, None)
        self.terminal_failures.pop(client_id, None)

    def _terminal_failure(self, client_id, error_type):
        if client_id not in self.terminal_pending:
            self.terminal_stages.pop(client_id, None)
            self.terminal_failures.pop(client_id, None)
            return
        stage = self.terminal_stages.get(client_id, 'unavailable')
        failure = stage, error_type
        if self.terminal_failures.get(client_id) != failure:
            logging.getLogger(__name__).warning(
                'Spot terminal proof waiting order_id=%s stage=%s type=%s', client_id, stage, error_type,
                extra={'event': 'spot_terminal_proof_wait', 'reason_code': 'spot_outflow_terminal_' + stage,
                       'error_type': error_type, 'scope': 'spot_outflow'})
        self.terminal_failures[client_id] = failure

    def terminal_reason_code(self):
        """Return an actual proof failure, not merely unattempted bounded work."""
        for client_id in self.terminal_pending:
            failure = self.terminal_failures.get(client_id)
            if failure:
                return 'spot_outflow_terminal_' + failure[0]
        return None

    @staticmethod
    def _complete_first_page(response, amount):
        try:
            return (str(response['code']) == '0' and isinstance(response['data'], list)
                    and sum((Decimal(fill['fillSz']) for fill in response['data']), ZERO) == amount)
        except (ValueError, TypeError, KeyError, ArithmeticError):
            return False

    async def _confirm(self, client_id, *, apply_terminal=False):
        from hummingbot.connector.markets_recorder import MarketsRecorder
        connector, order = self.orders[client_id]
        self.terminal_stages[client_id] = 'order_read'
        # Shutdown already has a bound venue identity for the order. Fetch
        # the first page alongside status, use it only if it covers the final
        # cumulative amount, and still re-read status after checking fills.
        fetch = (asyncio.create_task(connector._request_order_fills(order, after=None, limit=100))
                 if apply_terminal else None)
        prefetched = None
        try:
            response = await connector._request_order_update(order)
            if str(response.get("code")) != "0" or len(response.get("data", [])) != 1:
                raise ValueError("Incomplete terminal order")
            first = response["data"][0]
            self.terminal_stages[client_id] = 'order_terminal'
            if first.get("state") not in ("filled", "canceled"):
                return False
            self.terminal_stages[client_id] = 'order_identity'
            if (first.get("clOrdId") != client_id or str(first.get("ordId")) != order.exchange_order_id
                    or first.get("instId") != order.trading_pair or first.get("side") != "buy"
                    or Decimal(first["sz"]) != order.amount or Decimal(first["px"]) != order.price):
                raise ValueError("Terminal order identity/terms mismatch")
            expected_amount = Decimal(first["accFillSz"])
            if fetch is not None:
                self.terminal_stages[client_id] = 'fills_read'
                try:
                    candidate = await fetch
                except (ValueError, OSError, KeyError, ArithmeticError):
                    pass  # The ordered path below makes a fresh request.
                else:
                    if self._complete_first_page(candidate, expected_amount):
                        prefetched = candidate
        finally:
            if fetch is not None:
                fetch.cancel()
                await asyncio.gather(fetch, return_exceptions=True)
        fills, after, cursors = {}, None, set()
        seen_amount = ZERO
        for page_index in range(100):
            self.terminal_stages[client_id] = 'fills_read'
            response = (prefetched if page_index == 0 and prefetched is not None
                        else await connector._request_order_fills(order, after=after, limit=100))
            if str(response.get("code")) != "0" or not isinstance(response.get("data"), list):
                raise ValueError("Incomplete terminal fills")
            page = response["data"]
            self.terminal_stages[client_id] = 'fill_identity'
            for fill in page:
                identity = str(fill["tradeId"])
                if identity in fills or str(fill["ordId"]) != order.exchange_order_id:
                    raise ValueError("Overlapping fill identities")
                size = Decimal(fill["fillSz"])
                if not size.is_finite() or size <= 0:
                    raise ValueError("Invalid fill quantity")
                seen_amount += size
                fills[identity] = fill
            if len(page) < 100 or seen_amount == expected_amount:
                break
            after = str(page[-1].get("billId") or "")
            if not after or after in cursors or page_index == 99:
                raise ValueError("Incomplete fill pagination")
            cursors.add(after)
        self.terminal_stages[client_id] = 'fill_coverage'
        total = sum((Decimal(fill["fillSz"]) for fill in fills.values()), ZERO)
        if total != expected_amount or (first["state"] == "filled" and total != order.amount):
            raise ValueError("Terminal cumulative fills are incomplete")
        self.terminal_stages[client_id] = 'order_recheck'
        final = await connector._request_order_update(order)
        keys = ("ordId", "clOrdId", "instId", "side", "sz", "px", "state", "accFillSz", "uTime")
        if (str(final.get("code")) != "0" or len(final.get("data", [])) != 1
                or any(final["data"][0].get(key) != first.get(key) for key in keys)):
            raise ValueError("Order changed during terminal proof")
        self.terminal_stages[client_id] = 'recording'
        recorder = MarketsRecorder._shared_instance
        async with recorder.native_order_write(connector):
            if apply_terminal:
                self._check_terminal_identity(connector, order)
            created = BuyOrderCreatedEvent(
                timestamp=int(first["uTime"]) / 1000, type=order.order_type, trading_pair=order.trading_pair,
                amount=order.amount, price=order.price, order_id=client_id, creation_timestamp=order.creation_timestamp,
                exchange_order_id=order.exchange_order_id,
            )
            cancelled = await recorder.persist_order_update(
                connector, [(MarketEvent.BuyOrderCreated, created)], dict(connector.tracking_states),
            )
            recorder.finish_recorded_order_creation(connector, created)
            if cancelled is not None:
                raise cancelled
            for raw in fills.values():
                fill = connector._trade_update_from_fill(order, raw)
                await connector._order_tracker.persist_proved_trade(recorder, order, fill)
            recorder.sql_manager.assert_persistence_healthy()
        if apply_terminal:
            # Keep native fill-before-terminal ordering. The converter is the
            # connector's normal polling converter; it performs no second GET.
            await connector._order_tracker.process_order_update(
                connector._order_update_from_data(order, final['data'][0]))
            self._check_terminal_identity(connector, order)
            if order.executed_amount_base != total:
                raise ValueError('Native terminal fill quantity does not match the proof')
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
                "pending_terminal_orders": len(self.terminal_pending),
                "stale": self.projection is None or self.projection.catching_up(),
                "observed_at": self.projection.now_ms / 1000 if self.projection is not None else None,
                "buy_allowed": value["ready"] and reason is None and (value["remaining_quote"] or ZERO) > 0}


def checkpoint_outflow_status(checkpoint):
    if checkpoint is None:
        return None
    policy = SpotOutflowPolicy.model_validate(checkpoint["policy"])
    binding, coverage = checkpoint["binding"], checkpoint["coverage_start_ms"]
    if (not isinstance(binding, dict) or set(binding) != {"account_id", "environment", "trading_pair"}
            or not isinstance(binding["account_id"], str) or not binding["account_id"].strip()
            or binding["environment"] not in ("live", "demo") or binding["trading_pair"] != policy.trading_pair
            or not isinstance(coverage, int) or isinstance(coverage, bool) or coverage < 0):
        raise ValueError("Invalid outflow checkpoint observation")
    return dict(ready=False, stale=True, buy_allowed=False, reason_code="spot_outflow_not_running", observed_at=None,
                binding=binding, policy=policy.model_dump(mode="json"), recorded_net_outflow_quote=None,
                reserved_quote=None, remaining_quote=None, non_usdt_fees={}, window_start_ms=None, window_end_ms=None,
                coverage_start_ms=coverage, fill_count=0, last_buy_denial=None, pending_terminal_orders=0)
