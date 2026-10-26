"""Bounded listing-price policy over the existing strict market observation.

This helper proposes transitions. Only the owner commits episode/qualification
state; observation/readiness calls cannot start, renew or qualify an episode.
"""
from hummingbot.strategy_v2.utils.quote_guard import BootstrapConfig, QuoteGuard

__all__ = ["BootstrapConfig", "ListingBootstrap"]


class ListingBootstrap:
    def __init__(self, config, *, anchor, sideways_range=None, max_gap_seconds=30):
        self.config, self.policy, self.anchor = config, config.bootstrap, anchor
        self.max_gap_seconds = float(max_gap_seconds)
        self.last_sample_at = None
        self.strict = QuoteGuard(config, sideways_range)
        self.episode = dict(started_at=None, expires_at=None, market_qualified=False)
        self.bound = False
        self.good_since = self.good_since_epoch = self.last_token = None
        self.samples = 0
        self.dwell_complete = False

    def restore(self, episode, *, bound):
        self.episode, self.bound = dict(episode), bound

    @property
    def lower_price(self):
        return self.anchor * (1 - self.policy.max_anchor_move_bps / 10000)

    @property
    def upper_price(self):
        return self.anchor * (1 + self.policy.max_anchor_move_bps / 10000)

    @property
    def market_qualified(self):
        return self.episode["market_qualified"]

    def _qualification(self, observation, monotonic_now, sample):
        qualified = (observation.market_mid is not None and observation.bootstrap_eligible
                     and min(observation.external_bid_quote, observation.external_ask_quote) >= self.policy.min_external_depth_quote
                     and observation.market_spread_bps <= self.policy.max_market_spread_bps)
        if (not qualified or (self.good_since is not None and monotonic_now < self.good_since)
                or (sample and self.last_sample_at is not None
                    and monotonic_now - self.last_sample_at > self.max_gap_seconds)):
            self.good_since = self.good_since_epoch = self.last_token = None
            self.samples, self.dwell_complete = 0, False
        elif sample and observation.token != self.last_token:
            if self.good_since is None:
                self.good_since, self.good_since_epoch = monotonic_now, observation.observed_at
            self.last_token = observation.token
            self.samples = min(2, self.samples + 1)
            self.dwell_complete = (self.samples >= 2
                                   and monotonic_now - self.good_since >= float(self.policy.qualification_seconds))
        if sample:
            self.last_sample_at = monotonic_now
        return qualified

    def evaluate(self, observation, reference, monotonic_now, *, sample=False, reference_model=False,
                 configured_anchor=False):
        qualified_now = self._qualification(observation, monotonic_now, sample)
        status = self.strict.evaluate(observation, reference, monotonic_now, sample=sample)
        pending = self.dwell_complete and not self.market_qualified
        reason = None
        candidate = False
        if not self.bound:
            reason = "bootstrap_binding_unavailable"
        elif not observation.bootstrap_eligible:
            reason = "market_price_unavailable"
        elif pending:
            reason = "bootstrap_checkpoint_pending"
        elif self.market_qualified:
            if not qualified_now or not self.dwell_complete:
                reason = "market_liquidity_unqualified"
            elif not status["ready"]:
                reason = status["reason_code"]
        else:
            if observation.market_mid is not None and not status["ready"]:
                reason = status["reason_code"]
            if not self.lower_price <= reference <= self.upper_price:
                reason = "bootstrap_band"
            limits = self.strict.sideways_range
            if observation.market_mid is None and limits is not None and not limits.lower_price <= reference <= limits.upper_price:
                reason = "bootstrap_range"
            expiry = self.episode["expires_at"]
            if expiry is not None and observation.observed_at >= expiry:
                reason = "bootstrap_expired"
            if reason is None and self.episode["started_at"] is None:
                candidate, reason = True, "bootstrap_not_started"
        ready = reason is None
        state = "market_guarded" if self.market_qualified and ready else (
            "bootstrap" if not self.market_qualified and (ready or candidate) else "waiting")
        source = "reference_model" if reference_model else (
            "configured_anchor" if not self.market_qualified or configured_anchor else "market_book")
        expiry = self.episode["expires_at"]
        return {**status, "mode": "bootstrap_then_market", "ready": ready, "reason_code": reason,
                "can_admit": ready or candidate, "qualification_pending": pending,
                "bootstrap": dict(state=state, price_source=source, anchor=self.anchor,
                                  lower_price=self.lower_price, upper_price=self.upper_price,
                                  max_quote=self.policy.max_quote, started_at=self.episode["started_at"],
                                  expires_at=expiry, remaining_seconds=max(0, expiry - observation.observed_at) if expiry is not None else None,
                                  market_qualified=self.market_qualified, qualification_started_at=self.good_since_epoch,
                                  qualification_valid_samples=self.samples, qualification_ready=self.dwell_complete,
                                  external_bid_quote=observation.external_bid_quote,
                                  external_ask_quote=observation.external_ask_quote,
                                  market_spread_bps=observation.market_spread_bps,
                                  market_breakout_available=observation.market_mid is not None)}
