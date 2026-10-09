"""Conservative OKX spot cancellation and wire-ID reconciliation."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from inspect import isawaitable
from typing import Callable
from uuid import uuid4

from hummingbot.strategy_v2.life_liquidity.request_budget import AccountRequestBudget, RequestBudgetExceeded
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.session import OrderReconciliation
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


@dataclass(frozen=True)
class SpotFill:
    trade_id: str
    quantity_base: Decimal
    price_usdt: Decimal
    fee_currency: str | None = None
    signed_fee: Decimal | None = None
    fill_at_ms: int | None = None


@dataclass(frozen=True)
class CancelRetryPolicy:
    retry_interval_ms: int
    max_requests_per_cycle: int

    def __post_init__(self):
        if (not isinstance(self.retry_interval_ms, int) or isinstance(self.retry_interval_ms, bool)
                or self.retry_interval_ms <= 0
                or not isinstance(self.max_requests_per_cycle, int)
                or isinstance(self.max_requests_per_cycle, bool)
                or self.max_requests_per_cycle <= 0):
            raise ValueError("CANCEL_RETRY_POLICY_INVALID")


class CancelRequestError(IOError):
    def __init__(self, requests_used: int):
        super().__init__("CANCEL_ACK_UNAVAILABLE")
        self.requests_used = requests_used


class SpotReservationReconciler:
    """Apply observed fills to an existing reservation ledger by WAL identity.

    A restarted runner must restore/rebuild the ledger before using this
    adapter. Missing reservations fail closed; this adapter does not invent
    starting balances or erase old exposure.
    """

    def __init__(self, wal: IntentWAL, reservations: ReservationLedger,
                 *, require_fees: bool = False):
        self.wal = wal
        self.reservations = reservations
        self.require_fees = require_fees

    def request_cancel(self, wire_id: str) -> None:
        intent_id = self.wal.find_by_client_order_id(wire_id).intent_id
        self.reservations.request_cancel(intent_id)

    def mark_unknown(self, wire_id: str) -> None:
        intent_id = self.wal.find_by_client_order_id(wire_id).intent_id
        self.reservations.mark_unknown(intent_id)

    def apply_fills(self, wire_id: str, fills: tuple[SpotFill, ...],
                    cumulative: Decimal) -> bool:
        try:
            intent_id = self.wal.find_by_client_order_id(wire_id).intent_id
            self.reservations.apply_fills_snapshot(
                intent_id,
                tuple((fill.trade_id, fill.quantity_base, fill.price_usdt,
                       fill.fee_currency, fill.signed_fee) for fill in fills),
                cumulative, require_fees=self.require_fees)
            return True
        except (KeyError, TypeError, ValueError):
            return False

    def confirm_terminal(self, wire_id: str, state: str, cumulative: Decimal) -> bool:
        try:
            intent_id = self.wal.find_by_client_order_id(wire_id).intent_id
            self.reservations.confirm_terminal(
                intent_id, cumulative_filled=cumulative, fills_reconciled=True,
                exchange_state="CANCELED" if state == "canceled" else "FILLED")
            return True
        except (KeyError, ValueError):
            return False


class SpotAccountReconciler:
    """Require exact cash totals before terminal reservation release.

    Fees, deposits, or other account activity cause a mismatch until they are
    accounted for explicitly; a mismatch never grants additional capacity.
    """

    def __init__(self, connector, reservations: ReservationLedger, *, bills=None):
        self.connector = connector
        self.reservations = reservations
        self.bills = bills

    async def check(self) -> bool:
        try:
            if self.bills is not None and not await self.bills.reconcile():
                return False
            response = await self.connector.get_spot_cash_balances()
            if response.get("code") != "0" or len(response["data"]) != 1:
                return False
            details = response["data"][0]["details"]
            if not isinstance(details, list):
                return False
            balances = {}
            for item in details:
                currency = item["ccy"]
                if currency in balances:
                    return False
                if currency in ("LIFE", "USDT"):
                    amount = Decimal(item["cashBal"])
                    liability = Decimal(item.get("liab") or "0")
                    if (not amount.is_finite() or amount < 0
                            or not liability.is_finite() or liability != 0):
                        return False
                    balances[currency] = amount
            return (balances.get("LIFE") == self.reservations.life_balance
                    and balances.get("USDT") == self.reservations.usdt_balance)
        except (AttributeError, KeyError, TypeError, ValueError, InvalidOperation, TimeoutError, OSError):
            return False


class OkxSpotOrderGateway:
    MAX_PENDING_PAGES = 6
    MAX_HISTORY_PAGES = 6
    MAX_FILL_HISTORY_PAGES = 6
    # OKX requires ordType on this endpoint. conditional/oco may be queried
    # together; chase applies to derivatives and is outside the SPOT scope.
    SPOT_ALGO_TYPES = ("conditional,oco", "trigger", "move_order_stop",
                       "iceberg", "twap", "smart_iceberg")

    def __init__(self, connector, wal: IntentWAL, *, trading_pair: str,
                 clock: Callable[[], datetime],
                 apply_fills: Callable[[str, tuple[SpotFill, ...], Decimal], bool] | None = None,
                 confirm_terminal: Callable[[str, str, Decimal], bool] | None = None,
                 on_cancel_requested: Callable[[str], None] | None = None,
                 on_unknown: Callable[[str], None] | None = None,
                 account_check: Callable[[], bool] | None = None,
                 scope_check: Callable[[str, int, tuple[str, ...]], bool] | None = None,
                 runner_scope_check: Callable[[], bool] | None = None,
                 cancel_retry_policy: CancelRetryPolicy | None = None,
                 request_budget: AccountRequestBudget | None = None):
        if not trading_pair:
            raise ValueError("trading pair required")
        if cancel_retry_policy is not None and not isinstance(cancel_retry_policy, CancelRetryPolicy):
            raise ValueError("CANCEL_RETRY_POLICY_INVALID")
        if request_budget is not None and not isinstance(request_budget, AccountRequestBudget):
            raise ValueError("REQUEST_BUDGET_INVALID")
        self.connector = connector
        self.wal = wal
        self.trading_pair = trading_pair
        self.clock = clock
        self.apply_fills = apply_fills
        self.confirm_terminal = confirm_terminal
        self.on_cancel_requested = on_cancel_requested
        self.on_unknown = on_unknown
        self.account_check = account_check
        self.scope_check = scope_check
        self.runner_scope_check = runner_scope_check
        self.cancel_retry_policy = cancel_retry_policy
        self.request_budget = request_budget

    def _charge_request(self, kind: str) -> None:
        if self.request_budget is not None:
            self.request_budget.charge(kind, f"{kind.lower()}:{uuid4().hex}")

    async def request_cancel(self, session_id: str, epoch: int,
                             *, max_requests: int | None = None) -> int | None:
        if self.cancel_retry_policy is not None:
            return await self._request_cancel_bounded(
                self.wal.scoped_records(session_id, epoch), max_requests=max_requests)
        if max_requests is not None:
            raise ValueError("CANCEL_RETRY_POLICY_UNCONFIGURED")
        for record in self.wal.scoped_records(session_id, epoch):
            if (record.state in ("TERMINAL", "PREPARED", "ABORTED_BEFORE_SEND")
                    or record.exchange_terminal_observed):
                continue
            self._charge_request("CANCEL")
            # Persist the cancel intent before invoking an asynchronous connector.
            # An ACK only proves receipt of the request, not terminal state.
            self.wal.mark_cancel_requested(record.intent_id)
            if self.on_cancel_requested is not None:
                self.on_cancel_requested(record.client_order_id)
            if record.exchange_order_id is None:
                acknowledged = await self.connector.cancel_by_client_id(
                    self.trading_pair, record.client_order_id)
            else:
                acknowledged = await self.connector.cancel_by_exchange_order_id(
                    self.trading_pair, record.exchange_order_id)
            if acknowledged is not True:
                raise IOError("CANCEL_ACK_UNAVAILABLE")

    async def request_cancel_scopes(self, scopes: tuple[tuple[str, int], ...]) -> int:
        """Share one bounded request budget across all recovered session scopes."""
        if self.cancel_retry_policy is None:
            raise ValueError("CANCEL_RETRY_POLICY_UNCONFIGURED")
        scope_set = set(scopes)
        records = tuple(record for record in self.wal.all_records()
                        if (record.session_id, record.epoch) in scope_set)
        return await self._request_cancel_bounded(records)

    async def _retry_order_is_terminal(self, record) -> bool:
        try:
            if record.exchange_order_id is None:
                response = await self.connector.get_order_by_client_id(
                    self.trading_pair, record.client_order_id)
            else:
                response = await self.connector.get_order_by_exchange_order_id(
                    self.trading_pair, record.exchange_order_id)
            order = self._one_order(response, record.client_order_id)
            cumulative = Decimal(order["accFillSz"])
            if (not cumulative.is_finite() or cumulative < 0
                    or order["state"] == "filled" and cumulative == 0):
                return False
        except (AttributeError, KeyError, TypeError, ValueError, InvalidOperation,
                TimeoutError, OSError):
            return False
        if order["state"] in ("canceled", "filled"):
            # Identity or WAL failures must stop this cycle; they cannot be
            # converted into a new cancel against a conflicting exchange ID.
            self.wal.mark_exchange_terminal_observed(record.intent_id, str(order["ordId"]))
            return True
        return False

    async def _request_cancel_bounded(self, records: tuple,
                                      *, max_requests: int | None = None) -> int:
        policy = self.cancel_retry_policy
        if max_requests is not None and (not isinstance(max_requests, int)
                                         or isinstance(max_requests, bool) or max_requests < 0):
            raise ValueError("CANCEL_REQUEST_CAP_INVALID")
        limit = policy.max_requests_per_cycle if max_requests is None else min(
            policy.max_requests_per_cycle, max_requests)
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise ValueError("CANCEL_CLOCK_INVALID")
        pending = [record for record in records
                   if record.state not in ("TERMINAL", "PREPARED", "ABORTED_BEFORE_SEND")
                   and not record.exchange_terminal_observed]
        # A first cancellation is more urgent than retrying an acknowledged request.
        pending.sort(key=lambda record: (record.cancel_requested, record.cancel_attempts,
                                         record.intent_id))
        used = 0
        failed = False
        for record in pending:
            if used >= limit:
                break
            if record.last_cancel_attempt_at is not None:
                last = self.wal._cancel_time(record.last_cancel_attempt_at)
                if now < last:
                    raise ValueError("CANCEL_CLOCK_ROLLBACK")
                if now < last + timedelta(milliseconds=policy.retry_interval_ms):
                    continue
            if record.cancel_attempts > 0 and used + 2 <= limit:
                try:
                    self._charge_request("STATUS")
                except RequestBudgetExceeded:
                    pass  # Preserve the reserved capacity for cancellation.
                else:
                    used += 1  # Authenticated status consumes capacity too.
                    if await self._retry_order_is_terminal(record):
                        continue
            try:
                self._charge_request("CANCEL")
            except RequestBudgetExceeded:
                break
            # A crash between this WAL write and REST is safe: the next attempt
            # waits for the persisted retry deadline while risk stays reserved.
            self.wal.mark_cancel_attempt(record.intent_id, at=now)
            used += 1
            try:
                if self.on_cancel_requested is not None:
                    self.on_cancel_requested(record.client_order_id)
                if record.exchange_order_id is None:
                    acknowledged = await self.connector.cancel_by_client_id(
                        self.trading_pair, record.client_order_id)
                else:
                    acknowledged = await self.connector.cancel_by_exchange_order_id(
                        self.trading_pair, record.exchange_order_id)
                if acknowledged is not True:
                    failed = True
            except (KeyError, TypeError, ValueError, TimeoutError, OSError):
                failed = True
        if failed:
            raise CancelRequestError(used)
        return used

    async def _account_scope_complete(self, terminal_ids: set[str],
                                      observed_exchange_ids: dict[str, str]) -> bool:
        if not callable(getattr(self.connector, "get_all_open_spot_orders_page", None)):
            return False
        known = {record.client_order_id: record for record in self.wal.all_records()}
        seen_exchange_ids = set()
        cursor = None
        try:
            for _ in range(self.MAX_PENDING_PAGES):
                response = await self.connector.get_all_open_spot_orders_page(after=cursor)
                if response.get("code") != "0":
                    return False
                data = response["data"]
                if not isinstance(data, list) or len(data) > 100:
                    return False
                for item in data:
                    exchange_id = item["ordId"]
                    wire_id = item["clOrdId"]
                    expected_exchange_id = observed_exchange_ids.get(wire_id)
                    if expected_exchange_id is None and wire_id in known:
                        expected_exchange_id = known[wire_id].exchange_order_id
                    if (not isinstance(exchange_id, str) or not exchange_id
                            or exchange_id in seen_exchange_ids
                            or not isinstance(wire_id, str) or wire_id not in known
                            or expected_exchange_id != exchange_id
                            or wire_id in terminal_ids
                            or item["instType"] != "SPOT"
                            or item["instId"] != self.trading_pair
                            or item["state"] not in ("live", "partially_filled")):
                        return False
                    seen_exchange_ids.add(exchange_id)
                if len(data) < 100:
                    break
                cursor = data[-1]["ordId"]
            else:
                return False
        except (KeyError, TypeError, ValueError, TimeoutError, OSError):
            return False
        return await self._pending_algo_scope_complete()

    async def _pending_algo_scope_complete(self) -> bool:
        read_page = getattr(self.connector, "get_all_pending_spot_algo_orders_page", None)
        if not callable(read_page):
            return False
        try:
            for ord_type in self.SPOT_ALGO_TYPES:
                response = await read_page(ord_type)
                if not isinstance(response, dict) or response.get("code") != "0":
                    return False
                data = response.get("data")
                # This bot owns no algo orders. One row on the first page is
                # already enough to block; never cancel an unowned algo order.
                if not isinstance(data, list) or data:
                    return False
        except (AttributeError, KeyError, TypeError, ValueError, TimeoutError, OSError):
            return False
        return True

    @staticmethod
    def _one_order(response: dict, wire_id: str) -> dict:
        if response.get("code") != "0":
            raise ValueError("ORDER_STATUS_UNTRUSTED")
        data = response["data"]
        if (not isinstance(data, list) or len(data) != 1
                or data[0].get("clOrdId") != wire_id
                or not data[0].get("ordId")
                or data[0].get("state") not in ("live", "partially_filled", "canceled", "filled")):
            raise ValueError("ORDER_STATUS_UNTRUSTED")
        return data[0]

    async def _history_order(self, wire_id: str, exchange_order_id: str | None) -> dict:
        """Search a bounded complete history window; ambiguity stays unknown."""
        if not callable(getattr(self.connector, "get_spot_order_history_page", None)):
            raise ValueError("ORDER_HISTORY_UNAVAILABLE")
        cursor = None
        matches = []
        seen_ids = set()
        for _ in range(self.MAX_HISTORY_PAGES):
            response = await self.connector.get_spot_order_history_page(
                self.trading_pair, after=cursor)
            if (not isinstance(response, dict) or response.get("code") != "0"
                    or not isinstance(response.get("data"), list)):
                raise ValueError("ORDER_HISTORY_UNTRUSTED")
            data = response["data"]
            if len(data) > 100:
                raise ValueError("ORDER_HISTORY_UNTRUSTED")
            for item in data:
                order_id = str(item["ordId"])
                if not order_id or order_id in seen_ids or item["instId"] != self.trading_pair:
                    raise ValueError("ORDER_HISTORY_UNTRUSTED")
                seen_ids.add(order_id)
                if item["clOrdId"] == wire_id:
                    if exchange_order_id is None or order_id == exchange_order_id:
                        matches.append(item)
                    else:
                        raise ValueError("ORDER_HISTORY_ID_CONFLICT")
            if len(data) < 100:
                if len(matches) != 1:
                    raise ValueError("ORDER_HISTORY_AMBIGUOUS_OR_MISSING")
                order = self._one_order({"code": "0", "data": matches}, wire_id)
                if order["state"] not in ("canceled", "filled"):
                    raise ValueError("ORDER_HISTORY_NOT_TERMINAL")
                return order
            cursor = str(data[-1]["ordId"])
        raise ValueError("ORDER_HISTORY_INCOMPLETE")

    @staticmethod
    def _fills(response: dict, exchange_id: str, cumulative: Decimal) -> tuple[SpotFill, ...]:
        if response.get("code") != "0":
            raise ValueError("ORDER_FILLS_UNTRUSTED")
        data = response["data"]
        if not isinstance(data, list):
            raise ValueError("ORDER_FILLS_UNTRUSTED")
        fills = []
        seen = set()
        for raw in data:
            trade_id = str(raw["tradeId"])
            if (not trade_id or trade_id in seen or str(raw["ordId"]) != exchange_id):
                raise ValueError("ORDER_FILLS_UNTRUSTED")
            quantity = Decimal(raw["fillSz"])
            price = Decimal(raw["fillPx"])
            if (not quantity.is_finite() or quantity <= 0
                    or not price.is_finite() or price <= 0):
                raise ValueError("ORDER_FILLS_UNTRUSTED")
            seen.add(trade_id)
            fee_value = raw.get("fee")
            fee_currency = raw.get("feeCcy")
            if fee_value is None and fee_currency is None:
                fee = None
            elif (not isinstance(fee_currency, str) or not fee_currency
                  or fee_value is None):
                raise ValueError("ORDER_FEE_UNTRUSTED")
            else:
                fee = Decimal(fee_value)
                if not fee.is_finite():
                    raise ValueError("ORDER_FEE_UNTRUSTED")
            fill_time = raw.get("fillTime")
            if fill_time in (None, ""):
                fill_at_ms = None
            else:
                if not isinstance(fill_time, str) or not fill_time.isdecimal():
                    raise ValueError("ORDER_FILL_TIME_UNTRUSTED")
                fill_at_ms = int(fill_time)
                if fill_at_ms <= 0:
                    raise ValueError("ORDER_FILL_TIME_UNTRUSTED")
            fills.append(SpotFill(trade_id, quantity, price, fee_currency, fee, fill_at_ms))
        if sum((fill.quantity_base for fill in fills), Decimal("0")) != cumulative:
            raise ValueError("ORDER_FILL_CUMULATIVE_MISMATCH")
        return tuple(fills)

    async def _historical_fills(self, exchange_id: str,
                                cumulative: Decimal) -> tuple[SpotFill, ...]:
        if not callable(getattr(self.connector, "get_spot_fill_history_page", None)):
            raise ValueError("ORDER_FILL_HISTORY_UNAVAILABLE")
        cursor = None
        rows = []
        bill_ids = set()
        for _ in range(self.MAX_FILL_HISTORY_PAGES):
            response = await self.connector.get_spot_fill_history_page(
                self.trading_pair, exchange_id, after=cursor)
            if (not isinstance(response, dict) or response.get("code") != "0"
                    or not isinstance(response.get("data"), list)):
                raise ValueError("ORDER_FILL_HISTORY_UNTRUSTED")
            data = response["data"]
            if len(data) > 100:
                raise ValueError("ORDER_FILL_HISTORY_UNTRUSTED")
            for raw in data:
                bill_id = raw["billId"]
                if (not isinstance(bill_id, str) or not bill_id or bill_id in bill_ids
                        or raw["instId"] != self.trading_pair
                        or str(raw["ordId"]) != exchange_id):
                    raise ValueError("ORDER_FILL_HISTORY_UNTRUSTED")
                bill_ids.add(bill_id)
                rows.append(raw)
            if len(data) < 100:
                return self._fills({"code": "0", "data": rows}, exchange_id, cumulative)
            cursor = data[-1]["billId"]
        raise ValueError("ORDER_FILL_HISTORY_INCOMPLETE")

    async def reconcile(self, session_id: str, epoch: int) -> OrderReconciliation:
        open_ids = []
        pending_ids = []
        unknown_ids = []
        terminal_ids = set()
        observed_exchange_ids = {}
        terminal_candidates = []
        scope_complete = True
        fills_reconciled = True
        records = tuple(record for record in self.wal.scoped_records(session_id, epoch)
                        if record.state != "ABORTED_BEFORE_SEND")
        for record in records:
            wire_id = record.client_order_id
            if record.state == "PREPARED":
                unknown_ids.append(wire_id)
                scope_complete = False
                fills_reconciled = False
                continue
            try:
                try:
                    if record.exchange_order_id is None:
                        status = await self.connector.get_order_by_client_id(
                            self.trading_pair, wire_id)
                    else:
                        status = await self.connector.get_order_by_exchange_order_id(
                            self.trading_pair, record.exchange_order_id)
                    order = self._one_order(
                        status, wire_id)
                except (KeyError, TypeError, ValueError, TimeoutError, OSError):
                    order = await self._history_order(wire_id, record.exchange_order_id)
                if (record.exchange_order_id is not None
                        and record.exchange_order_id != str(order["ordId"])):
                    raise ValueError("ORDER_EXCHANGE_ID_MISMATCH")
                self.wal.acknowledge(record.intent_id, str(order["ordId"]))
                observed_exchange_ids[wire_id] = str(order["ordId"])
                cumulative = Decimal(order["accFillSz"])
                if not cumulative.is_finite() or cumulative < 0:
                    raise ValueError("ORDER_STATUS_UNTRUSTED")
                if order["state"] in ("partially_filled", "filled") and cumulative == 0:
                    raise ValueError("ORDER_STATUS_UNTRUSTED")
                exchange_id = str(order["ordId"])
                if order["state"] in ("canceled", "filled"):
                    # Terminal exchange status is enough to stop retrying cancel,
                    # but never enough to release the reservation by itself.
                    self.wal.mark_exchange_terminal_observed(record.intent_id, exchange_id)
                recent = await self.connector.get_fills_by_exchange_order_id(
                    self.trading_pair, exchange_id)
                try:
                    fills = self._fills(recent, exchange_id, cumulative)
                except ValueError as exc:
                    if str(exc) != "ORDER_FILL_CUMULATIVE_MISMATCH":
                        raise
                    fills = await self._historical_fills(exchange_id, cumulative)
                # Even an empty exchange snapshot must be compared with the
                # persisted ledger; an older zero cannot erase known fills.
                applied = (self.apply_fills(wire_id, fills, cumulative)
                           if self.apply_fills is not None else not fills)
                if not applied:
                    fills_reconciled = False
                state = order["state"]
                if state in ("live", "partially_filled"):
                    (pending_ids if record.cancel_requested else open_ids).append(wire_id)
                else:
                    terminal_ids.add(wire_id)
                    terminal_candidates.append((record.intent_id, wire_id, state,
                                                cumulative, str(order["ordId"]), applied))
            except (AttributeError, KeyError, TypeError, ValueError,
                    InvalidOperation, TimeoutError, OSError):
                if self.on_unknown is not None:
                    try:
                        self.on_unknown(wire_id)
                    except (KeyError, ValueError, OSError):
                        pass
                unknown_ids.append(wire_id)
                scope_complete = False
                fills_reconciled = False
        account_ok = True
        if self.account_check is not None:
            try:
                account_ok = self.account_check()
                if isawaitable(account_ok):
                    account_ok = await account_ok
                account_ok = account_ok is True
            except Exception:
                account_ok = False
            if not account_ok:
                scope_complete = False
                fills_reconciled = False
        wire_ids = tuple(record.client_order_id for record in records)
        try:
            if self.scope_check is None:
                scope_ok = await self._account_scope_complete(
                    terminal_ids, observed_exchange_ids)
            else:
                scope_ok = self.scope_check(session_id, epoch, wire_ids)
                if isawaitable(scope_ok):
                    scope_ok = await scope_ok
            if scope_ok is not True:
                scope_complete = False
        except Exception:
            scope_complete = False
        if self.runner_scope_check is not None:
            try:
                runner_scope_ok = self.runner_scope_check()
                if isawaitable(runner_scope_ok):
                    runner_scope_ok = await runner_scope_ok
                if runner_scope_ok is not True:
                    scope_complete = False
            except Exception:
                scope_complete = False
        for intent_id, wire_id, state, cumulative, exchange_id, applied in terminal_candidates:
            if (not applied or not scope_complete or self.confirm_terminal is None):
                fills_reconciled = False
                continue
            try:
                if self.confirm_terminal(wire_id, state, cumulative):
                    self.wal.mark_terminal(intent_id, exchange_id)
                else:
                    fills_reconciled = False
            except (KeyError, TypeError, ValueError, OSError):
                fills_reconciled = False
        return OrderReconciliation(
            session_id=session_id, epoch=epoch, observed_at=self.clock(),
            scope_complete=scope_complete, open_order_ids=tuple(open_ids),
            pending_cancel_ids=tuple(pending_ids), unknown_order_ids=tuple(unknown_ids),
            trade_events_reconciled=fills_reconciled)
