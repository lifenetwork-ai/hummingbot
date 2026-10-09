"""Opt-in, durable markout cohorts from exchange-reconciled spot fills."""

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable

from hummingbot.strategy_v2.life_liquidity.fill_attribution import ReconciledFillAttributor
from hummingbot.strategy_v2.life_liquidity.markout import MarkoutSummary, MarkoutTracker


@dataclass(frozen=True)
class IndependentHorizonObservation:
    price_usdt: Decimal
    value_at_ms: int
    observed_at_ms: int
    source_kind: str


class ReconciledMarkoutMonitor:
    def __init__(self, path: Path, *, attributor: ReconciledFillAttributor,
                 independent_horizon: Callable[[str, int], IndependentHorizonObservation | None],
                 utc_clock_ms: Callable[[], int], horizons_ms: tuple[int, ...],
                 min_samples: int, size_cutoff_base: Decimal, cohort_window_ms: int,
                 max_horizon_lag_ms: int, min_mean_markout_quote: Decimal,
                 monitored_cohorts: tuple[tuple[str, str, int], ...], create: bool):
        if (not isinstance(attributor, ReconciledFillAttributor)
                or not callable(independent_horizon) or not callable(utc_clock_ms)
                or not isinstance(horizons_ms, tuple)
                or any(type(horizon) is not int for horizon in horizons_ms)
                or type(min_samples) is not int
                or not isinstance(min_mean_markout_quote, Decimal)
                or not min_mean_markout_quote.is_finite()
                or not isinstance(max_horizon_lag_ms, int)
                or isinstance(max_horizon_lag_ms, bool) or max_horizon_lag_ms < 0
                or not isinstance(cohort_window_ms, int)
                or isinstance(cohort_window_ms, bool) or cohort_window_ms <= 0
                or not isinstance(create, bool)):
            raise ValueError("MARKOUT_RISK_POLICY_INVALID")
        tracker = MarkoutTracker(horizons_ms=horizons_ms, min_samples=min_samples,
                                 size_bucket_edges=(size_cutoff_base,))
        if (cohort_window_ms <= max(horizons_ms)
                or not isinstance(monitored_cohorts, tuple) or not monitored_cohorts
                or len(set(monitored_cohorts)) != len(monitored_cohorts)
                or any(not isinstance(cohort, tuple) or len(cohort) != 3
                       or cohort[0] not in ("BUY", "SELL")
                       or cohort[1] not in ("small", "large")
                       or type(cohort[2]) is not int
                       or cohort[2] not in horizons_ms for cohort in monitored_cohorts)):
            raise ValueError("MARKOUT_RISK_POLICY_INVALID")
        self.path = Path(path)
        self.attributor = attributor
        self.independent_horizon = independent_horizon
        self.utc_clock_ms = utc_clock_ms
        self.horizons_ms = tracker.horizons_ms
        self.min_samples = min_samples
        self.size_cutoff_base = size_cutoff_base
        self.cohort_window_ms = cohort_window_ms
        self.max_horizon_lag_ms = max_horizon_lag_ms
        self.min_mean_markout_quote = min_mean_markout_quote
        self.monitored_cohorts = monitored_cohorts
        self.reason_code = "MARKOUT_STARTUP_REVALIDATION"
        self._state = {"fills": {}, "observations": {}, "last_checked_at_ms": None}
        if create:
            with self._file_lock():
                if self.path.exists() or self.path.is_symlink():
                    raise ValueError("MARKOUT_RISK_EXISTS_USE_RESTORE")
                self._save(self._state)
        else:
            self._state = self._read()

    def _policy(self) -> dict:
        return {"attribution_path": str(self.attributor.path.resolve()),
                "attribution_policy": self.attributor._policy(),
                "horizons_ms": list(self.horizons_ms), "min_samples": self.min_samples,
                "size_cutoff_base": str(self.size_cutoff_base),
                "cohort_window_ms": self.cohort_window_ms,
                "max_horizon_lag_ms": self.max_horizon_lag_ms,
                "min_mean_markout_quote": str(self.min_mean_markout_quote),
                "monitored_cohorts": [list(cohort) for cohort in self.monitored_cohorts]}

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
            raise ValueError("MARKOUT_RISK_JOURNAL_UNAVAILABLE")
        with self.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if (not isinstance(data, dict) or data.get("schema_version") != 1
                or data.get("policy") != self._policy()
                or not isinstance(data.get("state"), dict)):
            raise ValueError("MARKOUT_RISK_JOURNAL_INVALID")
        state = data["state"]
        checked_at = state.get("last_checked_at_ms")
        if (not isinstance(state.get("fills"), dict)
                or not isinstance(state.get("observations"), dict)
                or checked_at is not None and (not isinstance(checked_at, int)
                                               or isinstance(checked_at, bool) or checked_at <= 0)):
            raise ValueError("MARKOUT_RISK_JOURNAL_INVALID")
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

    def _tracker(self, state: dict, now_ms: int) -> MarkoutTracker:
        tracker = MarkoutTracker(horizons_ms=self.horizons_ms, min_samples=self.min_samples,
                                 size_bucket_edges=(self.size_cutoff_base,))
        for trade_id, event in state["fills"].items():
            if event["fill_at_ms"] < now_ms - self.cohort_window_ms:
                continue
            tracker.record_fill(trade_id, event["side"], Decimal(event["quantity_base"]),
                                Decimal(event["price_usdt"]), fill_at_ms=event["fill_at_ms"])
            for horizon, observation in state["observations"].get(trade_id, {}).items():
                if not tracker.observe(trade_id, int(horizon), Decimal(observation["price_usdt"]),
                                       observed_at_ms=observation["observed_at_ms"],
                                       source_kind="independent_market"):
                    raise ValueError("MARKOUT_OBSERVATION_INVALID")
        return tracker

    def summary(self, side: str, size_bucket: str, horizon_ms: int) -> MarkoutSummary:
        try:
            if not self._verified():
                return MarkoutSummary("MARKOUT_UNAVAILABLE", 0, None)
            now = self.utc_clock_ms()
            return self._tracker(self._state, now).summary(
                side, size_bucket, horizon_ms, now_ms=now)
        except Exception:
            return MarkoutSummary("MARKOUT_UNAVAILABLE", 0, None)

    def _qualified_observation(self, value: object, target_ms: int, now_ms: int) -> bool:
        return (isinstance(value, IndependentHorizonObservation)
                and value.source_kind == "independent_market"
                and isinstance(value.price_usdt, Decimal) and value.price_usdt.is_finite()
                and value.price_usdt > 0
                and isinstance(value.value_at_ms, int) and not isinstance(value.value_at_ms, bool)
                and isinstance(value.observed_at_ms, int)
                and not isinstance(value.observed_at_ms, bool)
                and target_ms <= value.value_at_ms <= target_ms + self.max_horizon_lag_ms
                and value.value_at_ms <= value.observed_at_ms <= now_ms
                and value.observed_at_ms - target_ms <= self.max_horizon_lag_ms)

    def evaluate(self) -> bool:
        """Persist newly verified fills/observations before deciding new risk."""
        try:
            with self._file_lock():
                if not self._verified():
                    self.reason_code = "MARKOUT_JOURNAL_UNAVAILABLE"
                    return False
                fills = self.attributor.verified_fills()
                if fills is None:
                    self.reason_code = "MARKOUT_FILL_ATTRIBUTION_UNAVAILABLE"
                    return False
                now = self.utc_clock_ms()
                previous = self._state["last_checked_at_ms"]
                if (not isinstance(now, int) or isinstance(now, bool) or now <= 0
                        or previous is not None and now < previous):
                    self.reason_code = "MARKOUT_CLOCK_INVALID"
                    return False
                source = {record.trade_id: {
                    "side": record.side, "quantity_base": str(record.quantity_base),
                    "price_usdt": str(record.price_usdt), "fill_at_ms": record.fill_at_ms}
                    for record in fills}
                recorded = self._state["fills"]
                if (not set(recorded) <= set(source)
                        or any(source[trade_id] != event for trade_id, event in recorded.items())):
                    self.reason_code = "MARKOUT_FILL_CONFLICT"
                    return False
                persisted_observations = self._state["observations"]
                if not set(persisted_observations) <= set(source):
                    self.reason_code = "MARKOUT_OBSERVATION_CONFLICT"
                    return False
                for trade_id, by_horizon in persisted_observations.items():
                    if not isinstance(by_horizon, dict):
                        self.reason_code = "MARKOUT_OBSERVATION_CONFLICT"
                        return False
                    for horizon_key, event in by_horizon.items():
                        if (horizon_key not in {str(horizon) for horizon in self.horizons_ms}
                                or not isinstance(event, dict)):
                            self.reason_code = "MARKOUT_OBSERVATION_CONFLICT"
                            return False
                        value = IndependentHorizonObservation(
                            Decimal(event["price_usdt"]), event["value_at_ms"],
                            event["observed_at_ms"], "independent_market")
                        target = source[trade_id]["fill_at_ms"] + int(horizon_key)
                        if not self._qualified_observation(value, target, now):
                            self.reason_code = "MARKOUT_OBSERVATION_CONFLICT"
                            return False
                observations = {key: dict(value)
                                for key, value in persisted_observations.items()}
                missing = False
                pending = False
                for trade_id, event in source.items():
                    if event["fill_at_ms"] < now - self.cohort_window_ms:
                        continue
                    for horizon in self.horizons_ms:
                        key = str(horizon)
                        if key in observations.get(trade_id, {}):
                            continue
                        target = event["fill_at_ms"] + horizon
                        if now < target:
                            pending = True
                            continue
                        value = self.independent_horizon(trade_id, horizon)
                        if not self._qualified_observation(value, target, now):
                            missing = True
                            continue
                        observations.setdefault(trade_id, {})[key] = {
                            "price_usdt": str(value.price_usdt),
                            "value_at_ms": value.value_at_ms,
                            "observed_at_ms": value.observed_at_ms}
                updated = {"fills": source, "observations": observations,
                           "last_checked_at_ms": now}
                if updated != self._state:
                    self._save(updated)
                    self._state = updated
                if not self.attributor.ready():
                    self.reason_code = "MARKOUT_FILL_ATTRIBUTION_UNAVAILABLE"
                    return False
                if missing or pending:
                    self.reason_code = ("MARKOUT_OBSERVATION_MISSING" if missing
                                        else "MARKOUT_HORIZON_PENDING")
                    return False
                tracker = self._tracker(updated, now)
                for side, size_bucket, horizon in self.monitored_cohorts:
                    result = tracker.summary(side, size_bucket, horizon, now_ms=now)
                    if result.reason_code != "MARKOUT_READY":
                        self.reason_code = "MARKOUT_INSUFFICIENT_SAMPLES"
                        return False
                    if result.mean_markout_quote < self.min_mean_markout_quote:
                        self.reason_code = "MARKOUT_ADVERSE"
                        return False
                self.reason_code = "MARKOUT_READY"
                return True
        except Exception:
            self.reason_code = "MARKOUT_JOURNAL_UNAVAILABLE"
            return False
