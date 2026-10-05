"""Fail-closed listing checks; instrument presence is not trading readiness."""

import asyncio
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from decimal import Decimal, Inexact, InvalidOperation, localcontext
from typing import Any


@dataclass(frozen=True)
class BenchmarkConnectorRoute:
    """Bind a benchmark pair to a connector without changing trading markets."""

    connector_name: str
    trading_pair: str
    quote_currency: str

    @classmethod
    def from_strategy(cls, strategy) -> "BenchmarkConnectorRoute | None":
        if strategy.reference.mode != "bounded_benchmark":
            return None
        source = strategy.reference.sources[0]
        return cls(source.connector, source.pair, source.quote_currency)

    @property
    def source_id(self) -> str:
        return f"{self.connector_name}:{self.trading_pair}"

    def resolve(self, market_data_provider):
        """Reuse an existing trading connector or its public-data fallback."""
        trading_connectors = market_data_provider.connectors
        if not isinstance(trading_connectors, dict):
            raise ValueError("BENCHMARK_CONNECTOR_UNAVAILABLE")
        registered = trading_connectors.get(self.connector_name)
        try:
            resolved = market_data_provider.get_connector_with_fallback(self.connector_name)
        except (AttributeError, KeyError, ValueError) as exc:
            raise ValueError("BENCHMARK_CONNECTOR_UNAVAILABLE") from exc
        if resolved is None:
            raise ValueError("BENCHMARK_CONNECTOR_UNAVAILABLE")
        if registered is not None and resolved is not registered:
            raise ValueError("BENCHMARK_CONNECTOR_COLLISION")
        return resolved


@dataclass(frozen=True)
class InstrumentRules:
    tick_size: Decimal
    lot_size: Decimal
    min_size: Decimal

    @classmethod
    def from_okx(cls, info: dict[str, Any]) -> "InstrumentRules":
        values = []
        for field in ("tickSz", "lotSz", "minSz"):
            raw = info.get(field)
            if not isinstance(raw, str) or not raw:
                raise ValueError(f"{field} must be a nonempty decimal string")
            try:
                value = Decimal(raw)
            except InvalidOperation as exc:
                raise ValueError(f"{field} must be decimal") from exc
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{field} must be finite and positive")
            values.append(value)
        return cls(*values)


class ContractValidationError(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LinearSwapContract:
    """LIFE settled in USDT; contract counts and LIFE amounts remain distinct."""

    instrument: str
    trading_pair: str
    contract_value_life: Decimal
    lot_size_contracts: Decimal
    min_size_contracts: Decimal
    tick_size_usdt: Decimal

    @classmethod
    def from_okx(cls, info: dict[str, Any], trading_pair: str) -> "LinearSwapContract":
        try:
            base, quote = trading_pair.split("-", 1)
        except (AttributeError, ValueError) as exc:
            raise ContractValidationError("SWAP_INSTRUMENT_INVALID") from exc
        if (not isinstance(info, dict) or trading_pair != "LIFE-USDT" or not base or not quote
                or info.get("instType") != "SWAP"
                or info.get("instId") != f"{trading_pair}-SWAP"):
            raise ContractValidationError("SWAP_INSTRUMENT_INVALID")
        if info.get("ctType") != "linear":
            raise ContractValidationError("SWAP_CONTRACT_TYPE_INVALID")
        if info.get("ctValCcy") != base or info.get("settleCcy") != quote:
            raise ContractValidationError("SWAP_CONTRACT_UNIT_INVALID")
        raw_value = info.get("ctVal")
        try:
            value = Decimal(raw_value) if isinstance(raw_value, str) else Decimal("NaN")
        except InvalidOperation:
            value = Decimal("NaN")
        if not value.is_finite() or value <= 0:
            raise ContractValidationError("SWAP_CONTRACT_VALUE_INVALID")
        # The OKX connector formats base amounts with ctVal alone. A different
        # multiplier requires a coordinated connector and ledger change.
        raw_multiplier = info.get("ctMult")
        try:
            multiplier = Decimal(raw_multiplier) if isinstance(raw_multiplier, str) else Decimal("NaN")
        except InvalidOperation:
            multiplier = Decimal("NaN")
        if not multiplier.is_finite() or multiplier != 1:
            raise ContractValidationError("SWAP_CONTRACT_MULTIPLIER_UNSUPPORTED")
        try:
            rules = InstrumentRules.from_okx(info)
        except ValueError as exc:
            raise ContractValidationError("SWAP_ORDER_RULES_INVALID") from exc
        return cls(info["instId"], trading_pair, value, rules.lot_size,
                   rules.min_size, rules.tick_size)

    @staticmethod
    def _quantity(value: Decimal) -> Decimal:
        if not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError("quantity must be a finite Decimal")
        return value

    @staticmethod
    def _exact_multiply(left: Decimal, right: Decimal) -> Decimal:
        with localcontext() as context:
            context.prec = max(28, len(left.as_tuple().digits) + len(right.as_tuple().digits) + 2)
            return left * right

    def contracts_to_life(self, contracts: Decimal) -> Decimal:
        return self._exact_multiply(self._quantity(contracts), self.contract_value_life)

    def life_to_contracts(self, life_amount: Decimal) -> Decimal:
        with localcontext() as context:
            context.prec = max(28, len(self._quantity(life_amount).as_tuple().digits)
                               + len(self.contract_value_life.as_tuple().digits) + 2)
            context.traps[Inexact] = True
            try:
                return life_amount / self.contract_value_life
            except Inexact as exc:
                raise ValueError("LIFE amount has no exact decimal contract quantity") from exc

    @property
    def minimum_order_life(self) -> Decimal:
        return self.contracts_to_life(self.min_size_contracts)

    @property
    def order_step_life(self) -> Decimal:
        return self.contracts_to_life(self.lot_size_contracts)

    def valid_order_contracts(self, contracts: Decimal) -> bool:
        return (isinstance(contracts, Decimal) and contracts.is_finite()
                and contracts >= self.min_size_contracts
                and contracts % self.lot_size_contracts == 0)


def is_order_book_ready(connector, trading_pair: str) -> bool:
    """Check connector and both top levels; SnapshotQualityGate checks freshness."""
    try:
        if not connector.ready:
            return False
        order_book = connector.get_order_book(trading_pair)
        bid = order_book.get_price(is_buy=False)
        ask = order_book.get_price(is_buy=True)
        return (math.isfinite(bid) and math.isfinite(ask)
                and bid > 0 and ask > bid)
    except (AttributeError, KeyError, OSError, TypeError, ValueError, OverflowError):
        return False


def _positive_milliseconds(value: Any) -> int | None:
    if not isinstance(value, str) or len(value) > 20 or re.fullmatch(r"[0-9]+", value) is None:
        return None
    number = int(value)
    return number if number > 0 else None


class ContinuousTradingGate:
    """Confirm a spot listing phase using OKX metadata and OKX server time."""

    def __init__(self):
        self.ready = False
        self.reason_code = "CONTINUOUS_TRADING_UNCHECKED"
        self.exchange_time_ms: int | None = None
        self.continuous_start_ms: int | None = None

    def reset(self, reason_code: str = "CONTINUOUS_TRADING_UNCHECKED") -> None:
        self.ready = False
        self.reason_code = reason_code
        self.exchange_time_ms = None
        self.continuous_start_ms = None

    def evaluate(self, instrument: dict[str, Any], time_response: Any) -> bool:
        self.reset()
        if not isinstance(instrument, dict) or instrument.get("state") != "live":
            self.reason_code = "INSTRUMENT_NOT_LIVE"
            return False

        if (not isinstance(time_response, dict) or time_response.get("code") != "0"
                or not isinstance(time_response.get("data"), list) or len(time_response["data"]) != 1
                or not isinstance(time_response["data"][0], dict)):
            self.reason_code = "EXCHANGE_TIME_INVALID"
            return False
        self.exchange_time_ms = _positive_milliseconds(time_response["data"][0].get("ts"))
        if self.exchange_time_ms is None:
            self.reason_code = "EXCHANGE_TIME_INVALID"
            return False

        open_type = instrument.get("openType")
        if open_type not in ("", "fix_price", "pre_quote", "call_auction"):
            self.reason_code = "OPEN_TYPE_UNKNOWN"
            return False
        list_time = _positive_milliseconds(instrument.get("listTime"))
        if list_time is None:
            self.reason_code = "LIST_TIME_INVALID"
            return False

        switch_raw = instrument.get("contTdSwTime")
        if open_type in ("pre_quote", "call_auction") and not switch_raw:
            self.reason_code = "CONTINUOUS_START_UNKNOWN"
            return False
        if not isinstance(switch_raw, str):
            self.reason_code = "CONTINUOUS_START_INVALID"
            return False
        switch_time = _positive_milliseconds(switch_raw) if switch_raw else None
        if switch_raw and (switch_time is None or switch_time < list_time):
            self.reason_code = "CONTINUOUS_START_INVALID"
            return False

        self.continuous_start_ms = switch_time if switch_time is not None else list_time
        if self.exchange_time_ms < self.continuous_start_ms:
            self.reason_code = "CONTINUOUS_TRADING_NOT_STARTED"
            return False
        self.ready = True
        self.reason_code = "CONTINUOUS_TRADING_CONFIRMED"
        return True


class SnapshotValidationError(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _positive_book_decimal(raw: Any, reason_code: str) -> Decimal:
    if not isinstance(raw, str) or not raw:
        raise SnapshotValidationError(reason_code)
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise SnapshotValidationError(reason_code) from exc
    if not value.is_finite() or value <= 0:
        raise SnapshotValidationError(reason_code)
    return value


def _book_levels(raw: Any, descending: bool) -> tuple[tuple[Decimal, Decimal], ...]:
    if not isinstance(raw, list) or not raw:
        raise SnapshotValidationError("BOOK_RESPONSE_INVALID")
    parsed = []
    for row in raw:
        if not isinstance(row, list) or len(row) < 2:
            raise SnapshotValidationError("BOOK_RESPONSE_INVALID")
        price = _positive_book_decimal(row[0], "BOOK_PRICE_INVALID")
        size = _positive_book_decimal(row[1], "BOOK_DEPTH_INVALID")
        if parsed and ((descending and price >= parsed[-1][0]) or (not descending and price <= parsed[-1][0])):
            raise SnapshotValidationError("BOOK_LEVELS_UNSORTED")
        parsed.append((price, size))
    return tuple(parsed)


@dataclass(frozen=True)
class MarketSnapshot:
    instrument: str
    exchange_timestamp_ms: int
    received_monotonic: float
    bid: Decimal
    ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    bid_depth: tuple[tuple[Decimal, Decimal], ...]
    ask_depth: tuple[tuple[Decimal, Decimal], ...]
    market_state: str
    data_source: str
    sequence_id: int | None
    observed_age_ms: int

    @classmethod
    def from_okx(cls, response: Any, instrument: str, received_monotonic: float,
                 market_state: str, observed_age_ms: int) -> "MarketSnapshot":
        if (not isinstance(response, dict) or response.get("code") != "0"
                or not isinstance(response.get("data"), list) or len(response["data"]) != 1
                or not isinstance(response["data"][0], dict)):
            raise SnapshotValidationError("BOOK_RESPONSE_INVALID")
        data = response["data"][0]
        timestamp = _positive_milliseconds(data.get("ts"))
        if timestamp is None:
            raise SnapshotValidationError("BOOK_RESPONSE_INVALID")
        bids = _book_levels(data.get("bids"), descending=True)
        asks = _book_levels(data.get("asks"), descending=False)
        bid, bid_size = bids[0]
        ask, ask_size = asks[0]
        if bid >= ask:
            raise SnapshotValidationError("BOOK_CROSSED")
        sequence_id = data.get("seqId")
        if isinstance(sequence_id, bool) or not isinstance(sequence_id, int) or sequence_id < 0:
            sequence_id = None
        return cls(
            instrument=instrument, exchange_timestamp_ms=timestamp,
            received_monotonic=received_monotonic, bid=bid, ask=ask,
            bid_size=bid_size, ask_size=ask_size, bid_depth=bids, ask_depth=asks,
            market_state=market_state,
            data_source="okx_rest_books", sequence_id=sequence_id,
            observed_age_ms=observed_age_ms,
        )


class SnapshotQualityGate:
    """Reject bad REST snapshots and expire permits using monotonic elapsed time."""

    def __init__(self, instrument: str, max_age_ms: int, clock: Callable[[], float] = time.monotonic):
        if not instrument or isinstance(max_age_ms, bool) or not isinstance(max_age_ms, int) or max_age_ms <= 0:
            raise ValueError("instrument and positive max_age_ms are required")
        self.instrument = instrument
        self.max_age_ms = max_age_ms
        self.clock = clock
        self.ready = False
        self.reason_code = "BOOK_UNCHECKED"
        self.snapshot: MarketSnapshot | None = None

    def reset(self, reason_code: str = "BOOK_UNCHECKED") -> None:
        self.ready = False
        self.reason_code = reason_code

    def evaluate(self, response: Any, *, exchange_now_ms: int, received_monotonic: float,
                 market_state: str, continuous_trading_ready: bool) -> bool:
        self.reset()
        if not continuous_trading_ready or market_state != "live":
            self.reason_code = "CONTINUOUS_TRADING_NOT_CONFIRMED"
            return False
        if (isinstance(exchange_now_ms, bool) or not isinstance(exchange_now_ms, int)
                or exchange_now_ms <= 0):
            self.reason_code = "EXCHANGE_TIME_INVALID"
            return False
        if (not isinstance(received_monotonic, (int, float))
                or not math.isfinite(received_monotonic) or received_monotonic < 0):
            self.reason_code = "RECEIVE_TIME_INVALID"
            return False
        try:
            candidate = MarketSnapshot.from_okx(
                response, self.instrument, received_monotonic, market_state,
                observed_age_ms=0,
            )
        except SnapshotValidationError as exc:
            self.reason_code = exc.reason_code
            return False

        previous = self.snapshot
        if previous is not None and received_monotonic < previous.received_monotonic:
            self.reason_code = "RECEIVE_TIME_OUT_OF_ORDER"
            return False
        if previous is not None and candidate.exchange_timestamp_ms < previous.exchange_timestamp_ms:
            self.reason_code = "BOOK_OUT_OF_ORDER"
            return False
        if previous is not None and candidate.exchange_timestamp_ms == previous.exchange_timestamp_ms and (
                candidate.bid_depth, candidate.ask_depth
        ) != (previous.bid_depth, previous.ask_depth):
            self.reason_code = "BOOK_TIMESTAMP_CONFLICT"
            return False
        if candidate.exchange_timestamp_ms > exchange_now_ms:
            self.reason_code = "BOOK_TIMESTAMP_FUTURE"
            return False
        age_ms = exchange_now_ms - candidate.exchange_timestamp_ms
        if age_ms > self.max_age_ms:
            self.reason_code = "BOOK_STALE"
            return False

        self.snapshot = replace(candidate, observed_age_ms=age_ms)
        self.ready = True
        self.reason_code = "BOOK_QUALITY_CONFIRMED"
        return True

    def permit(self, now: float | None = None) -> bool:
        if not self.ready or self.snapshot is None:
            return False
        now = self.clock() if now is None else now
        return (isinstance(now, (int, float)) and math.isfinite(now)
                and now >= self.snapshot.received_monotonic
                and self.snapshot.observed_age_ms
                + (now - self.snapshot.received_monotonic) * 1000 <= self.max_age_ms)


class BookContinuityGate:
    """Require a new REST observation after each synchronized WS snapshot."""

    def __init__(self, max_silence_seconds: float = 65):
        if not math.isfinite(max_silence_seconds) or max_silence_seconds <= 0:
            raise ValueError("max_silence_seconds must be positive and finite")
        self.max_silence_seconds = max_silence_seconds
        self.confirmed_epoch: int | None = None
        self.reason_code = "BOOK_RESYNC_REQUIRED"

    def reset(self):
        self.confirmed_epoch = None
        self.reason_code = "BOOK_RESYNC_REQUIRED"

    def confirm(self, health, snapshot: MarketSnapshot | None,
                request_started_monotonic: float | None = None) -> bool:
        self.reset()
        if health is None:
            self.reason_code = "BOOK_FEED_UNAVAILABLE"
        elif not health.connected:
            self.reason_code = "BOOK_FEED_DISCONNECTED"
        elif not health.synchronized:
            self.reason_code = health.reason_code
        elif (snapshot is None or snapshot.sequence_id is None
              or health.snapshot_received_monotonic is None
              or request_started_monotonic is None
              or request_started_monotonic < health.snapshot_received_monotonic
              or snapshot.received_monotonic < health.snapshot_received_monotonic
              or snapshot.exchange_timestamp_ms < health.snapshot_exchange_timestamp_ms
              or (snapshot.exchange_timestamp_ms == health.snapshot_exchange_timestamp_ms
                  and health.snapshot_sequence_id is not None
                  and snapshot.sequence_id < health.snapshot_sequence_id)):
            self.reason_code = "BOOK_SNAPSHOT_BEFORE_RESYNC"
        else:
            self.confirmed_epoch = health.epoch
            self.reason_code = "BOOK_CONTINUITY_CONFIRMED"
            return True
        return False

    def permit(self, health, snapshot_gate: SnapshotQualityGate, now: float | None = None) -> bool:
        now = snapshot_gate.clock() if now is None else now
        if (health is None or not health.connected or not health.synchronized
                or self.confirmed_epoch != health.epoch or health.last_message_monotonic is None
                or not isinstance(now, (int, float)) or not math.isfinite(now)
                or now < health.last_message_monotonic
                or now - health.last_message_monotonic > self.max_silence_seconds):
            return False
        return snapshot_gate.permit(now)


class ListingGate:
    """Poll public instrument metadata on a monotonic, bounded retry schedule."""

    def __init__(self, inst_type: str, inst_id: str, base_delay: float = 5,
                 max_delay: float = 300, clock: Callable[[], float] = time.monotonic):
        if not inst_type or not inst_id:
            raise ValueError("instrument type and ID are required")
        if not all(math.isfinite(value) and value > 0 for value in (base_delay, max_delay)):
            raise ValueError("retry delays must be finite and positive")
        if max_delay < base_delay:
            raise ValueError("maximum retry delay must be at least the base delay")
        self.inst_type = inst_type
        self.inst_id = inst_id
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.clock = clock
        self.state = "WAITING_READY"
        self.instrument_found = False
        self.instrument_rules: InstrumentRules | None = None
        self.instrument_state: str | None = None
        self.instrument_info: dict[str, Any] | None = None
        self.reason_code = "INSTRUMENT_UNCHECKED"
        self.next_refresh_at = 0.0
        self._failures = 0

    @property
    def metadata_ready(self) -> bool:
        return self.instrument_found and self.instrument_rules is not None and self.instrument_state == "live"

    async def poll(self, now: float, fetch: Callable[[], Awaitable[dict[str, Any]]]) -> bool:
        if not isinstance(now, (int, float)) or not math.isfinite(now) or now < 0:
            raise ValueError("monotonic clock must be finite and nonnegative")
        if now < self.next_refresh_at:
            return self.instrument_found

        # Revoke any previous observation before awaiting external data. A delayed
        # or failed refresh cannot leave a stale positive listing permit behind.
        self.instrument_found = False
        self.instrument_rules = None
        self.instrument_state = None
        self.instrument_info = None
        try:
            response = await fetch()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.reason_code = "INSTRUMENT_FETCH_ERROR"
        else:
            if (not isinstance(response, dict) or response.get("code") != "0"
                    or not isinstance(response.get("data"), list)
                    or any(not isinstance(item, dict) for item in response["data"])):
                self.reason_code = "INSTRUMENT_RESPONSE_INVALID"
            else:
                matches = [
                    item for item in response["data"] if
                    item.get("instType") == self.inst_type and item.get("instId") == self.inst_id
                ]
                self.instrument_found = len(matches) > 0
                if len(matches) > 1:
                    self.reason_code = "INSTRUMENT_RESPONSE_INVALID"
                elif not matches:
                    self.reason_code = "INSTRUMENT_NOT_FOUND"
                else:
                    info = matches[0]
                    self.instrument_state = info.get("state")
                    self.instrument_info = dict(info)
                    try:
                        self.instrument_rules = InstrumentRules.from_okx(info)
                    except ValueError:
                        self.reason_code = "INSTRUMENT_RULES_INVALID"
                    else:
                        self.reason_code = ("INSTRUMENT_FOUND_PENDING_DATA_GATES" if self.instrument_state == "live"
                                            else "INSTRUMENT_NOT_LIVE")

        if self.instrument_found:
            self._failures = 0
            delay = self.base_delay
        else:
            self._failures += 1
            delay = min(self.max_delay, self.base_delay * 2 ** min(self._failures - 1, 32))
        self.next_refresh_at = now + delay
        return self.instrument_found


class OkxInstrumentSource:
    """Use Hummingbot's OKX connector and its shared REST throttler."""

    def __init__(self, market_data_provider, connector_name: str, inst_type: str):
        self.market_data_provider = market_data_provider
        self.connector_name = connector_name
        self.inst_type = inst_type

    async def fetch(self) -> dict[str, Any]:
        connector = self.market_data_provider.get_connector_with_fallback(self.connector_name)
        return await connector._api_get(
            path_url=connector.trading_pairs_request_path,
            params={"instType": self.inst_type},
        )

    async def fetch_server_time(self) -> dict[str, Any]:
        connector = self.market_data_provider.get_connector_with_fallback(self.connector_name)
        return await connector._api_get(path_url=connector.check_network_request_path)

    async def fetch_order_book(self, instrument: str) -> dict[str, Any]:
        from hummingbot.connector.exchange.okx.okx_constants import OKX_ORDER_BOOK_PATH

        connector = self.market_data_provider.get_connector_with_fallback(self.connector_name)
        return await connector._api_get(
            path_url=OKX_ORDER_BOOK_PATH,
            params={"instId": instrument, "sz": "5"},
        )
