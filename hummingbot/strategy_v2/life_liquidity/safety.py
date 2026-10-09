"""Persisted safety state and priority decisions."""

import json
import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path


def _valid(value: Decimal, *, positive: bool = False) -> bool:
    return (isinstance(value, Decimal) and value.is_finite()
            and (value > 0 if positive else value >= 0))


@dataclass(frozen=True)
class SafetyObservation:
    observed_monotonic_ms: int
    market_data_fresh: bool
    latency_ok: bool
    account_ready: bool
    model_ready: bool
    drawdown_bps: Decimal
    margin_buffer_quote: Decimal


@dataclass(frozen=True)
class SafetyDecision:
    state: str
    reason_code: str
    cancel_required: bool
    allow_new_quotes: bool
    max_new_quote_base: Decimal | None


class SafetyGate:
    def __init__(self, path: Path, *, max_drawdown_bps: Decimal,
                 min_margin_buffer_quote: Decimal, stable_data_ms: int,
                 recovery_probe_base: Decimal):
        if (not _valid(max_drawdown_bps, positive=True)
                or not _valid(min_margin_buffer_quote)
                or not isinstance(stable_data_ms, int) or stable_data_ms < 0
                or not _valid(recovery_probe_base, positive=True)):
            raise ValueError("safety limits must be explicit finite values")
        self.path = Path(path)
        self.max_drawdown_bps = max_drawdown_bps
        self.min_margin_buffer_quote = min_margin_buffer_quote
        self.stable_data_ms = stable_data_ms
        self.recovery_probe_base = recovery_probe_base
        self.state = "PAUSED"
        self.reason_code = "STARTUP_REVALIDATION"
        self._good_since_ms: int | None = None
        self._last_seen_ms: int | None = None
        if self.path.exists():
            with self.path.open(encoding="utf-8") as handle:
                data = json.load(handle)
            if data.get("schema_version") != 1 or data.get("halted") not in (True, False):
                raise ValueError("safety journal invalid")
            if data["halted"]:
                self.state = "HALTED"
                self.reason_code = data.get("reason_code", "HALT_LATCHED")

    def _persist_halt(self, reason_code: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "halted": True, "reason_code": reason_code}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _decision(self) -> SafetyDecision:
        return SafetyDecision(self.state, self.reason_code,
                              self.state in ("PAUSED", "HALTED"),
                              self.state in ("NORMAL", "DEGRADED"),
                              (self.recovery_probe_base if self.state == "DEGRADED" else
                               None if self.state == "NORMAL" else Decimal("0")))

    def invalidate(self, reason_code: str) -> SafetyDecision:
        """Reset recovery after an unavailable runtime observation; retain a latched HALT."""
        if self.state != "HALTED":
            self.state, self.reason_code = "PAUSED", reason_code
            self._good_since_ms = None
        return self._decision()

    def halt(self, reason_code: str) -> SafetyDecision:
        """Latch an operator stop before attempting its durable checkpoint."""
        if not isinstance(reason_code, str) or not reason_code:
            raise ValueError("HALT_REASON_REQUIRED")
        if self.state != "HALTED":
            self.state, self.reason_code = "HALTED", reason_code
            self._good_since_ms = None
        self._persist_halt(self.reason_code)
        return self._decision()

    def evaluate(self, observation: SafetyObservation) -> SafetyDecision:
        if self.state == "HALTED":
            return self._decision()
        now = observation.observed_monotonic_ms
        if not isinstance(now, int) or isinstance(now, bool) or now < 0 or (
                self._last_seen_ms is not None and now < self._last_seen_ms):
            self.state, self.reason_code = "PAUSED", "SAFETY_CLOCK_INVALID"
            self._good_since_ms = None
            return self._decision()
        self._last_seen_ms = now
        if (not _valid(observation.drawdown_bps) or not _valid(observation.margin_buffer_quote)
                or any(type(value) is not bool for value in (
                    observation.market_data_fresh, observation.latency_ok,
                    observation.account_ready, observation.model_ready))):
            self.state, self.reason_code = "PAUSED", "RISK_DATA_UNAVAILABLE"
            self._good_since_ms = None
            return self._decision()
        if observation.drawdown_bps > self.max_drawdown_bps:
            reason = "DRAWDOWN_LIMIT_BREACHED"
        elif observation.margin_buffer_quote < self.min_margin_buffer_quote:
            reason = "MARGIN_BUFFER_BREACHED"
        else:
            reason = None
        if reason is not None:
            self._persist_halt(reason)
            self.state, self.reason_code = "HALTED", reason
            return self._decision()
        for ready, failure in (
            (observation.account_ready, "ACCOUNT_DATA_UNAVAILABLE"),
            (observation.market_data_fresh, "MARKET_DATA_STALE"),
            (observation.latency_ok, "LATENCY_EXCEEDED"),
            (observation.model_ready, "MODEL_DIVERGENCE"),
        ):
            if not ready:
                self.state, self.reason_code = "PAUSED", failure
                self._good_since_ms = None
                return self._decision()
        if self.state == "PAUSED":
            if self._good_since_ms is None:
                self._good_since_ms = now
            if now - self._good_since_ms < self.stable_data_ms:
                self.reason_code = "STABLE_DATA_WAIT"
                return self._decision()
            self.state, self.reason_code = "DEGRADED", "RECOVERY_PROBE"
        elif self.state == "DEGRADED" and now - self._good_since_ms >= 2 * self.stable_data_ms:
            self.state, self.reason_code = "NORMAL", "SAFETY_READY"
        return self._decision()
