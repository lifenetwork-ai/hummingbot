"""LIFE V2 controller adapter for configuration discovery and offline validation.

The trading controller is deliberately inert until the P2–P9 data, accounting,
order, and release gates are implemented. Loading this module cannot place orders.
"""

import asyncio
from decimal import Decimal
from typing import Literal

from pydantic import Field

from hummingbot.connector.exchange.okx.okx_book_health import BookFeedHealth
from hummingbot.strategy_v2.controllers.controller_base import ControllerBase, ControllerConfigBase
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
        """P2 runner integration hook; P4.15 implements actual safety actions."""
        pass

    def stop(self):
        super().stop()
        if self._perpetual_poll_task is not None and not self._perpetual_poll_task.done():
            self._perpetual_poll_task.cancel()

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
                "trading is disabled pending P2–P9 gates."]
