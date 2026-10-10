"""Account-specific OKX fee observations and currency-safe fill reconciliation.

Fee rates are dimensionless. OKX does not state the charged currency in the
trade-fee response, so only actual fills can establish a fee currency amount.
"""

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from hummingbot.strategy_v2.life_liquidity.spot_quotes import QuoteCosts

FEE_RATES_PATH = "/api/v5/account/trade-fee"


class FeeDataError(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _decimal(value: Any, reason_code: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise FeeDataError(reason_code)
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise FeeDataError(reason_code) from exc
    if not amount.is_finite():
        raise FeeDataError(reason_code)
    return amount


def _positive_milliseconds(value: Any) -> int:
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,20}", value) is None:
        raise FeeDataError("FEE_TIMESTAMP_INVALID")
    timestamp = int(value)
    if timestamp <= 0:
        raise FeeDataError("FEE_TIMESTAMP_INVALID")
    return timestamp


def _currency(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[A-Z0-9]{2,20}", value) is None:
        raise FeeDataError("FEE_CURRENCY_INVALID")
    return value


def _instrument(instrument: Any) -> tuple[str, str, str]:
    if not isinstance(instrument, dict):
        raise FeeDataError("FEE_INSTRUMENT_MISMATCH")
    inst_type, inst_id, group_id = (instrument.get(key) for key in ("instType", "instId", "groupId"))
    if inst_type not in ("SPOT", "SWAP") or not isinstance(inst_id, str) or not inst_id:
        raise FeeDataError("FEE_INSTRUMENT_MISMATCH")
    if (inst_type == "SWAP" and not inst_id.endswith("-SWAP")
            or inst_type == "SPOT" and inst_id.endswith("-SWAP")):
        raise FeeDataError("FEE_INSTRUMENT_MISMATCH")
    pair = inst_id.removesuffix("-SWAP") if inst_type == "SWAP" else inst_id
    currencies = pair.split("-")
    if len(currencies) != 2 or not all(currencies):
        raise FeeDataError("FEE_INSTRUMENT_MISMATCH")
    if not isinstance(group_id, str) or not group_id:
        raise FeeDataError("FEE_GROUP_MISSING")
    return inst_type, inst_id, group_id


@dataclass(frozen=True)
class FeeRateSnapshot:
    account_id: str
    connector_name: str
    inst_type: str
    instrument_id: str
    group_id: str
    notional_currency: str
    fee_currency: None
    exchange_timestamp_ms: int
    maker_cost_rate: Decimal
    taker_cost_rate: Decimal

    @classmethod
    def from_okx(cls, response: Any, *, account_id: str, connector_name: str,
                 instrument: dict[str, Any], notional_currency: str) -> "FeeRateSnapshot":
        if not isinstance(account_id, str) or not account_id.strip():
            raise FeeDataError("FEE_ACCOUNT_MISSING")
        if not isinstance(connector_name, str) or not connector_name:
            raise FeeDataError("FEE_CONNECTOR_UNAVAILABLE")
        inst_type, inst_id, group_id = _instrument(instrument)
        _currency(notional_currency)
        pair = inst_id.removesuffix("-SWAP") if inst_type == "SWAP" else inst_id
        if notional_currency != pair.split("-")[1]:
            raise FeeDataError("FEE_CURRENCY_MISMATCH")
        if (not isinstance(response, dict) or response.get("code") != "0"
                or not isinstance(response.get("data"), list) or len(response["data"]) != 1
                or not isinstance(response["data"][0], dict)):
            raise FeeDataError("FEE_RESPONSE_INVALID")
        row = response["data"][0]
        if row.get("instType") != inst_type:
            raise FeeDataError("FEE_INSTRUMENT_MISMATCH")
        fee_groups = row.get("feeGroup")
        if not isinstance(fee_groups, list) or not fee_groups:
            raise FeeDataError("FEE_GROUP_MISSING")
        if any(not isinstance(group, dict) for group in fee_groups):
            raise FeeDataError("FEE_RESPONSE_INVALID")
        matches = [group for group in fee_groups if group.get("groupId") == group_id]
        if len(matches) != 1:
            raise FeeDataError("FEE_GROUP_MISMATCH")
        maker = _decimal(matches[0].get("maker"), "FEE_RATE_INVALID")
        taker = _decimal(matches[0].get("taker"), "FEE_RATE_INVALID")
        return cls(account_id=account_id, connector_name=connector_name, inst_type=inst_type,
                   instrument_id=inst_id, group_id=group_id, notional_currency=notional_currency,
                   fee_currency=None, exchange_timestamp_ms=_positive_milliseconds(row.get("ts")),
                   maker_cost_rate=-maker, taker_cost_rate=-taker)

    def is_fresh(self, *, exchange_now_ms: int, max_age_ms: int) -> bool:
        if (not isinstance(exchange_now_ms, int) or isinstance(exchange_now_ms, bool)
                or not isinstance(max_age_ms, int) or isinstance(max_age_ms, bool) or max_age_ms <= 0):
            return False
        age = exchange_now_ms - self.exchange_timestamp_ms
        return 0 <= age <= max_age_ms

    def conservative_cost_rate(self, liquidity: str) -> Decimal:
        if liquidity == "maker":
            return max(self.maker_cost_rate, Decimal("0"))
        if liquidity == "taker":
            return max(self.taker_cost_rate, Decimal("0"))
        raise FeeDataError("FEE_LIQUIDITY_INVALID")


@dataclass(frozen=True)
class FeeQuoteDecision:
    allowed: bool
    reason_code: str
    required_maker_rate: Decimal | None
    required_exit_rate: Decimal | None


class FeeQuoteBinding:
    """Opt-in, account-bound fee check repeated at planning and final send."""

    def __init__(self, *, snapshot: Callable[[], FeeRateSnapshot | None],
                 exchange_now_ms: Callable[[], int], account_id: str,
                 connector_name: str, instrument_id: str, group_id: str,
                 max_age_ms: int, fee_policy: str,
                 fee_ceiling_bps: Decimal | None = None):
        if (not callable(snapshot) or not callable(exchange_now_ms)
                or any(not isinstance(value, str) or not value for value in (
                    account_id, connector_name, instrument_id, group_id))
                or not isinstance(max_age_ms, int) or isinstance(max_age_ms, bool)
                or max_age_ms <= 0 or fee_policy not in ("pause", "conservative_ceiling")
                or fee_policy == "pause" and fee_ceiling_bps is not None
                or fee_policy == "conservative_ceiling" and (
                    not isinstance(fee_ceiling_bps, Decimal)
                    or not fee_ceiling_bps.is_finite() or fee_ceiling_bps <= 0)):
            raise FeeDataError("FEE_QUOTE_POLICY_INVALID")
        self.snapshot = snapshot
        self.exchange_now_ms = exchange_now_ms
        self.account_id = account_id
        self.connector_name = connector_name
        self.instrument_id = instrument_id
        self.group_id = group_id
        self.max_age_ms = max_age_ms
        self.fee_policy = fee_policy
        self.fee_ceiling_bps = fee_ceiling_bps

    @staticmethod
    def _deny(reason: str) -> FeeQuoteDecision:
        return FeeQuoteDecision(False, reason, None, None)

    def evaluate(self, costs: QuoteCosts) -> FeeQuoteDecision:
        if (not isinstance(costs, QuoteCosts)
                or any(not isinstance(rate, Decimal) or not rate.is_finite() or rate < 0
                       for rate in (costs.maker_fee_rate, costs.exit_fee_rate))):
            return self._deny("FEE_COST_INPUT_INVALID")
        try:
            now = self.exchange_now_ms()
        except Exception:
            return self._deny("FEE_CLOCK_INVALID")
        if not isinstance(now, int) or isinstance(now, bool) or now <= 0:
            return self._deny("FEE_CLOCK_INVALID")
        try:
            observed = self.snapshot()
        except Exception:
            observed = None
        if observed is not None:
            if not isinstance(observed, FeeRateSnapshot):
                return self._deny("FEE_SNAPSHOT_INVALID")
            if (observed.account_id != self.account_id
                    or observed.connector_name != self.connector_name
                    or observed.inst_type != "SPOT"
                    or observed.instrument_id != self.instrument_id
                    or observed.group_id != self.group_id
                    or observed.notional_currency != "USDT"
                    or observed.fee_currency is not None):
                return self._deny("FEE_IDENTITY_MISMATCH")
            if any(not isinstance(rate, Decimal) or not rate.is_finite()
                   for rate in (observed.maker_cost_rate, observed.taker_cost_rate)):
                return self._deny("FEE_SNAPSHOT_INVALID")
            if (not isinstance(observed.exchange_timestamp_ms, int)
                    or isinstance(observed.exchange_timestamp_ms, bool)
                    or observed.exchange_timestamp_ms <= 0):
                return self._deny("FEE_SNAPSHOT_INVALID")
            if observed.is_fresh(exchange_now_ms=now, max_age_ms=self.max_age_ms):
                maker = observed.conservative_cost_rate("maker")
                exit_rate = observed.conservative_cost_rate("taker")
                if (self.fee_ceiling_bps is not None
                        and max(maker, exit_rate) * Decimal("10000") > self.fee_ceiling_bps):
                    return self._deny("FEE_CEILING_BREACHED")
                reason = "FEE_ACCOUNT_RATE_READY"
            else:
                observed = None
                reason = "FEE_RATE_STALE"
        else:
            reason = "FEE_RATE_UNAVAILABLE"
        if observed is None:
            if self.fee_policy != "conservative_ceiling":
                return self._deny(reason)
            maker = exit_rate = self.fee_ceiling_bps / Decimal("10000")
            reason = "FEE_CEILING_FALLBACK"
        if costs.maker_fee_rate < maker or costs.exit_fee_rate < exit_rate:
            return self._deny("FEE_COST_UNDERSTATED")
        return FeeQuoteDecision(True, reason, maker, exit_rate)


class OkxFeeRateSource:
    """Read account rates through the registered authenticated connector only."""

    def __init__(self, market_data_provider, connector_name: str, account_id: str,
                 instrument: dict[str, Any], notional_currency: str):
        if not isinstance(account_id, str) or not account_id.strip():
            raise FeeDataError("FEE_ACCOUNT_MISSING")
        inst_type, inst_id, _ = _instrument(instrument)
        _currency(notional_currency)
        pair = inst_id.removesuffix("-SWAP") if inst_type == "SWAP" else inst_id
        if notional_currency != pair.split("-")[1]:
            raise FeeDataError("FEE_CURRENCY_MISMATCH")
        self.market_data_provider = market_data_provider
        self.connector_name = connector_name
        self.account_id = account_id
        self.instrument = dict(instrument)
        self.notional_currency = notional_currency

    async def fetch(self) -> FeeRateSnapshot:
        connectors = getattr(self.market_data_provider, "connectors", None)
        connector = connectors.get(self.connector_name) if isinstance(connectors, dict) else None
        if connector is None or not callable(getattr(connector, "_api_get", None)):
            raise FeeDataError("FEE_CONNECTOR_UNAVAILABLE")
        inst_type, inst_id, _ = _instrument(self.instrument)
        params = {"instType": inst_type}
        if inst_type == "SPOT":
            params["instId"] = inst_id
        else:
            params["instFamily"] = inst_id.removesuffix("-SWAP")
        response = await connector._api_get(
            path_url=FEE_RATES_PATH, params=params, is_auth_required=True)
        return FeeRateSnapshot.from_okx(
            response, account_id=self.account_id, connector_name=self.connector_name,
            instrument=self.instrument, notional_currency=self.notional_currency)


class CachedFeeRateSource:
    """Explicit refresh cadence; invalidate before I/O and never reuse failed data."""

    def __init__(self, source: OkxFeeRateSource, *, utc_clock_ms: Callable[[], int], refresh_interval_ms: int):
        if (not callable(getattr(source, "fetch", None)) or not callable(utc_clock_ms)
                or type(refresh_interval_ms) is not int or refresh_interval_ms <= 0):
            raise ValueError("FEE_REFRESH_POLICY_INVALID")
        self.source, self.utc_clock_ms = source, utc_clock_ms
        self.refresh_interval_ms = refresh_interval_ms
        self._snapshot = None
        self._due_ms = 0
        self._last_now = None
        self._refreshing = False
        self._clock_invalid = False

    def _now(self):
        now = self.utc_clock_ms()
        if (self._clock_invalid or type(now) is not int or now <= 0
                or self._last_now is not None and now < self._last_now):
            self._clock_invalid = True
            self._snapshot = None
            raise ValueError("FEE_REFRESH_CLOCK_INVALID")
        self._last_now = now
        return now

    def snapshot(self) -> FeeRateSnapshot | None:
        try:
            now = self._now()
            return self._snapshot if not self._refreshing and now < self._due_ms else None
        except Exception:
            return None

    async def refresh_if_due(self) -> bool:
        try:
            now = self._now()
            if self._refreshing or now < self._due_ms:
                return False
            self._refreshing = True
            self._snapshot = None
            self._due_ms = now + self.refresh_interval_ms
            try:
                observed = await self.source.fetch()
                finished = self._now()
                if (not isinstance(observed, FeeRateSnapshot)
                        or type(observed.exchange_timestamp_ms) is not int
                        or not 0 < observed.exchange_timestamp_ms <= finished
                        or finished >= self._due_ms):
                    return False
                self._snapshot = observed
                return True
            finally:
                self._refreshing = False
        except Exception:
            self._snapshot = None
            return False


@dataclass(frozen=True)
class ActualFillFee:
    account_id: str
    inst_type: str
    instrument_id: str
    trade_id: str
    currency: str
    cost_amount: Decimal
    fill_time_ms: int
    reported_at_ms: int

    @classmethod
    def from_okx(cls, fill: Any, *, account_id: str, inst_type: str,
                 instrument_id: str) -> "ActualFillFee":
        if not isinstance(account_id, str) or not account_id.strip():
            raise FeeDataError("FEE_ACCOUNT_MISSING")
        if inst_type not in ("SPOT", "SWAP") or not isinstance(instrument_id, str) or not instrument_id:
            raise FeeDataError("FEE_INSTRUMENT_MISMATCH")
        if inst_type == "SWAP" and not instrument_id.endswith("-SWAP"):
            raise FeeDataError("FEE_INSTRUMENT_MISMATCH")
        if inst_type == "SPOT" and instrument_id.endswith("-SWAP"):
            raise FeeDataError("FEE_INSTRUMENT_MISMATCH")
        if not isinstance(fill, dict) or fill.get("instId") != instrument_id:
            raise FeeDataError("FEE_INSTRUMENT_MISMATCH")
        trade_id = fill.get("tradeId")
        if not isinstance(trade_id, str) or not trade_id:
            raise FeeDataError("FEE_TRADE_ID_INVALID")
        fill_time = _positive_milliseconds(fill.get("fillTime"))
        reported_at = _positive_milliseconds(fill.get("ts"))
        if reported_at < fill_time:
            raise FeeDataError("FEE_TIMESTAMP_INVALID")
        return cls(account_id=account_id, inst_type=inst_type, instrument_id=instrument_id,
                   trade_id=trade_id, currency=_currency(fill.get("feeCcy")),
                   cost_amount=-_decimal(fill.get("fee"), "FEE_AMOUNT_INVALID"),
                   fill_time_ms=fill_time, reported_at_ms=reported_at)


@dataclass(frozen=True)
class FeeReconciliation:
    actual: ActualFillFee
    estimated_cost: Decimal
    estimated_currency: str
    variance: Decimal | None
    reason_code: str


class FeeReconciler:
    def __init__(self):
        self._by_fill: dict[tuple[str, str, str, str], FeeReconciliation] = {}

    def record(self, actual: ActualFillFee, *, estimated_cost: Decimal,
               estimated_currency: str) -> FeeReconciliation:
        if not isinstance(actual, ActualFillFee):
            raise FeeDataError("FEE_FILL_INVALID")
        if not isinstance(estimated_cost, Decimal) or not estimated_cost.is_finite():
            raise FeeDataError("FEE_ESTIMATE_INVALID")
        _currency(estimated_currency)
        key = (actual.account_id, actual.inst_type, actual.instrument_id, actual.trade_id)
        if actual.currency == estimated_currency:
            result = FeeReconciliation(actual, estimated_cost, estimated_currency,
                                       actual.cost_amount - estimated_cost, "FEE_RECONCILED")
        else:
            result = FeeReconciliation(actual, estimated_cost, estimated_currency,
                                       None, "FEE_CURRENCY_CONVERSION_REQUIRED")
        previous = self._by_fill.get(key)
        if previous is not None:
            if previous != result:
                raise FeeDataError("FEE_FILL_CONFLICT")
            return previous
        self._by_fill[key] = result
        return result

    def actual_costs_by_currency(self) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for result in self._by_fill.values():
            currency = result.actual.currency
            totals[currency] = totals.get(currency, Decimal("0")) + result.actual.cost_amount
        return totals
