"""Opt-in bounded telemetry; explicit units/quality, no arbitrary payloads.

This journal records observations, not send authority or proof of exchange
history. A failed writer stays unhealthy until explicit restore. Offline values
are labeled synthetic; missing/stale/not-applicable metrics always carry null.
"""

import fcntl
import json
import os
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from threading import Lock, RLock
from typing import Callable

METRIC_UNITS = {
    **dict.fromkeys(("book_source_age_ms", "reference_source_age_ms", "session_remaining_ms",
                     "ack_latency_ms", "cancel_confirm_latency_ms"), "ms"),
    **dict.fromkeys(("inventory_base", "pending_buy_base", "pending_sell_base",
                     "quoted_bid_depth_base", "quoted_ask_depth_base"), "LIFE"),
    **dict.fromkeys(("usdt_balance", "nav_quote", "adjusted_nav_quote", "net_pnl_quote",
                     "starting_inventory_pnl_quote", "inventory_pnl_quote", "other_inventory_pnl_quote", "liquidation_value_quote", "execution_loss_quote",
                     "fees_quote", "fill_time_gross_edge_quote", "fill_time_net_edge_quote",
                     "quote_gross_edge_quote", "quote_maker_cost_quote", "quote_exit_cost_quote",
                     "quote_impact_cost_quote", "quote_carry_cost_quote", "quote_inventory_risk_quote",
                     "quote_uncertainty_cost_quote", "quote_net_edge_quote", "margin_quote", "funding_quote",
                     "hedge_cost_quote", "subsidy_committed_quote", "subsidy_available_quote",
                     "loss_budget_available_quote"), "USDT"),
    "drawdown_bps": "bps", "quoted_spread_bps": "bps", "reject_count": "count",
    "two_sided_availability_session_ratio": "ratio", "two_sided_availability_eligible_ratio": "ratio",
}
INPUT_TYPES = {
    **dict.fromkeys(("reference_usdt", "exit_value_usdt", "best_bid_usdt", "best_ask_usdt",
                     "maker_fee_rate", "exit_fee_rate", "impact_cost_usdt", "carry_cost_usdt",
                     "inventory_cost_usdt", "uncertainty_bps", "min_net_edge_bps",
                     "observed_monotonic_ms", "expires_monotonic_ms", "intent_price_usdt",
                     "intent_quantity_base", "intent_remaining_base", "buy_independent_depth_base",
                     "sell_independent_depth_base", "volatility_bps", "buy_markout_loss_bps",
                     "sell_markout_loss_bps", "max_spread_bps", "max_depth_fraction",
                     "target_inventory_base", "inventory_band_base", "max_inventory_widen_bps"), "decimal"),
    **dict.fromkeys(("reference_ready", "all_gates_ready", "market_reference_ready", "cancel_requested"), "bool"),
    **dict.fromkeys(("planner_reason", "fee_reason", "subsidy_reason", "runtime_reason", "markout_reason", "wal_state", "intent_side"), "code"),
}
GATES = ("runtime", "stops", "spot_risk", "joint", "hedge", "loss", "accounting", "markout",
         "runner_events", "session", "market", "telemetry")
STAGES = ("RISK_EVENT", "PERMISSION", "FINAL_SEND", "QUEUE_REJECT", "QUEUE_DISPATCH",
          "CANCEL_REQUEST", "EXCHANGE_CONFIRM", "ACK", "SNAPSHOT")


def _ms(value):
    return type(value) is int and value >= 0


def _code(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", value) is not None


def _identity(value):
    return value is None or isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) is not None


@dataclass(frozen=True)
class Metric:
    value: Decimal | None
    unit: str
    quality: str
    reason_code: str

    def __post_init__(self):
        if (self.unit not in ("ms", "LIFE", "USDT", "bps", "count", "ratio")
                or self.quality not in ("verified", "synthetic", "missing", "stale", "not_applicable")
                or not _code(self.reason_code)
                or (self.quality in ("verified", "synthetic")
                    and (not isinstance(self.value, Decimal) or not self.value.is_finite()))
                or (self.quality in ("missing", "stale", "not_applicable") and self.value is not None)):
            raise ValueError("TELEMETRY_METRIC_INVALID")

    def payload(self):
        return {**asdict(self), "value": None if self.value is None else str(self.value)}


class TelemetryRecorder:
    def __init__(self, path: Path, *, clock_ms: Callable[[], int], synthetic: bool,
                 max_records: int, create: bool):
        if (not callable(clock_ms) or type(synthetic) is not bool or type(create) is not bool
                or type(max_records) is not int or not 0 < max_records <= 100000):
            raise ValueError("TELEMETRY_POLICY_INVALID")
        self.path = Path(path)
        self.clock_ms = clock_ms
        self.synthetic = synthetic
        self._policy = dict(schema_version=1, synthetic=synthetic, max_records=max_records)
        self._lock = RLock()
        self._flush_lock = Lock()
        self.healthy = True
        self.active_event_id = None
        self._rows = []
        if self.path.is_symlink():
            raise ValueError("TELEMETRY_JOURNAL_INVALID")
        if create:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            header = json.dumps({"policy": self._policy}, sort_keys=True) + "\n"
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(header)
                handle.flush()
                os.fsync(handle.fileno())
        try:
            self._durable = self.path.read_bytes()
            if not self._durable.endswith(b"\n"):
                raise ValueError("TELEMETRY_JOURNAL_INVALID")
            lines = self._durable.splitlines()
            if json.loads(lines[0]) != {"policy": self._policy}:
                raise ValueError("TELEMETRY_POLICY_MISMATCH")
            for line in lines[1:]:
                row = json.loads(line)
                normalized = self._normalize(
                    **{key: row[key] for key in (
                        "stage", "allowed", "reason_code", "session_id", "epoch", "config_version",
                        "intent_id", "wire_id", "event_id", "gates", "inputs")},
                    metrics={key: Metric(None if value["value"] is None else Decimal(value["value"]),
                                         value["unit"], value["quality"], value["reason_code"])
                             for key, value in row["metrics"].items()},
                    markouts={key: Metric(None if value["value"] is None else Decimal(value["value"]),
                                          value["unit"], value["quality"], value["reason_code"])
                              for key, value in row["markouts"].items()},
                    at_ms=row["at_ms"])
                if row != normalized:
                    raise ValueError("TELEMETRY_JOURNAL_INVALID")
                self._rows.append(row)
            self._durable_count = len(self._rows)
        except (KeyError, IndexError, TypeError, OSError, ArithmeticError) as exc:
            raise ValueError("TELEMETRY_JOURNAL_INVALID") from exc

    def _normalize(self, *, stage, allowed, reason_code, session_id=None, epoch=None, config_version=None,
                   intent_id=None, wire_id=None, event_id=None, gates=None, inputs=None, metrics=None, markouts=None, at_ms):
        gates = {} if gates is None else gates
        inputs = {} if inputs is None else inputs
        metrics = {} if metrics is None else metrics
        markouts = {} if markouts is None else markouts
        if (stage not in STAGES or allowed is not None and type(allowed) is not bool
                or not _code(reason_code) or not all(_identity(value) for value in (
                    session_id, intent_id, wire_id, event_id))
                or any(value is not None and (type(value) is not int or value < 1)
                       for value in (epoch, config_version))
                or not _ms(at_ms) or len(self._rows) >= self._policy["max_records"]
                or self._rows and at_ms < self._rows[-1]["at_ms"]
                or not isinstance(gates, dict) or not set(gates) <= set(GATES)
                or any(value is not None and type(value) is not bool for value in gates.values())
                or not isinstance(inputs, dict) or not set(inputs) <= set(INPUT_TYPES)
                or any((INPUT_TYPES[key] == "bool" and type(value) is not bool
                        or INPUT_TYPES[key] == "code" and not _code(value)
                        or INPUT_TYPES[key] == "decimal" and (
                            not isinstance(value, str) or len(value) > 96
                            or not Decimal(value).is_finite())) for key, value in inputs.items())
                or not isinstance(metrics, dict) or not set(metrics) <= set(METRIC_UNITS)
                or not isinstance(markouts, dict)
                or any(not isinstance(key, str) or re.fullmatch(r"(BUY|SELL):(small|large):[1-9][0-9]*", key) is None
                       for key in markouts)):
            raise ValueError("TELEMETRY_RECORD_INVALID")
        complete = {key: Metric(None, unit, "missing", "NOT_OBSERVED") for key, unit in METRIC_UNITS.items()}
        complete.update(metrics)
        for key, metric in {**complete, **markouts}.items():
            if (not isinstance(metric, Metric) or metric.unit != METRIC_UNITS.get(key, "USDT")
                    or self.synthetic and metric.quality == "verified"
                    or not self.synthetic and metric.quality == "synthetic"):
                raise ValueError("TELEMETRY_METRIC_SCOPE_INVALID")
        return dict(sequence=len(self._rows) + 1, at_ms=at_ms, stage=stage, allowed=allowed,
                    reason_code=reason_code, session_id=session_id, epoch=epoch, config_version=config_version,
                    intent_id=intent_id, wire_id=wire_id, event_id=event_id,
                    gates={key: gates.get(key) for key in GATES}, inputs=dict(inputs),
                    metrics={key: value.payload() for key, value in complete.items()},
                    markouts={key: value.payload() for key, value in markouts.items()})

    def capture(self, *, stage, allowed, reason_code, **fields):
        """Memory-only capture for safety/send/cancel callbacks; no file I/O."""
        with self._lock:
            try:
                if not self.healthy:
                    raise ValueError("TELEMETRY_JOURNAL_UNAVAILABLE")
                row = self._normalize(stage=stage, allowed=allowed, reason_code=reason_code,
                                      at_ms=self.clock_ms(), **fields)
                self._rows.append(row)
            except Exception:
                self.healthy = False
                raise

    def flush(self):
        """Persist outside safety callbacks; disk I/O never holds the capture lock."""
        with self._flush_lock:
            try:
                with self._lock:
                    if not self.healthy:
                        raise ValueError("TELEMETRY_JOURNAL_UNAVAILABLE")
                    count = len(self._rows)
                    rows = tuple(self._rows[self._durable_count:count])
                    previous = self._durable
                encoded = b"".join((json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode()
                                   for row in rows)
                if self.path.is_symlink():
                    raise ValueError("TELEMETRY_JOURNAL_UNAVAILABLE")
                descriptor = os.open(self.path, os.O_RDWR | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0))
                with os.fdopen(descriptor, "r+b") as handle:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                    if handle.read() != previous:
                        raise ValueError("TELEMETRY_WRITER_STALE")
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                with self._lock:
                    self._durable = previous + encoded
                    self._durable_count = count
            except Exception:
                self.healthy = False
                raise

    def record(self, *, stage, allowed, reason_code, **fields):
        self.capture(stage=stage, allowed=allowed, reason_code=reason_code, **fields)
        self.flush()

    def begin_risk_event(self, event_id: str, reason_code: str):
        self.record(stage="RISK_EVENT", allowed=None, reason_code=reason_code, event_id=event_id)
        self.active_event_id = event_id

    def records(self):
        return deepcopy(self._rows)


def risk_latency(event_ms, *, block_ms, cancel_request_ms, confirmed_ms, budgets, synthetic):
    """Missing exchange/account confirmation is unknown even when cancel ACK exists."""
    values = dict(block_ms=block_ms, cancel_request_ms=cancel_request_ms, confirmation_ms=confirmed_ms)
    if (not _ms(event_ms) or type(synthetic) is not bool or not isinstance(budgets, dict)
            or set(budgets) != set(values) or any(not _ms(value) for value in budgets.values())
            or any(value is not None and (not _ms(value) or value < event_ms) for value in values.values())
            or cancel_request_ms is not None and confirmed_ms is not None and confirmed_ms < cancel_request_ms):
        raise ValueError("RISK_LATENCY_INVALID")
    result = {}
    for name, value in values.items():
        metric = Metric(None, "ms", "missing", "NOT_CONFIRMED") if value is None else Metric(
            Decimal(value - event_ms), "ms", "synthetic" if synthetic else "verified", "MONOTONIC_EVENT_DELTA")
        result[name] = {**metric.payload(), "budget_ms": budgets[name],
                        "within_budget": None if value is None else value - event_ms <= budgets[name]}
    return result


def liquidity_availability(intervals, *, synthetic):
    """Explicit non-overlapping intervals; pauses remain in full-session time.

    Each tuple is (start_ms, end_ms, eligible, both_acknowledged_quotes_valid).
    Unknown eligibility/quote state makes the window insufficient evidence.
    """
    if type(synthetic) is not bool:
        raise ValueError("LIQUIDITY_WINDOW_INVALID")
    full = eligible_time = quoted = 0
    previous_end = None
    unknown = not intervals
    for start, end, eligible, both in intervals:
        if (not _ms(start) or not _ms(end) or end <= start
                or previous_end is not None and start != previous_end
                or any(value is not None and type(value) is not bool for value in (eligible, both))
                or both is True and eligible is False):
            raise ValueError("LIQUIDITY_WINDOW_INVALID")
        previous_end = end
        duration = end - start
        full += duration
        eligible_time += duration if eligible is True else 0
        quoted += duration if both is True else 0
        unknown |= eligible is None or both is None
    quality = "synthetic" if synthetic else "verified"
    return {key: (Metric(None, "ratio", "missing", "INSUFFICIENT_EVIDENCE") if unknown or denominator == 0
                  else Metric(Decimal(quoted) / denominator, "ratio", quality, "DURATION_WEIGHTED"))
            for key, denominator in (("full_session", full), ("eligible", eligible_time))}
