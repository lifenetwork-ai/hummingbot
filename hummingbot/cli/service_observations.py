"""Optional runtime observations and their JSON presentation, without control logic."""


class ServiceObservations:
    def __init__(self):
        self.reporting = {}
        self.drawdown = None
        self.outflow = None

    @staticmethod
    def _risk_view(reader, model, availability, *, scope, reason, operator=False):
        from sqlalchemy.exc import SQLAlchemyError

        from hummingbot.core.bot_ownership import OwnershipLost
        try:
            value = reader()
            view = model.model_validate(value).model_dump(mode="json") if value is not None else None
        except (OwnershipLost, SQLAlchemyError):
            raise
        except Exception as error:
            availability.fail(scope, reason, error, operator=operator)
            return None, False
        if operator and view is not None and view.get("reason_code") == reason:
            availability.fail(scope, reason, operator=True)
            return view, False
        availability.clear(scope)
        return view, True

    def capture_checkpoint(self, kind, reader, availability):
        from hummingbot.cli.admin_models import DrawdownStatus, SpotOutflowStatus
        attribute, model = {"spot_outflow": ("outflow", SpotOutflowStatus),
                            "drawdown": ("drawdown", DrawdownStatus)}[kind]
        reason = kind + "_checkpoint_invalid"
        view, valid = self._risk_view(reader, model, availability, scope=kind + "_checkpoint",
                                      reason=reason, operator=True)
        setattr(self, attribute, view)
        return valid

    def capture_drawdown(self, strategy, availability):
        from hummingbot.cli.admin_models import DrawdownStatus
        self.drawdown, _ = self._risk_view(lambda: self._drawdown_view(strategy), DrawdownStatus, availability,
                                           scope="drawdown_reporting", reason="drawdown_observation_unavailable")
        return self.drawdown

    def _drawdown_view(self, strategy):
        if strategy is not None and isinstance(getattr(getattr(strategy, "config", None), "mm_drawdown_scope", None), dict):
            return strategy.mm_drawdown_status()
        if strategy is None and self.drawdown is not None:
            return {**self.drawdown, "ready": False, "stale": True,
                    "global_pnl_quote": None, "global_drawdown_quote": None,
                    "reason_code": self.drawdown.get("reason_code") or "drawdown_not_running",
                    "controllers": {identity: {**value, "pnl_quote": None, "drawdown_quote": None}
                                    for identity, value in self.drawdown["controllers"].items()}}
        return self.drawdown

    def capture(self, core, owner, availability):
        from hummingbot.cli.admin_models import SpotOutflowStatus
        from hummingbot.cli.admin_observations import capture_reporting
        from hummingbot.core.spot_outflow_guard import SpotOutflowGuard
        outflow = getattr(owner, "spot_outflow", None)
        self.outflow, _ = self._risk_view(
            outflow.status if isinstance(outflow, SpotOutflowGuard) else lambda: self.outflow,
            SpotOutflowStatus, availability, scope="spot_outflow_reporting", reason="spot_outflow_observation_unavailable")
        strategy = core.strategy
        self.capture_drawdown(strategy, availability)
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
                "mm_drawdown": self.drawdown,
                "spot_outflow": self.outflow,
                "reporting": self.reporting}
