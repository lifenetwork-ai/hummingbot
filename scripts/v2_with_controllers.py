import asyncio
import os
from decimal import Decimal
from typing import Dict, List, Optional

from pydantic import Field, model_validator

from hummingbot.client.hummingbot_application import HummingbotApplication
from hummingbot.connector.connector_base import ConnectorBase
from hummingbot.core.event.events import MarketOrderFailureEvent
from hummingbot.core.mm_drawdown import MMDrawdownMarket, drawdown_policy
from hummingbot.core.spot_outflow import SpotOutflowPolicy
from hummingbot.core.utils.read_retry import retry_delay, transient_read
from hummingbot.strategy.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction, StopExecutorAction


class V2WithControllersConfig(StrategyV2ConfigBase):
    script_file_name: str = os.path.basename(__file__)
    # Legacy zero/negative settings retain their old behavior. The managed MM
    # adapter validates finite positive Decimal limits when its scope is present.
    max_global_drawdown_quote: Optional[Decimal] = Field(default=None, allow_inf_nan=True)
    max_controller_drawdown_quote: Optional[Decimal] = Field(default=None, allow_inf_nan=True)
    mm_drawdown_scope: Optional[Dict[str, MMDrawdownMarket]] = None
    mm_spot_outflow_policy: Optional[SpotOutflowPolicy] = None
    mm_drawdown_max_data_age_seconds: Decimal = Field(default=Decimal("10"), gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_mm_drawdown(self):
        if self.mm_drawdown_scope is not None:
            limits = (self.max_global_drawdown_quote, self.max_controller_drawdown_quote)
            if (not self.mm_drawdown_scope or len(self.mm_drawdown_scope) > 2 or all(value is None for value in limits)
                    or any(value is not None and (not value.is_finite() or value <= 0) for value in limits)):
                raise ValueError("Managed MM drawdown requires a bounded scope and positive finite limits")
            if any(not identity.strip() for identity in self.mm_drawdown_scope):
                raise ValueError("Drawdown requires stable controller identities")
            if len({(market.connector_name, market.trading_pair) for market in self.mm_drawdown_scope.values()}) != len(self.mm_drawdown_scope):
                raise ValueError("Drawdown market identities must be unique")
            if any(value.get("initial_positions") for value in self.controller_config_overrides or []):
                raise ValueError("Managed drawdown restores recorded exposure; initial position overrides are unsupported")
        return self


class V2WithControllers(StrategyV2Base):
    """
    This script runs a generic strategy with cash out feature. Will also check if the controllers configs have been
    updated and apply the new settings.
    The cash out of the script can be set by the time_to_cash_out parameter in the config file. If set, the script will
    stop the controllers after the specified time has passed, and wait until the active executors finalize their
    execution.
    The controllers will also have a parameter to manually cash out. In that scenario, the main strategy will stop the
    specific controller and wait until the active executors finalize their execution. The rest of the executors will
    wait until the main strategy stops them.
    """
    performance_report_interval: int = 1

    def __init__(self, connectors: Dict[str, ConnectorBase], config: V2WithControllersConfig):
        if config.mm_drawdown_scope or config.mm_spot_outflow_policy:
            from hummingbot.client.config.runtime_config import postgres_requested
            if not postgres_requested():
                raise ValueError("Managed MM drawdown requires PostgreSQL service ownership")
        super().__init__(connectors, config)
        self.config = config
        self.max_pnl_by_controller = {}
        self.max_global_pnl = Decimal("0")
        self.drawdown_exited_controllers = []
        self.closed_executors_buffer: int = 30
        self._last_performance_report_timestamp = 0
        self.mm_drawdown_checkpoint_callback = None
        self._mm_drawdown_last_checkpoint = self._mm_drawdown_state = self._mm_drawdown_breach = None
        self._mm_drawdown_bound = False
        self.mm_valuation_task = None
        if config.mm_drawdown_scope:
            self.mm_valuation_task = asyncio.create_task(self.initialize_mm_valuations())

    def on_tick(self):
        super().on_tick()
        if not self._is_stop_triggered:
            if self.config.mm_drawdown_scope:
                self.control_max_drawdown()
                if not self._is_stop_triggered:
                    self.check_manual_kill_switch()
            else:
                self.check_manual_kill_switch()
                self.control_max_drawdown()
            self.send_performance_report()

    def control_max_drawdown(self):
        if self.config.mm_drawdown_scope:
            self._control_mm_drawdown()
            return
        if self.config.max_controller_drawdown_quote:
            self.check_max_controller_drawdown()
        if self.config.max_global_drawdown_quote:
            self.check_max_global_drawdown()

    def mm_drawdown_policy(self):
        return drawdown_policy(self.config.mm_drawdown_scope, self.config.max_global_drawdown_quote,
                               self.config.max_controller_drawdown_quote)

    def mm_drawdown_checkpoint(self):
        return dict(mm_drawdown_policy=self.mm_drawdown_policy(),
                    max_global_pnl=str(self.max_global_pnl),
                    max_pnl_by_controller={identity: str(self.max_pnl_by_controller.get(identity, Decimal("0")))
                                           for identity in self.config.mm_drawdown_scope},
                    _is_stop_triggered=self._is_stop_triggered,
                    drawdown_exited_controllers=list(self.drawdown_exited_controllers),
                    mm_drawdown_breach=self._mm_drawdown_breach)

    def _mm_report_values(self):
        now = self.market_data_provider.time()
        if getattr(self.executor_orchestrator, "mm_scope_incomplete", False):
            raise ValueError("drawdown_position_scope_unavailable")
        stamp = getattr(self, "controller_reports_timestamp", None)
        if (not isinstance(stamp, (int, float)) or not -1 <= now - stamp <= 3
                or getattr(self, "controller_reports_error", None) is not None):
            raise ValueError("drawdown_report_unavailable")
        if not self.executor_orchestrator.mm_valuation_ready():
            raise ValueError("drawdown_valuation_unavailable")
        values = {}
        for identity in self.config.mm_drawdown_scope:
            report = self.controller_reports.get(identity, {}).get("performance")
            if report is None:
                raise ValueError("drawdown_report_unavailable")
            value = Decimal(report.global_pnl_quote)
            if not value.is_finite():
                raise ValueError("drawdown_report_unavailable")
            values[identity] = value
        return values

    def mm_drawdown_status(self):
        state = dict(self._mm_drawdown_state or {})
        state.update(enabled=True, metric="covered_v2_report_drawdown", scope=self.mm_drawdown_policy()["scope"],
                     max_global_drawdown_quote=self.config.max_global_drawdown_quote,
                     max_controller_drawdown_quote=self.config.max_controller_drawdown_quote,
                     global_peak_quote=self.max_global_pnl,
                     controllers={identity: dict(peak_quote=self.max_pnl_by_controller.get(identity, Decimal("0")),
                                                 pnl_quote=(state.get("controller_pnl") or {}).get(identity),
                                                 drawdown_quote=(self.max_pnl_by_controller.get(identity, Decimal("0")) - state["controller_pnl"][identity]
                                                                 if identity in (state.get("controller_pnl") or {}) else None))
                                  for identity in self.config.mm_drawdown_scope},
                     fees="executor_and_held_trade_quote_fees", funding="excluded")
        state.setdefault("ready", False)
        state.setdefault("reason_code", "drawdown_report_unavailable")
        state.setdefault("observed_at", None)
        state.setdefault("global_pnl_quote", None)
        state.setdefault("global_drawdown_quote", None)
        state.setdefault("breached_controller_id", None)
        state.setdefault("stale", True)
        state.setdefault("operator_action_required", False)
        if self._mm_drawdown_breach:
            state.update(ready=False, reason_code=self._mm_drawdown_breach["reason_code"],
                         breached_controller_id=self._mm_drawdown_breach.get("controller_id"), operator_action_required=True)
        try:
            self._mm_report_values()
            observed = state.get("observed_at")
            if (not isinstance(observed, (int, float))
                    or not -1 <= self.market_data_provider.time() - observed <= 3):
                raise ValueError("drawdown_report_unavailable")
        except (ValueError, ArithmeticError) as error:
            state.update(ready=False, stale=True, global_pnl_quote=None, global_drawdown_quote=None)
            for value in state["controllers"].values():
                value["pnl_quote"] = None
                value["drawdown_quote"] = None
            if not self._is_stop_triggered:
                state["reason_code"] = str(error) if str(error) in {"drawdown_report_unavailable", "drawdown_valuation_unavailable", "drawdown_position_scope_unavailable"} else "drawdown_report_unavailable"
                if str(error) == "drawdown_position_scope_unavailable":
                    state["operator_action_required"] = True
        if not self._mm_drawdown_bound:
            state.update(ready=False, reason_code="drawdown_checkpoint_unavailable")
        if self._is_stop_triggered:
            state["ready"] = False
        state.pop("controller_pnl", None)
        return state

    def mm_drawdown_ready(self):
        return self.mm_drawdown_status()["ready"]

    def _halt_mm_drawdown(self, reason):
        self._is_stop_triggered = True
        self.drawdown_exited_controllers = list(self.config.mm_drawdown_scope)
        for controller in self.controllers.values():
            controller._session_admitted = False
        HummingbotApplication.main_application().stop(mm_drawdown_reason=reason)

    def _control_mm_drawdown(self):
        if self._is_stop_triggered:
            return
        if not self._mm_drawdown_bound and self.mm_drawdown_checkpoint_callback is None:
            self._mm_drawdown_state = dict(ready=False, stale=True, reason_code="drawdown_checkpoint_unavailable")
            return
        try:
            values = self._mm_report_values()
        except (ValueError, ArithmeticError) as error:
            self._mm_drawdown_state = dict(ready=False, stale=True, global_pnl_quote=None, global_drawdown_quote=None,
                                           reason_code=str(error) if str(error) in {"drawdown_report_unavailable", "drawdown_valuation_unavailable", "drawdown_position_scope_unavailable"} else "drawdown_report_unavailable",
                                           operator_action_required=str(error) == "drawdown_position_scope_unavailable")
            return
        total = sum(values.values(), Decimal("0"))
        self.max_global_pnl = max(self.max_global_pnl, total)
        for identity, value in values.items():
            self.max_pnl_by_controller[identity] = max(self.max_pnl_by_controller.get(identity, Decimal("0")), value)
        reason, breached = None, None
        if self.config.max_controller_drawdown_quote is not None:
            breached = next((identity for identity, value in values.items()
                             if self.max_pnl_by_controller[identity] - value > self.config.max_controller_drawdown_quote), None)
            if breached:
                reason = "drawdown_controller_limit"
        if self.config.max_global_drawdown_quote is not None and self.max_global_pnl - total > self.config.max_global_drawdown_quote:
            reason = "drawdown_global_limit"
        if reason:
            self._is_stop_triggered = True
            self.drawdown_exited_controllers = list(self.config.mm_drawdown_scope)
            self._mm_drawdown_breach = dict(reason_code=reason, controller_id=breached)
            for controller in self.controllers.values():
                controller._session_admitted = False
        self._mm_drawdown_state = dict(ready=reason is None, stale=False, reason_code=reason,
                                       observed_at=self.controller_reports_timestamp, global_pnl_quote=total,
                                       global_drawdown_quote=self.max_global_pnl - total, controller_pnl=values,
                                       breached_controller_id=breached, operator_action_required=reason is not None)
        checkpoint = self.mm_drawdown_checkpoint()
        if checkpoint != self._mm_drawdown_last_checkpoint:
            if self.mm_drawdown_checkpoint_callback is None:
                self._mm_drawdown_state.update(ready=False, reason_code="drawdown_checkpoint_unavailable")
                return
            try:
                self.mm_drawdown_checkpoint_callback(checkpoint)
            except Exception:
                self._mm_drawdown_state.update(ready=False, reason_code="drawdown_checkpoint_unavailable", operator_action_required=True)
                self._halt_mm_drawdown("drawdown_checkpoint_unavailable")
                raise
            self._mm_drawdown_bound = True
            self._mm_drawdown_last_checkpoint = checkpoint
        if reason:
            self._halt_mm_drawdown(reason)

    async def initialize_mm_valuations(self):
        required = {(position.connector_name, position.trading_pair)
                    for positions in self.executor_orchestrator.positions_held.values() for position in positions
                    if position.net_amount_base and position.connector_name not in self.connectors}
        for connector, pair in sorted(required):
            attempt = 0
            while True:
                try:
                    source = self.market_data_provider.get_connector_with_fallback(connector)
                    if not source.is_trading_required:
                        await asyncio.wait_for(source._update_trading_rules(), timeout=30)
                    result = await asyncio.wait_for(self.market_data_provider.acquire_order_books(connector, [pair], self), timeout=30)
                    if not result.get(pair):
                        raise OSError("Retained drawdown valuation is not available")
                except Exception as error:
                    if not transient_read(error):
                        return  # Status remains explicitly unavailable; no execution task is restarted.
                    attempt = min(attempt + 1, 60)
                    await self.market_data_provider.release_order_books(connector, [pair], self)
                    await asyncio.sleep(retry_delay(attempt, error))
                else:
                    break

    async def on_stop(self, **kwargs):
        if self.mm_valuation_task is not None and not self.mm_valuation_task.done():
            self.mm_valuation_task.cancel()
            await asyncio.gather(self.mm_valuation_task, return_exceptions=True)
        await super().on_stop(**kwargs)

    def check_max_controller_drawdown(self):
        for controller_id, controller in self.controllers.items():
            if controller.status != RunnableStatus.RUNNING:
                continue
            controller_pnl = self.get_performance_report(controller_id).global_pnl_quote
            last_max_pnl = self.max_pnl_by_controller[controller_id]
            if controller_pnl > last_max_pnl:
                self.max_pnl_by_controller[controller_id] = controller_pnl
            else:
                current_drawdown = last_max_pnl - controller_pnl
                if current_drawdown > self.config.max_controller_drawdown_quote:
                    self.logger().info(f"Controller {controller_id} reached max drawdown. Stopping the controller.")
                    controller.stop()
                    executors_order_placed = self.filter_executors(
                        executors=self.get_executors_by_controller(controller_id),
                        filter_func=lambda x: x.is_active and not x.is_trading,
                    )
                    self.executor_orchestrator.execute_actions(
                        actions=[StopExecutorAction(controller_id=controller_id, executor_id=executor.id) for executor in executors_order_placed]
                    )
                    self.drawdown_exited_controllers.append(controller_id)

    def check_max_global_drawdown(self):
        current_global_pnl = sum([self.get_performance_report(controller_id).global_pnl_quote for controller_id in self.controllers.keys()])
        if current_global_pnl > self.max_global_pnl:
            self.max_global_pnl = current_global_pnl
        else:
            current_global_drawdown = self.max_global_pnl - current_global_pnl
            if current_global_drawdown > self.config.max_global_drawdown_quote:
                self.drawdown_exited_controllers.extend(list(self.controllers.keys()))
                self.logger().info("Global drawdown reached. Stopping the strategy.")
                self._is_stop_triggered = True
                HummingbotApplication.main_application().stop()

    def get_controller_report(self, controller_id: str) -> dict:
        """
        Get the full report for a controller including performance and custom info.
        """
        performance_report = self.controller_reports.get(controller_id, {}).get("performance")
        return {
            "performance": performance_report.dict() if performance_report else {},
            "custom_info": self.controllers[controller_id].get_custom_info()
        }

    def send_performance_report(self):
        if self.current_timestamp - self._last_performance_report_timestamp >= self.performance_report_interval and self._pub:
            controller_reports = {controller_id: self.get_controller_report(controller_id) for controller_id in self.controllers.keys()}
            self._pub(controller_reports)
            self._last_performance_report_timestamp = self.current_timestamp

    def check_manual_kill_switch(self):
        for controller_id, controller in self.controllers.items():
            if controller.config.manual_kill_switch and controller.status == RunnableStatus.RUNNING:
                self.logger().info(f"Manual cash out for controller {controller_id}.")
                controller.stop()
                executors_to_stop = self.get_executors_by_controller(controller_id)
                self.executor_orchestrator.execute_actions(
                    [StopExecutorAction(executor_id=executor.id,
                                        controller_id=executor.controller_id) for executor in executors_to_stop])
            if not controller.config.manual_kill_switch and controller.status == RunnableStatus.TERMINATED:
                if getattr(controller, "_control_error", None):
                    from hummingbot.client.config.runtime_config import postgres_requested
                    if postgres_requested():
                        continue  # The service handles failed tasks; preserve file-mode behavior.
                if controller_id in self.drawdown_exited_controllers:
                    continue
                self.logger().info(f"Restarting controller {controller_id}.")
                controller.start()

    def check_executors_status(self):
        active_executors = self.filter_executors(
            executors=self.get_all_executors(),
            filter_func=lambda executor: executor.status == RunnableStatus.RUNNING
        )
        if not active_executors:
            self.logger().info("All executors have finalized their execution. Stopping the strategy.")
            HummingbotApplication.main_application().stop()
        else:
            non_trading_executors = self.filter_executors(
                executors=active_executors,
                filter_func=lambda executor: not executor.is_trading
            )
            self.executor_orchestrator.execute_actions(
                [StopExecutorAction(executor_id=executor.id,
                                    controller_id=executor.controller_id) for executor in non_trading_executors])

    def create_actions_proposal(self) -> List[CreateExecutorAction]:
        return []

    def stop_actions_proposal(self) -> List[StopExecutorAction]:
        return []

    def apply_initial_setting(self):
        connectors_position_mode = {}
        for controller_id, controller in self.controllers.items():
            self.max_pnl_by_controller.setdefault(controller_id, Decimal("0"))
            config_dict = controller.config.model_dump()
            if "connector_name" in config_dict:
                if self.is_perpetual(config_dict["connector_name"]):
                    if "position_mode" in config_dict:
                        connectors_position_mode[config_dict["connector_name"]] = config_dict["position_mode"]
                    if "leverage" in config_dict and "trading_pair" in config_dict:
                        self.connectors[config_dict["connector_name"]].set_leverage(
                            leverage=config_dict["leverage"],
                            trading_pair=config_dict["trading_pair"])
        for connector_name, position_mode in connectors_position_mode.items():
            self.connectors[connector_name].set_position_mode(position_mode)

    def did_fail_order(self, order_failed_event: MarketOrderFailureEvent):
        """
        Handle order failure events by logging the error and stopping the strategy if necessary.
        """
        if order_failed_event.error_message and "position side" in order_failed_event.error_message.lower():
            connectors_position_mode = {}
            for controller_id, controller in self.controllers.items():
                config_dict = controller.config.model_dump()
                if "connector_name" in config_dict:
                    if self.is_perpetual(config_dict["connector_name"]):
                        if "position_mode" in config_dict:
                            connectors_position_mode[config_dict["connector_name"]] = config_dict["position_mode"]
            for connector_name, position_mode in connectors_position_mode.items():
                self.connectors[connector_name].set_position_mode(position_mode)
