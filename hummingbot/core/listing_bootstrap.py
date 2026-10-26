"""Owner checkpoint and final-send adapter for the existing MM listing guard."""
import math
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.strategy_v2.utils.quote_guard import QuoteGuardConfig


class BootstrapMarketPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    connector_name: Literal["okx", "okx_perpetual"]
    trading_pair: str = Field(pattern=r"^[A-Z0-9][A-Z0-9._]*-USDT$")
    initial_anchor: Decimal = Field(gt=0, allow_inf_nan=False)
    min_price: Decimal = Field(gt=0, allow_inf_nan=False)
    max_price: Decimal = Field(gt=0, allow_inf_nan=False)
    max_data_age_seconds: Decimal = Field(gt=0, le=30, allow_inf_nan=False)
    quote_guards: QuoteGuardConfig

    @model_validator(mode="after")
    def explicit_bootstrap(self):
        if (self.quote_guards.mode != "bootstrap_then_market" or not self.quote_guards.enabled
                or not self.min_price <= self.initial_anchor <= self.max_price):
            raise ValueError("Bootstrap requires complete guarded market policy and bounded anchor")
        return self


class BootstrapEpisode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    started_at: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    expires_at: Optional[float] = Field(default=None, gt=0, allow_inf_nan=False)
    market_qualified: bool = Field(default=False, strict=True)
    qualified_at: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if ((self.started_at is None) != (self.expires_at is None)
                or (self.started_at is not None and self.expires_at <= self.started_at)
                or (self.market_qualified != (self.qualified_at is not None))):
            raise ValueError("Invalid bootstrap episode checkpoint")
        return self


def canonical_policy(value):
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    if isinstance(value, dict):
        return {key: canonical_policy(item) for key, item in value.items()}
    return value


class ListingBootstrapGuard:
    """Small owner adapter; the existing recovery row is the only checkpoint."""

    def __init__(self, owner, policies, controllers):
        self.owner = owner
        self.policies = {key: canonical_policy(BootstrapMarketPolicy.model_validate(value).model_dump())
                         for key, value in policies.items()}
        if not 1 <= len(self.policies) <= 2:
            raise ValueError("Bootstrap requires one or two stable market identities")
        self.controllers = controllers
        self.frame = None
        self.deadlines = {}

    def _validated(self, state):
        from copy import deepcopy
        frame = deepcopy(state.get("listing_bootstrap"))
        if (not frame or frame.get("policies") != self.policies
                or set(frame.get("episodes", {})) != set(self.policies)
                or self.owner.spot_outflow is None
                or frame.get("binding") != self.owner.spot_outflow.identity()):
            raise SubmissionBlocked("bootstrap_binding_mismatch")
        for key, value in frame["episodes"].items():
            episode = BootstrapEpisode.model_validate(value)
            duration = self.policies[key]["quote_guards"]["bootstrap"]["duration_seconds"]
            if episode.started_at is not None and episode.expires_at - episode.started_at > duration + .000001:
                raise SubmissionBlocked("bootstrap_checkpoint_invalid")
        return frame

    def attach(self):
        for key, controller in self.controllers.items():
            if key in self.policies:
                controller.restore_bootstrap_episode(self.frame["episodes"][key] if self.frame else None)

    def bind_if_ready(self):
        if self.frame is not None:
            self.attach()
            return True
        outflow = self.owner.spot_outflow
        if outflow is None or outflow.binding is None:
            return False
        row = self.owner.check_owner()
        state = row["recovery_state"] or {}
        if state.get("listing_bootstrap") is None:
            from copy import deepcopy
            frame = dict(binding=dict(outflow.binding), policies=self.policies,
                         episodes={key: BootstrapEpisode().model_dump(mode="json") for key in self.policies})

            def bind(current):
                if current.get("halt") or current.get("listing_bootstrap") not in (None, frame):
                    raise SubmissionBlocked("bootstrap binding changed during admission")
                current["listing_bootstrap"] = deepcopy(frame)
            if not self.owner._mutate_recovery(bind, expected_version=self.owner.version):
                return False
            state = {"listing_bootstrap": frame}
        try:
            self.frame = self._validated(state)
        except (ValueError, SubmissionBlocked):
            self.owner.require_recovery("bootstrap_binding_mismatch")
            raise SubmissionBlocked("bootstrap_binding_mismatch") from None
        self.attach()
        return True

    def sync_qualification(self):
        if not self.bind_if_ready():
            return False
        pending = []
        for key, controller in self.controllers.items():
            if key in self.policies:
                controller.update_reference()
                if controller.processed_data.get("quote_guard", {}).get("qualification_pending"):
                    pending.append(key)
        if pending:
            row = self.owner.check_owner()

            def qualify(state):
                if state.get("halt"):
                    raise SubmissionBlocked("Recovery HALT blocks qualification")
                frame = self._validated(state)
                for key in pending:
                    if not frame["episodes"][key]["market_qualified"]:
                        frame["episodes"][key].update(market_qualified=True, qualified_at=float(row["database_now"]))
                state["listing_bootstrap"] = frame
            if not self.owner._mutate_recovery(qualify, expected_version=self.owner.version):
                return False
            self.refresh()
        return True

    def refresh(self):
        self.frame = self._validated(self.owner.check_owner()["recovery_state"] or {})
        self.attach()

    def needs_start(self):
        return self.frame is not None and any(
            key in self.controllers and value["started_at"] is None and not value["market_qualified"]
            for key, value in self.frame["episodes"].items())

    def start_admission(self, recovery, session, now):
        frame = self._validated(recovery)
        if recovery.get("halt") or session.status != "running":
            raise SubmissionBlocked("bootstrap session admission is not running")
        for key, controller in self.controllers.items():
            if key not in self.policies:
                continue
            episode = frame["episodes"][key]
            if episode["started_at"] is None and not episode["market_qualified"]:
                if not controller.processed_data.get("quote_guard", {}).get("can_admit"):
                    raise SubmissionBlocked("bootstrap market is not jointly ready")
                duration = self.policies[key]["quote_guards"]["bootstrap"]["duration_seconds"]
                expiry = min(now + duration, session.expires_at, session.step_expires_at)
                if not math.isfinite(expiry) or expiry <= now:
                    raise SubmissionBlocked("bootstrap admission already expired")
                episode.update(started_at=now, expires_at=expiry)
        recovery["listing_bootstrap"] = frame

    def _market(self, connector, pair, row):
        frame = self._validated(row["recovery_state"] or {})
        matches = [key for key, policy in self.policies.items()
                   if (policy["connector_name"], policy["trading_pair"]) == (connector.name, pair)]
        if not matches:
            raise SubmissionBlocked("bootstrap market identity mismatch")
        key = matches[0]
        controller = self.controllers.get(key)
        if controller is None:
            raise SubmissionBlocked("bootstrap controller unavailable")
        controller.restore_bootstrap_episode(frame["episodes"][key])
        controller.update_reference()
        if not controller.processed_data.get("ready"):
            raise SubmissionBlocked(controller.processed_data.get("reason", "bootstrap market unavailable"))
        return key, frame["episodes"][key], controller

    def _check(self, connector, order, row, *, original=None):
        from hummingbot.core.data_type.common import OrderType
        key, episode, controller = self._market(connector, order.trading_pair, row)
        # Other configured markets retain their normal path when only one leg bootstraps.
        if order.order_type != OrderType.LIMIT_MAKER:
            raise SubmissionBlocked("bootstrap requires post-only maker orders")
        boot = not episode["market_qualified"]
        deadline = episode["expires_at"] if boot else original
        if (boot and deadline is None) or (deadline is not None and float(row["database_now"]) >= deadline):
            raise SubmissionBlocked("bootstrap_expired")
        if not controller.quote_price_valid(order.price, bootstrap=boot or original is not None):
            raise SubmissionBlocked("bootstrap quote price outside bounds")
        if boot:
            orders = dict(connector.in_flight_orders)
            orders[order.client_order_id] = order
            if len(orders) > controller.config.quote_guards.max_own_orders:
                raise SubmissionBlocked("bootstrap tracked order limit")
            amount = Decimal("0")
            for item in orders.values():
                if item.trading_pair == order.trading_pair and not item.is_done:
                    price, size = Decimal(str(item.price)), Decimal(str(item.amount))
                    if not price.is_finite() or not size.is_finite() or price <= 0 or size <= 0:
                        raise SubmissionBlocked("bootstrap unknown pending allocation")
                    amount += price * size  # Original sizes retain partially filled allocations.
            if amount > controller.config.quote_guards.bootstrap.max_quote:
                raise SubmissionBlocked("bootstrap_quote_budget")
        controller.check_maker_crossing(order.price, order.trade_type)
        return deadline

    def before_order(self, connector, order, row):
        if not any(policy["connector_name"] == connector.name for policy in self.policies.values()):
            return
        deadline = self._check(connector, order, row)
        self.deadlines[order.client_order_id] = (connector.name, deadline)

    def authorize(self, data, row):
        identity = data.get("clOrdId")
        original = self.deadlines.get(identity)
        if original is None:
            # A configured bootstrap leg cannot bypass the pre-intent capture.
            instrument = data.get("instId", "")
            if any(instrument == policy["trading_pair"] + ("-SWAP" if policy["connector_name"] == "okx_perpetual" else "")
                   for policy in self.policies.values()):
                raise SubmissionBlocked("bootstrap order identity unavailable")
            return None
        name, deadline = original
        connector = self.owner.connectors[name]
        order = connector.in_flight_orders.get(identity)
        if order is None:
            raise SubmissionBlocked("bootstrap tracked order unavailable")
        instrument = order.trading_pair + ("-SWAP" if name == "okx_perpetual" else "")
        size = Decimal(str(data.get("sz")))
        if name == "okx_perpetual":
            size = connector._format_size_to_amount(order.trading_pair, size)
        if (data.get("instId") != instrument or data.get("side") != order.trade_type.name.lower()
                or Decimal(str(data.get("px"))) != order.price or size != order.amount
                or data.get("ordType") != "post_only"):
            raise SubmissionBlocked("bootstrap order payload changed")
        return self._check(connector, order, row, original=deadline)
