"""LIFE V2 controller adapter for configuration discovery and offline validation.

The trading controller is deliberately inert until the P2–P9 data, accounting,
order, and release gates are implemented. Loading this module cannot place orders.
"""

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field

from hummingbot.connector.exchange.okx.okx_book_health import BookFeedHealth
from hummingbot.strategy_v2.controllers.controller_base import ControllerBase, ControllerConfigBase
from hummingbot.strategy_v2.life_liquidity import account_lock
from hummingbot.strategy_v2.life_liquidity.account_bills import CashflowApprovals, SpotBillReconciler
from hummingbot.strategy_v2.life_liquidity.account_lock import AccountLockUnavailable, AccountRiskPoolLock
from hummingbot.strategy_v2.life_liquidity.config import ConfigUpdateState, StrategyConfig
from hummingbot.strategy_v2.life_liquidity.market_data import (
    BenchmarkConnectorRoute,
    BookContinuityGate,
    ContinuousTradingGate,
    ContractValidationError,
    LinearSwapContract,
    ListingGate,
    OkxInstrumentSource,
    SnapshotQualityGate,
    is_order_book_ready,
)
from hummingbot.strategy_v2.life_liquidity.order_gateway import (
    OkxSpotOrderGateway,
    SpotAccountReconciler,
    SpotReservationReconciler,
)
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits
from hummingbot.strategy_v2.life_liquidity.session import SessionManager, SessionStore
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL

# Synthetic offline guard only. A separately calibrated live book TTL is a P9 decision.
SYNTHETIC_BOOK_MAX_AGE_MS = 2000
SYNTHETIC_FEED_MAX_SILENCE_SECONDS = 65
# Bound retries after metadata becomes available so failed starts do not hammer OKX.
BOOK_BOOTSTRAP_RETRY_BASE_SECONDS = 5
BOOK_BOOTSTRAP_RETRY_MAX_SECONDS = 300


def _simulation_template() -> StrategyConfig:
    """Synthetic CLI create template; never a live allocation or trading policy."""
    return StrategyConfig.model_validate({
        "execution_mode": "simulation",
        "session": {"duration": "4h"},
        "reference": {"mode": "market", "lookback": "15m"},
        "quotes": {"spreads_bps": ["30"], "sizes_base": ["10"]},
        "economics": {"objective": "profit_mm"},
    })


class LifeLiquidityConfig(ControllerConfigBase):
    controller_type: Literal["generic"] = "generic"
    controller_name: Literal["life_liquidity"] = "life_liquidity"
    total_amount_quote: Decimal = Field(
        default=Decimal("0"),
        json_schema_extra={"is_updatable": False},
    )
    strategy: StrategyConfig = Field(
        default_factory=_simulation_template,
        description="Nested settings can be edited in YAML or with hbot config dotted keys; restart to apply.",
        json_schema_extra={"prompt_on_new": False, "is_updatable": False},
    )
    recovery_state_dir: str | None = Field(default=None, json_schema_extra={"is_updatable": False})
    recovery_account_uid: str | None = Field(default=None, json_schema_extra={"is_updatable": False})
    recovery_reconciliation_max_age_ms: int | None = Field(
        default=None, json_schema_extra={"is_updatable": False})

    def update_markets(self, markets):
        # P2.9 bootstraps the public LIFE book after listing checks. Benchmarks
        # are public data routes, not order-capable markets.
        return markets


class LifeLiquidityController(ControllerBase):
    def __init__(self, config: LifeLiquidityConfig, *args, **kwargs):
        super().__init__(config, *args, **kwargs)
        self.config_update_state = ConfigUpdateState(config.strategy)
        self.benchmark_route = BenchmarkConnectorRoute.from_strategy(config.strategy)
        self.listing_gate = ListingGate(inst_type="SPOT", inst_id=config.strategy.spot.pair)
        self.perpetual_listing_gate = ListingGate(
            inst_type="SWAP", inst_id=f"{config.strategy.perpetual.pair}-SWAP")
        self.perpetual_contract: LinearSwapContract | None = None
        self.perpetual_contract_reason_code = "PERPETUAL_DISABLED"
        self._perpetual_poll_task: asyncio.Task | None = None
        self.spot_book_bootstrap_ready = False
        self.spot_book_bootstrap_reason_code = "ORDER_BOOK_BOOTSTRAP_PENDING"
        self.spot_book_bootstrap_failures = 0
        self.spot_book_bootstrap_next_at = 0.0
        self.book_ready = False
        self.continuous_gate = ContinuousTradingGate()
        self.snapshot_gate = SnapshotQualityGate(config.strategy.spot.pair, SYNTHETIC_BOOK_MAX_AGE_MS)
        self.continuity_gate = BookContinuityGate(SYNTHETIC_FEED_MAX_SILENCE_SECONDS)
        self.processed_data = {}
        self._order_safety_manager: SessionManager | None = None
        self._order_safety_gateway: OkxSpotOrderGateway | None = None
        self._order_safety_wal: IntentWAL | None = None
        self._order_safety_account_lock: AccountRiskPoolLock | None = None
        self._account_uid_verified = False
        self._release_account_lock_on_task_done = False
        self._order_safety_stopped = False
        self.order_safety_task: asyncio.Task | None = None
        self.order_safety_reason_code = "ORDER_SAFETY_NOT_INSTALLED"

    def _restore_order_safety(self) -> None:
        """Restore existing journals only; no synthetic session or balances are created."""
        directory = Path(self.config.recovery_state_dir)
        if (not directory.is_absolute() or not directory.is_dir()
                or not isinstance(self.config.recovery_account_uid, str)
                or not self.config.recovery_account_uid.isascii()
                or not self.config.recovery_account_uid.isdecimal()
                or not isinstance(self.config.recovery_reconciliation_max_age_ms, int)
                or isinstance(self.config.recovery_reconciliation_max_age_ms, bool)
                or self.config.recovery_reconciliation_max_age_ms <= 0):
            raise ValueError("ORDER_SAFETY_RECOVERY_CONFIG_INVALID")
        paths = [directory / name for name in (
            "session.json", "intents.json", "reservations.json", "cashflows.json")]
        if any(not path.is_file() or path.is_symlink() for path in paths):
            raise ValueError("ORDER_SAFETY_JOURNAL_MISSING")
        risk = self.config.strategy.risk
        if any(value is None for value in (
                risk.min_inventory_base, risk.max_inventory_base,
                risk.max_gross_quote, risk.max_net_base)):
            raise ValueError("ORDER_SAFETY_RISK_LIMITS_MISSING")
        limits = RiskLimits(risk.min_inventory_base, risk.max_inventory_base,
                            risk.max_gross_quote, risk.max_net_base)
        manager = SessionManager(SessionStore(paths[0]),
                                 wall_clock=lambda: datetime.now(timezone.utc),
                                 max_reconciliation_age_ms=(
                                     self.config.recovery_reconciliation_max_age_ms))
        wal = IntentWAL(paths[1])
        reservations = ReservationLedger.restore(paths[2], limits=limits)
        approvals = CashflowApprovals.load(paths[3])
        records = wal.all_records()
        if (len({record.client_order_id for record in records}) != len(records)
                or any(record.reservation_id != record.intent_id for record in records)
                or {record.reservation_id for record in records} != reservations.reservation_ids):
            raise ValueError("ORDER_SAFETY_JOURNALS_DISAGREE")
        if manager.current_session is None:
            raise ValueError("ORDER_SAFETY_SESSION_MISSING")
        connector = self.market_data_provider.get_connector_with_fallback(
            self.config.strategy.spot.connector)
        required = ("cancel_by_client_id", "get_order_by_client_id",
                    "cancel_by_exchange_order_id", "get_order_by_exchange_order_id",
                    "get_fills_by_exchange_order_id", "get_all_open_spot_orders_page",
                    "get_spot_order_history_page", "get_spot_fill_history_page",
                    "get_spot_cash_balances", "get_account_uid", "get_account_bills_page")
        if any(not callable(getattr(connector, name, None)) for name in required):
            raise ValueError("ORDER_SAFETY_CONNECTOR_UNAVAILABLE")
        reconciler = SpotReservationReconciler(wal, reservations, require_fees=True)
        bills = SpotBillReconciler(connector, reservations, wal, approvals)
        account = SpotAccountReconciler(connector, reservations, bills=bills)
        gateway = OkxSpotOrderGateway(
            connector, wal, trading_pair=self.config.strategy.spot.pair,
            clock=lambda: datetime.now(timezone.utc),
            apply_fills=reconciler.apply_fills, confirm_terminal=reconciler.confirm_terminal,
            on_cancel_requested=reconciler.request_cancel, on_unknown=reconciler.mark_unknown,
            account_check=account.check)
        account_ownership = AccountRiskPoolLock(
            self.config.recovery_account_uid, account_lock.ACCOUNT_LOCK_ROOT)
        account_ownership.acquire()
        try:
            self.install_order_safety(manager, gateway, wal)
        except BaseException:
            account_ownership.release()
            raise
        self._order_safety_account_lock = account_ownership

    def install_order_safety(self, manager: SessionManager, gateway: OkxSpotOrderGateway,
                             wal: IntentWAL) -> None:
        """Attach restored spot order state; this never enables order creation."""
        if (manager.current_session is None or gateway.wal is not wal
                or gateway.trading_pair != self.config.strategy.spot.pair
                or gateway.apply_fills is None or gateway.confirm_terminal is None
                or gateway.on_cancel_requested is None or gateway.on_unknown is None
                or gateway.account_check is None
                or gateway.scope_check is not None):
            raise ValueError("ORDER_SAFETY_RECOVERY_INCOMPLETE")
        self._order_safety_manager = manager
        self._order_safety_gateway = gateway
        self._order_safety_wal = wal
        self.order_safety_reason_code = "ORDER_SAFETY_READY_TO_RECONCILE"

    def update_config(self, new_config: LifeLiquidityConfig):
        if new_config.strategy != self.config.strategy:
            self.config_update_state.reject("CONFIG_UPDATE_UNSUPPORTED")
            raise ValueError("LIFE nested hot reload is unavailable until the controller gate is implemented")
        super().update_config(new_config)

    def on_config_load_failure(self):
        return self.config_update_state.reject("CONFIG_LOAD_FAILED")

    def trading_permissions_ready(self) -> bool:
        # P4 will replace this with the final order-permission checks.
        return False

    def on_safety_tick(self, timestamp: float) -> None:
        """Persist expiry and run order cancellation independent of quote readiness."""
        if self._order_safety_stopped:
            return
        manager = self._order_safety_manager
        if manager is None:
            if self.config.recovery_state_dir is not None:
                try:
                    self._restore_order_safety()
                except AccountLockUnavailable as exc:
                    self.order_safety_reason_code = str(exc)
                    return
                except Exception:
                    self.order_safety_reason_code = "ORDER_SAFETY_RECOVERY_FAILED"
                    self.logger().exception("LIFE order-safety recovery failed")
                    return
                manager = self._order_safety_manager
            else:
                return
        try:
            # This safety-only adapter has no quote permissions. It must pause
            # an active restored session until the later live gates are wired.
            manager.tick(reference_ready=False, all_gates_ready=False)
        except Exception:
            self.order_safety_reason_code = "SESSION_SAFETY_TICK_FAILED"
            self.logger().exception("LIFE safety session tick failed")
            return
        if self.order_safety_task is not None and not self.order_safety_task.done():
            return
        if manager.state not in ("PAUSED", "EXPIRED", "TRANSITIONING"):
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.order_safety_reason_code = "ORDER_SAFETY_LOOP_UNAVAILABLE"
            return
        self.order_safety_task = loop.create_task(self._cancel_and_reconcile_orders())
        self.order_safety_task.add_done_callback(self._on_order_safety_done)

    def _on_order_safety_done(self, task: asyncio.Task) -> None:
        if self._release_account_lock_on_task_done:
            self._release_account_lock()
        if task.cancelled():
            self.order_safety_reason_code = "ORDER_SAFETY_CANCELLED"
            return
        failure = task.exception()
        if failure is not None:
            self.order_safety_reason_code = "ORDER_SAFETY_FAILED"
            self.logger().error("LIFE safety order task failed", exc_info=(
                type(failure), failure, failure.__traceback__))

    async def _cancel_and_reconcile_orders(self) -> None:
        manager = self._order_safety_manager
        gateway = self._order_safety_gateway
        wal = self._order_safety_wal
        if self._order_safety_account_lock is not None and not self._account_uid_verified:
            try:
                observed_uid = await gateway.connector.get_account_uid()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.order_safety_reason_code = "ACCOUNT_UID_UNVERIFIED"
                self.logger().exception("LIFE account UID verification failed")
                return
            if observed_uid != self.config.recovery_account_uid:
                self.order_safety_reason_code = "ACCOUNT_UID_MISMATCH"
                return
            self._account_uid_verified = True
        current = manager.current_session
        scopes = {(record.session_id, record.epoch) for record in wal.all_records()
                  if record.state != "TERMINAL"}
        scopes.add((current.session_id, current.epoch))
        primary_result = None
        reason = "OLD_ORDERS_RECONCILED"
        for session_id, epoch in sorted(scopes):
            cancel_failed = False
            try:
                await gateway.request_cancel(session_id, epoch)
            except asyncio.CancelledError:
                raise
            except Exception:
                cancel_failed = True
                self.logger().exception("LIFE order cancellation failed")
            try:
                result = await gateway.reconcile(session_id, epoch)
            except asyncio.CancelledError:
                raise
            except Exception:
                reason = "RECONCILIATION_FETCH_FAILED"
                self.logger().exception("LIFE order reconciliation failed")
                continue
            if (manager.state == "TRANSITIONING" and session_id == current.session_id
                    and epoch == current.epoch):
                primary_result = result
            if not result.scope_complete:
                reason = "RECONCILIATION_INCOMPLETE"
            elif not result.trade_events_reconciled:
                reason = "OLD_FILLS_UNRECONCILED"
            elif (result.open_order_ids or result.pending_cancel_ids
                  or result.unknown_order_ids):
                reason = "CANCEL_REQUEST_FAILED" if cancel_failed else "OLD_ORDERS_UNRESOLVED"
        if manager.state == "TRANSITIONING" and primary_result is not None:
            try:
                manager.tick(reference_ready=False, all_gates_ready=False,
                             reconciliation=primary_result,
                             market_reference_ready=False)
            except Exception:
                reason = "SESSION_SAFETY_TICK_FAILED"
                self.logger().exception("LIFE transition reconciliation failed")
        if reason == "OLD_ORDERS_RECONCILED" and manager.state == "TRANSITIONING":
            reason = manager.reason_code
        self.order_safety_reason_code = reason

    def stop(self):
        self._order_safety_stopped = True
        super().stop()
        if self.order_safety_task is not None and not self.order_safety_task.done():
            self._release_account_lock_on_task_done = True
            self.order_safety_task.cancel()
        else:
            self._release_account_lock()
        if self._perpetual_poll_task is not None and not self._perpetual_poll_task.done():
            self._perpetual_poll_task.cancel()

    def _release_account_lock(self) -> None:
        if self._order_safety_account_lock is not None:
            self._order_safety_account_lock.release()
            self._order_safety_account_lock = None
            self._account_uid_verified = False
            self._release_account_lock_on_task_done = False

    def benchmark_connector(self):
        if self.benchmark_route is None:
            raise ValueError("BENCHMARK_SOURCE_NOT_CONFIGURED")
        return self.benchmark_route.resolve(self.market_data_provider)

    def allow_create_executor_actions(self) -> bool:
        return (self.config.strategy.spot.enabled and self.config_update_state.order_permission()
                and self.listing_gate.metadata_ready
                and self.book_ready and self._live_spot_book_ready()
                and self.continuous_gate.ready and self.snapshot_gate.permit()
                and self.continuity_gate.permit(self._book_feed_health(), self.snapshot_gate)
                and self.trading_permissions_ready())

    def _live_spot_book_ready(self) -> bool:
        try:
            connector = self.market_data_provider.get_connector_with_fallback(self.config.strategy.spot.connector)
            tracked_books = getattr(getattr(connector, "order_book_tracker", None), "order_books", None)
            if isinstance(tracked_books, dict) and self.config.strategy.spot.pair not in tracked_books:
                return False
        except (AttributeError, KeyError, OSError, TypeError, ValueError):
            return False
        return is_order_book_ready(connector, self.config.strategy.spot.pair)

    def _book_feed_health(self):
        try:
            connector = self.market_data_provider.get_connector_with_fallback(self.config.strategy.spot.connector)
            health = connector.order_book_tracker.data_source.book_feed_health(self.config.strategy.spot.pair)
            return health if isinstance(health, BookFeedHealth) else None
        except (AttributeError, KeyError, TypeError):
            return None

    async def control_task(self):
        self.book_ready = False
        self.snapshot_gate.reset()
        self.continuity_gate.reset()
        self.continuous_gate.reset()
        self._schedule_perpetual_listing_poll()
        if not self.config.strategy.spot.enabled:
            await self.update_processed_data()
            return
        source = OkxInstrumentSource(self.market_data_provider, self.config.strategy.spot.connector, "SPOT")
        await self.listing_gate.poll(self.listing_gate.clock(), source.fetch)
        if (self.listing_gate.metadata_ready and not self.spot_book_bootstrap_ready
                and self.snapshot_gate.clock() >= self.spot_book_bootstrap_next_at):
            try:
                self.spot_book_bootstrap_ready = bool(
                    await self.market_data_provider.initialize_order_book(
                        self.config.strategy.spot.connector, self.config.strategy.spot.pair))
            except asyncio.CancelledError:
                raise
            except Exception:
                self.spot_book_bootstrap_ready = False
            self.spot_book_bootstrap_reason_code = (
                "ORDER_BOOK_BOOTSTRAP_CONFIRMED" if self.spot_book_bootstrap_ready
                else "ORDER_BOOK_BOOTSTRAP_FAILED")
            if self.spot_book_bootstrap_ready:
                self.spot_book_bootstrap_failures = 0
            else:
                self.spot_book_bootstrap_failures += 1
                delay = min(BOOK_BOOTSTRAP_RETRY_MAX_SECONDS,
                            BOOK_BOOTSTRAP_RETRY_BASE_SECONDS
                            * 2 ** min(self.spot_book_bootstrap_failures - 1, 6))
                self.spot_book_bootstrap_next_at = self.snapshot_gate.clock() + delay
        if self.listing_gate.metadata_ready and self.spot_book_bootstrap_ready:
            try:
                connector = self.market_data_provider.get_connector_with_fallback(self.config.strategy.spot.connector)
                tracked_books = getattr(getattr(connector, "order_book_tracker", None), "order_books", None)
                if isinstance(tracked_books, dict) and self.config.strategy.spot.pair not in tracked_books:
                    self.spot_book_bootstrap_ready = False
                    self.spot_book_bootstrap_reason_code = "ORDER_BOOK_TRACKING_LOST"
                    self.spot_book_bootstrap_failures = 1
                    self.spot_book_bootstrap_next_at = (self.snapshot_gate.clock()
                                                        + BOOK_BOOTSTRAP_RETRY_BASE_SECONDS)
                else:
                    self.book_ready = is_order_book_ready(connector, self.config.strategy.spot.pair)
            except Exception:
                pass
        if self.book_ready:
            book_response = None
            received_monotonic = None
            request_started_monotonic = None
            try:
                request_started_monotonic = self.snapshot_gate.clock()
                book_response = await source.fetch_order_book(self.config.strategy.spot.pair)
                received_monotonic = self.snapshot_gate.clock()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.snapshot_gate.reason_code = "BOOK_FETCH_ERROR"
            try:
                time_response = await source.fetch_server_time()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.continuous_gate.reason_code = "EXCHANGE_TIME_FETCH_ERROR"
            else:
                self.continuous_gate.evaluate(self.listing_gate.instrument_info, time_response)
            if self.continuous_gate.ready and received_monotonic is not None:
                self.snapshot_gate.evaluate(
                    book_response, exchange_now_ms=self.continuous_gate.exchange_time_ms,
                    received_monotonic=received_monotonic, market_state=self.listing_gate.instrument_state,
                    continuous_trading_ready=True,
                )
                if self.snapshot_gate.permit():
                    self.continuity_gate.confirm(self._book_feed_health(), self.snapshot_gate.snapshot,
                                                 request_started_monotonic)
        await self.update_processed_data()
        if self.snapshot_gate.permit():
            await super().control_task()

    def _schedule_perpetual_listing_poll(self):
        if not self.config.strategy.perpetual.enabled:
            self.perpetual_contract = None
            self.perpetual_contract_reason_code = "PERPETUAL_DISABLED"
            return
        if self._perpetual_poll_task is not None and not self._perpetual_poll_task.done():
            return
        gate = self.perpetual_listing_gate
        if gate.clock() < gate.next_refresh_at:
            return
        # Revoke an old positive observation before the asynchronous fetch starts.
        gate.instrument_found = False
        gate.instrument_rules = None
        gate.instrument_state = None
        gate.instrument_info = None
        self.perpetual_contract = None
        self.perpetual_contract_reason_code = "SWAP_METADATA_PENDING"
        self._perpetual_poll_task = asyncio.create_task(self._poll_perpetual_listing_and_publish())

    async def _poll_perpetual_listing_and_publish(self):
        try:
            await self._poll_perpetual_listing()
            await self.update_processed_data()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.perpetual_contract = None
            self.perpetual_listing_gate.instrument_found = False
            self.perpetual_listing_gate.instrument_rules = None
            self.perpetual_listing_gate.next_refresh_at = (
                self.perpetual_listing_gate.clock() + self.perpetual_listing_gate.base_delay)
            self.perpetual_contract_reason_code = "SWAP_METADATA_FETCH_ERROR"
            self.logger().exception("LIFE SWAP metadata poll failed")
            await self.update_processed_data()

    async def _poll_perpetual_listing(self):
        self.perpetual_contract = None
        if not self.config.strategy.perpetual.enabled:
            self.perpetual_contract_reason_code = "PERPETUAL_DISABLED"
            return
        source = OkxInstrumentSource(self.market_data_provider,
                                     self.config.strategy.perpetual.connector, "SWAP")
        await self.perpetual_listing_gate.poll(self.perpetual_listing_gate.clock(), source.fetch)
        self.perpetual_contract_reason_code = self.perpetual_listing_gate.reason_code
        if self.perpetual_listing_gate.instrument_found:
            try:
                self.perpetual_contract = LinearSwapContract.from_okx(
                    self.perpetual_listing_gate.instrument_info,
                    self.config.strategy.perpetual.pair,
                )
            except ContractValidationError as exc:
                self.perpetual_contract_reason_code = exc.reason_code
            else:
                if self.perpetual_listing_gate.instrument_state == "live":
                    self.perpetual_contract_reason_code = "SWAP_METADATA_CONFIRMED"

    async def update_processed_data(self):
        if not self.config.strategy.spot.enabled:
            reason = "SPOT_DISABLED"
        elif not self.listing_gate.metadata_ready:
            reason = self.listing_gate.reason_code
        elif not self.spot_book_bootstrap_ready:
            reason = self.spot_book_bootstrap_reason_code
        elif not self.book_ready:
            reason = "ORDER_BOOK_NOT_READY"
        elif not self.continuous_gate.ready:
            reason = self.continuous_gate.reason_code
        elif not self.snapshot_gate.permit():
            reason = self.snapshot_gate.reason_code
        else:
            reason = self.continuity_gate.reason_code
        snapshot = self.snapshot_gate.snapshot
        contract = self.perpetual_contract
        self.processed_data = {
            "state": self.listing_gate.state,
            "reason_code": reason,
            "instrument_found": self.listing_gate.instrument_found,
            "metadata_ready": self.listing_gate.metadata_ready,
            "spot_book_bootstrap_ready": self.spot_book_bootstrap_ready,
            "book_ready": self.book_ready,
            "continuous_trading_ready": self.continuous_gate.ready,
            "book_snapshot_ready": self.snapshot_gate.permit(),
            "book_continuity_ready": self.continuity_gate.permit(self._book_feed_health(), self.snapshot_gate),
            "spot_market_data_ready": bool(
                self.config.strategy.spot.enabled and self.listing_gate.metadata_ready
                and self.book_ready and self.continuous_gate.ready
                and self.continuity_gate.permit(self._book_feed_health(), self.snapshot_gate)),
            "perpetual_contract_listed": bool(
                self.config.strategy.perpetual.enabled and self.perpetual_listing_gate.instrument_found),
            "perpetual_metadata_ready": bool(
                self.config.strategy.perpetual.enabled and self.perpetual_listing_gate.metadata_ready
                and self.perpetual_contract is not None),
            "perpetual_contract_value_life": str(contract.contract_value_life) if contract else None,
            "perpetual_min_order_life": str(contract.minimum_order_life) if contract else None,
            "perpetual_order_step_life": str(contract.order_step_life) if contract else None,
            "perpetual_market_state": (self.perpetual_listing_gate.instrument_state
                                       if self.config.strategy.perpetual.enabled else None),
            "perpetual_reason_code": self.perpetual_contract_reason_code,
            "perpetual_quote_ready": False,
            "order_safety_reason_code": self.order_safety_reason_code,
            "last_book_exchange_timestamp_ms": snapshot.exchange_timestamp_ms if snapshot else None,
            "last_book_received_monotonic": snapshot.received_monotonic if snapshot else None,
            "last_book_source": snapshot.data_source if snapshot else None,
        }

    def determine_executor_actions(self):
        return []

    def to_format_status(self):
        reason = self.processed_data.get("reason_code", self.listing_gate.reason_code)
        if self.snapshot_gate.ready and not self.snapshot_gate.permit():
            reason = "BOOK_STALE"
        elif self.snapshot_gate.permit() and not self.continuity_gate.permit(
                self._book_feed_health(), self.snapshot_gate):
            reason = self.continuity_gate.reason_code
        return [f"LIFE liquidity: {self.listing_gate.state} ({reason}); "
                f"order safety: {self.order_safety_reason_code}; "
                "trading is disabled pending P2–P9 gates."]
