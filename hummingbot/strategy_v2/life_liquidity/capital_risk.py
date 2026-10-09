"""Opt-in, durable high-water NAV measurement from independent LIFE values."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from hummingbot.strategy_v2.life_liquidity.accounting import CapitalMeasurement
from hummingbot.strategy_v2.life_liquidity.fill_attribution import ReconciledFillAttributor


@dataclass(frozen=True)
class IndependentNavObservation:
    price_usdt: Decimal
    value_at_ms: int
    observed_at_ms: int
    source_kind: str


class CapitalRiskMonitor:
    def __init__(self, path: Path, *, attributor: ReconciledFillAttributor,
                 independent_value: Callable[[], IndependentNavObservation],
                 utc_clock_ms: Callable[[], int], max_value_age_ms: int, create: bool):
        if (not isinstance(attributor, ReconciledFillAttributor)
                or not callable(independent_value) or not callable(utc_clock_ms)
                or not isinstance(max_value_age_ms, int) or isinstance(max_value_age_ms, bool)
                or max_value_age_ms < 0 or not isinstance(create, bool)):
            raise ValueError("CAPITAL_RISK_POLICY_INVALID")
        self.path = Path(path)
        self.attributor = attributor
        self.independent_value = independent_value
        self.utc_clock_ms = utc_clock_ms
        self.max_value_age_ms = max_value_age_ms
        self.opening_nav_quote = (attributor.opening_usdt
                                  + attributor.opening_life
                                  * attributor.opening_independent_price_usdt)
        self._state = {"highwater_quote": str(self.opening_nav_quote),
                       "last_value_at_ms": None, "last_price_usdt": None,
                       "last_checked_at_ms": None}
        if create:
            with self._file_lock():
                if self.path.exists() or self.path.is_symlink():
                    raise ValueError("CAPITAL_RISK_EXISTS_USE_RESTORE")
                self._save(self._state)
        else:
            self._state = self._read()

    def _policy(self) -> dict:
        return {"attribution_path": str(self.attributor.path.resolve()),
                "attribution_policy": self.attributor._policy(),
                "max_value_age_ms": self.max_value_age_ms}

    @contextmanager
    def _file_lock(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f"{self.path.name}.lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _read(self) -> dict:
        if not self.path.is_file() or self.path.is_symlink():
            raise ValueError("CAPITAL_RISK_JOURNAL_UNAVAILABLE")
        with self.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or data.get("policy") != self._policy()
                or not isinstance(data.get("state"), dict)):
            raise ValueError("CAPITAL_RISK_JOURNAL_INVALID")
        state = data["state"]
        try:
            highwater = Decimal(state["highwater_quote"])
            value_at = state["last_value_at_ms"]
            price_raw = state["last_price_usdt"]
            price = None if price_raw is None else Decimal(price_raw)
            checked_at = state["last_checked_at_ms"]
            if (not highwater.is_finite() or highwater < self.opening_nav_quote
                    or not (value_at is None and price is None and checked_at is None
                            or isinstance(value_at, int) and not isinstance(value_at, bool)
                            and value_at > 0 and price is not None
                            and price.is_finite() and price > 0
                            and isinstance(checked_at, int) and not isinstance(checked_at, bool)
                            and checked_at >= value_at)):
                raise ValueError("CAPITAL_RISK_JOURNAL_INVALID")
        except (InvalidOperation, KeyError, TypeError, ValueError):
            raise ValueError("CAPITAL_RISK_JOURNAL_INVALID")
        return state

    def _verified(self) -> bool:
        return self._read() == self._state

    def _save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"schema_version": 1, "policy": self._policy(), "state": state},
                          handle, sort_keys=True)
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

    def measure(self) -> CapitalMeasurement | None:
        """Fail closed on stale value, attribution, or any ambiguous journal write."""
        try:
            with self._file_lock():
                if not self._verified() or not self.attributor.ready():
                    return None
                now = self.utc_clock_ms()
                value = self.independent_value()
                if (not isinstance(now, int) or isinstance(now, bool) or now <= 0
                        or not isinstance(value, IndependentNavObservation)
                        or value.source_kind != "independent_market"
                        or not isinstance(value.price_usdt, Decimal)
                        or not value.price_usdt.is_finite() or value.price_usdt <= 0
                        or not isinstance(value.value_at_ms, int)
                        or isinstance(value.value_at_ms, bool) or value.value_at_ms <= 0
                        or not isinstance(value.observed_at_ms, int)
                        or isinstance(value.observed_at_ms, bool)
                        or value.value_at_ms > value.observed_at_ms
                        or value.observed_at_ms > now
                        or now - value.value_at_ms > self.max_value_age_ms):
                    return None
                previous_at = self._state["last_value_at_ms"]
                previous_checked = self._state["last_checked_at_ms"]
                clock_rolled_back = previous_checked is not None and now < previous_checked
                value_rolled_back = previous_at is not None and value.value_at_ms < previous_at
                value_conflicted = (previous_at is not None and value.value_at_ms == previous_at
                                    and str(value.price_usdt) != self._state["last_price_usdt"])
                if clock_rolled_back or value_rolled_back or value_conflicted:
                    return None
                capital = self.attributor.capital()
                capital.highwater_quote = Decimal(self._state["highwater_quote"])
                measurement = capital.measure(value.price_usdt, source_kind=value.source_kind)
                if measurement is None or not self.attributor.ready():
                    return None
                updated = {"highwater_quote": str(measurement.highwater_quote),
                           "last_value_at_ms": value.value_at_ms,
                           "last_price_usdt": str(value.price_usdt),
                           "last_checked_at_ms": now}
                if updated != self._state:
                    self._save(updated)
                    self._state = updated
                return measurement
        except Exception:
            return None
