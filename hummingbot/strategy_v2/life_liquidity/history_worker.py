"""Explicitly bounded, read-only history preparation outside the event loop.

This is precomputation, never an order permission or a financial checkpoint.
Timeout/cancellation does not stop a running thread: its admission slot remains
occupied until it exits, and its late result has no writer callback.
"""
import asyncio
import hashlib
import json
import math
import multiprocessing
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, fields
from decimal import Decimal, localcontext
from threading import Lock


@dataclass(frozen=True)
class HistoryWorkerPolicy:
    max_orders: int
    max_trades: int
    max_snapshot_bytes: int
    timeout_ms: int
    backend: str = "thread"

    def __post_init__(self):
        if (any(type(getattr(self, name)) is not int or getattr(self, name) <= 0 for name in (
                "max_orders", "max_trades", "max_snapshot_bytes", "timeout_ms"))
                or self.backend not in ("thread", "process")):
            raise ValueError("JOINT_HISTORY_WORKER_POLICY_INVALID")


@dataclass(frozen=True)
class PreparedHistory:
    bundle: object
    encoded: dict
    orders: tuple
    proof_id: str


@dataclass(frozen=True)
class _ReservationImage:
    raw: bytes
    path: str
    limits: object
    life_balance: Decimal
    usdt_balance: Decimal
    reservations: tuple
    trades: tuple
    account_events: tuple


def _bootstrap(_):
    # Spawn imports and worker initialization happen explicitly before any
    # order eligibility, never inside a measured final-send deadline.
    from hummingbot.strategy_v2.life_liquidity import joint_recovery, risk
    return joint_recovery.__name__, risk.__name__


def _capture(bundle, policy):
    # Local imports avoid the recovery/worker type-definition cycle.
    from hummingbot.strategy_v2.life_liquidity.joint_recovery import (
        RecoveryBundle,
        RecoveryOrder,
        RecoveryPayment,
        RecoveryTrade,
    )
    from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalClaim

    if (type(bundle) is not RecoveryBundle or type(bundle.orders) is not tuple
            or len(bundle.orders) > policy.max_orders):
        raise ValueError("JOINT_HISTORY_SNAPSHOT_INVALID_OR_CAPACITY")
    trades = 0
    for order in bundle.orders:
        if type(order) is not RecoveryOrder or type(order.trades) is not tuple:
            raise ValueError("JOINT_HISTORY_SNAPSHOT_INVALID")
        trades += len(order.trades)
        if trades > policy.max_trades:
            raise ValueError("JOINT_HISTORY_SNAPSHOT_CAPACITY")
    types = {cls: tuple(field.name for field in fields(cls)) for cls in (
        RecoveryBundle, RecoveryOrder, RecoveryPayment, RecoveryTrade, CapitalClaim)}
    size = 0

    def detach(value):
        nonlocal size
        # Conservative escaped-JSON upper bound, including field/container
        # overhead. Reject before allocating a complete serialized snapshot.
        size += 128
        if type(value) in (str, int, bool, Decimal, type(None)):
            size += 6 * len(str(value))
        if size > policy.max_snapshot_bytes:
            raise ValueError("JOINT_HISTORY_SNAPSHOT_CAPACITY")
        if type(value) in (str, int, bool, type(None)):
            return value
        if type(value) is Decimal:
            if not value.is_finite():
                raise ValueError("JOINT_HISTORY_SNAPSHOT_INVALID")
            return value
        if type(value) is tuple:
            return tuple(detach(item) for item in value)
        names = types.get(type(value))
        if names is None or set(vars(value)) != set(names):
            raise ValueError("JOINT_HISTORY_SNAPSHOT_INVALID")
        return type(value)(**{name: detach(getattr(value, name)) for name in names})

    return detach(bundle)


def _encode(snapshot):
    from hummingbot.strategy_v2.life_liquidity.joint_recovery import encode_order
    from hummingbot.strategy_v2.life_liquidity.policy_state import _freeze

    bundle, max_bytes, decimal_context = snapshot
    # Executor threads do not inherit the caller's Decimal context.
    with localcontext(decimal_context):
        orders = tuple(_freeze(encode_order(order)) for order in bundle.orders)
        encoded = {key: value for key, value in vars(bundle).items() if key != "orders"}
        encoded["payments"] = [
            {key: str(value) if isinstance(value, Decimal) else value for key, value in vars(payment).items()}
            for payment in bundle.payments]
        encoded["orders"] = sorted(orders, key=lambda order: order["claim"]["intent_id"])
        # Normalize tuple branches to immutable JSON arrays on the worker too.
        encoded["pending_wire_ids"] = list(bundle.pending_wire_ids)
        encoded["algo_wire_ids"] = list(bundle.algo_wire_ids)
        raw = json.dumps(encoded, sort_keys=True, separators=(",", ":")).encode()
        if len(raw) > max_bytes:
            raise ValueError("JOINT_HISTORY_SNAPSHOT_CAPACITY")
        return PreparedHistory(bundle, _freeze(encoded), orders, hashlib.sha256(raw).hexdigest())


def _decode_reservations(snapshot):
    from pathlib import Path

    from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger

    raw, path, limits, policy, decimal_context = snapshot
    with localcontext(decimal_context):
        evidence = ReservationLedger._restore_snapshot(raw, path=Path(path), limits=limits,
                                                       max_reservations=policy.max_orders, max_trades=policy.max_trades)
        return _ReservationImage(raw, path, limits, evidence.life_balance, evidence.usdt_balance,
                                 tuple(evidence._reservations.items()), tuple(evidence._trades.items()),
                                 tuple(evidence._account_events.items()))


class HistoryWorker:
    def __init__(self, policy: HistoryWorkerPolicy):
        if type(policy) is not HistoryWorkerPolicy:
            raise ValueError("JOINT_HISTORY_WORKER_POLICY_INVALID")
        self._policy = policy
        self._mutex = Lock()
        self._executor = None
        self._pending = None
        self._closed = False
        self._started = policy.backend == "thread"

    @property
    def policy(self):
        return self._policy

    @property
    def busy(self):
        with self._mutex:
            return self._pending is not None and not self._pending.done()

    async def prepare(self, bundle, *, timeout_ms=None):
        from decimal import getcontext

        return await self._run(_encode, lambda: (
            _capture(bundle, self.policy), self.policy.max_snapshot_bytes, getcontext().copy()), timeout_ms)

    async def decode_reservations(self, raw, *, path, limits, timeout_ms=None):
        from decimal import getcontext
        from pathlib import Path
        from threading import RLock
        from types import MappingProxyType

        from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits

        def capture():
            if (type(raw) is not bytes or len(raw) > self.policy.max_snapshot_bytes
                    or type(path) is not str or type(limits) is not RiskLimits
                    or any(type(value) is not Decimal for value in vars(limits).values())):
                raise ValueError("JOINT_HISTORY_SNAPSHOT_INVALID_OR_CAPACITY")
            # Detach even the frozen policy dataclass from the live ledger.
            return raw, path, RiskLimits(**vars(limits)), self.policy, getcontext().copy()

        image = await self._run(_decode_reservations, capture, timeout_ms)
        # The process returns only immutable scalar/fact tuples, never a lock,
        # journal instance, connector or live controller. Reconstitute the
        # already-validated candidate without parsing/replaying it again.
        evidence = ReservationLedger.__new__(ReservationLedger)
        evidence.path, evidence.limits = Path(image.path), image.limits
        evidence.life_balance, evidence.usdt_balance = image.life_balance, image.usdt_balance
        evidence._reservations = MappingProxyType(dict(image.reservations))
        evidence._trades = MappingProxyType(dict(image.trades))
        evidence._account_events = MappingProxyType(dict(image.account_events))
        evidence._lock, evidence._uncertain = RLock(), False
        evidence._archive = None
        evidence._archive_authority = None
        evidence._durable_raw = image.raw
        evidence._snapshot_raw = image.raw
        evidence._sealed_evidence = True
        return evidence

    async def start(self, *, timeout_ms):
        """Explicit startup budget; startup itself grants no trading permission."""
        await self._run(_bootstrap, lambda: None, timeout_ms, startup=True)
        with self._mutex:
            if self._closed:
                raise ValueError("JOINT_HISTORY_WORKER_CLOSED")
            self._started = True

    async def _run(self, function, capture, timeout_ms, *, startup=False):
        deadline_ms = self.policy.timeout_ms if timeout_ms is None else timeout_ms
        if type(deadline_ms) is not int or deadline_ms <= 0:
            raise ValueError("JOINT_HISTORY_WORKER_POLICY_INVALID")
        deadline = time.monotonic() + min(deadline_ms, self.policy.timeout_ms) / 1000
        with self._mutex:
            if self._closed:
                raise ValueError("JOINT_HISTORY_WORKER_CLOSED")
            if not self._started and not startup:
                raise ValueError("JOINT_HISTORY_WORKER_NOT_STARTED")
            if self._pending is not None and not self._pending.done():
                raise ValueError("JOINT_HISTORY_WORKER_BUSY")
            detached = capture()
            remaining = deadline - time.monotonic()
            if not math.isfinite(remaining) or remaining <= 0:
                raise ValueError("JOINT_HISTORY_WORKER_TIMEOUT")
            if self._executor is None:
                self._executor = (ThreadPoolExecutor(max_workers=1, thread_name_prefix="life-history")
                                  if self.policy.backend == "thread" else ProcessPoolExecutor(
                                      max_workers=1, mp_context=multiprocessing.get_context("spawn")))
            try:
                future = self._executor.submit(function, detached)
            except Exception as exc:
                raise ValueError("JOINT_HISTORY_WORKER_FAILED") from exc
            self._pending = future
        wrapped = asyncio.wrap_future(future)
        # Observe late exceptions even if the awaiting task times out/cancels.
        wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        try:
            result = await asyncio.wait_for(asyncio.shield(wrapped), remaining)
        except TimeoutError as exc:
            raise ValueError("JOINT_HISTORY_WORKER_TIMEOUT") from exc
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ValueError("JOINT_HISTORY_WORKER_FAILED") from exc
        with self._mutex:
            if self._closed:
                raise ValueError("JOINT_HISTORY_WORKER_CLOSED")
        if time.monotonic() > deadline:
            raise ValueError("JOINT_HISTORY_WORKER_TIMEOUT")
        return result

    def close(self):
        with self._mutex:
            self._closed = True
            if self._executor is not None:
                self._executor.shutdown(wait=False, cancel_futures=True)
