"""Optional runtime observations and their JSON presentation, without control logic."""


class ServiceObservations:
    def __init__(self):
        self.reporting = {}
        self.drawdown = None
        self.outflow = None

    def capture(self, core, owner, availability):
        from hummingbot.cli.admin_models import DrawdownStatus, SpotOutflowStatus
        from hummingbot.cli.admin_observations import capture_reporting
        from hummingbot.core.spot_outflow_guard import SpotOutflowGuard
        outflow = getattr(owner, "spot_outflow", None)
        if isinstance(outflow, SpotOutflowGuard):
            self.outflow = outflow.status()
        strategy = core.strategy
        if strategy is not None and isinstance(getattr(getattr(strategy, "config", None), "mm_drawdown_scope", None), dict):
            self.drawdown = strategy.mm_drawdown_status()
        elif strategy is None and self.drawdown is not None:
            self.drawdown = {**self.drawdown, "ready": False, "stale": True,
                             "global_pnl_quote": None, "global_drawdown_quote": None,
                             "reason_code": self.drawdown.get("reason_code") or "drawdown_not_running",
                             "controllers": {identity: {**value, "pnl_quote": None, "drawdown_quote": None}
                                             for identity, value in self.drawdown["controllers"].items()}}
        try:
            engine_status = core.get_status()
            availability.clear("engine_reporting")
        except Exception as error:
            availability.fail("engine_reporting", "engine_reporting_unavailable", error)
            # Receipt completion still needs known booleans. Missing values stay
            # unknown rather than pretending a failed projection proves stop.
            engine_status = {key: value if isinstance(value, bool) else None for key, value in (
                ("strategy_running", getattr(core, "_strategy_running", None)),
                ("clock_running", getattr(core, "_is_running", None)))}
        try:
            reporting = capture_reporting(core, self.reporting)
            balances = {name: {asset: str(value) for asset, value in connector.get_all_balances().items()}
                        for name, connector in core.connector_manager.connectors.items()}
            self.reporting = reporting
            availability.clear("reporting")
            reporting_unavailable = False
        except Exception as error:
            availability.fail("reporting", "reporting_unavailable", error)
            balances, reporting_unavailable = {}, True
        return {"engine": engine_status, "balances": balances, "reporting_unavailable": reporting_unavailable,
                "mm_drawdown": DrawdownStatus.model_validate(self.drawdown).model_dump(mode="json") if self.drawdown is not None else None,
                "spot_outflow": SpotOutflowStatus.model_validate(self.outflow).model_dump(mode="json") if self.outflow is not None else None,
                "reporting": self.reporting}
