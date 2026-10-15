"""Worker-owned risk decisions from the repository's accounted financial facts.

Only the financial repository writes NAV/highwater. Markout checkpoints contain
independent horizon observations, never balances, fills or release permission.
"""
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.capital_risk import IndependentNavObservation
from hummingbot.strategy_v2.life_liquidity.markout import MarkoutSummary, MarkoutTracker
from hummingbot.strategy_v2.life_liquidity.markout_risk import (
    IndependentHorizonObservation,
    MarkoutProbeGuard,
    MarkoutProbeSource,
)
from hummingbot.strategy_v2.life_liquidity.recovery_probe import RecoveryProbeGuard
from hummingbot.strategy_v2.life_liquidity.safety import SafetyGate, SafetyObservation
from hummingbot.strategy_v2.life_liquidity.storage.capital import ObserveSpotNAV
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint, thaw, utc
from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillObservation


class FileRiskApplication:
    def __init__(self, service):
        self.service, self.store, self.policy = service, service.store, service.config.risk
        self.reason_code = 'SERVICE_RISK_STARTUP_REVALIDATION'
        self._last_allowed, self._qualified_sources = False, None
        self._last_reduction_allowed = False
        self._invalidations = self._evaluated_invalidations = 0
        self._markout_expires_at = None
        self.probe = None
        self.markout_probe = self.markout_source = self._markout_tracker = None
        self._markout_bootstrap = False

    @classmethod
    async def install(cls, service, create):
        app = cls(service)
        await app.store._call(app._install, create)
        return app

    def _install(self, create):
        self.store.assert_worker_thread()
        p = self.policy
        self.gate = SafetyGate(Path(self.service.config.state_dir) / 'safety.json',
                               max_drawdown_bps=p.max_drawdown_bps, min_margin_buffer_quote=p.min_margin_buffer_quote,
                               stable_data_ms=p.stable_data_ms, recovery_probe_base=p.recovery_probe_base, require_existing=not create)
        if create:
            self.gate.initialize_empty()
        meta = self.store._read()
        prior = meta.resources.get('risk_service_policy')
        if prior is None:
            if not create or not self.store._created_offline:
                raise StorageError('SERVICE_RISK_IMPORT_REQUIRED')
            self.store._commit(meta.cut, self.service.config.owner, 'risk-service:bind', (
                ('risk_service_policy', encode(p.binding_json())),
                ('risk_markouts', encode({'observations': {}, 'last_checked_ms': None}))))
        elif thaw(prior) != p.binding_json():
            raise StorageError('SERVICE_RISK_POLICY_MISMATCH')
        # A restored gate cannot be rearmed by a diagnostic/recovery receipt.
        self.gate.bind_reconciliation_authority(lambda: False)
        if p.recovery_probe_campaign_base is not None:
            self.probe = RecoveryProbeGuard(Path(self.service.config.state_dir) / 'recovery_probe.json',
                                            self.store._ledger, max_quote_base=p.recovery_probe_base,
                                            max_campaign_base=p.recovery_probe_campaign_base, create=create)
            self.service.controller._runtime_risk_gate = self.gate
            self.service.controller.install_recovery_probe_guard(self.probe)
        if p.markout is not None and p.markout.probe_size_base is not None:
            self.markout_source = FileMarkoutSource(self)
            self.markout_probe = MarkoutProbeGuard(self.markout_source, self.store._ledger,
                                                   max_quote_base=p.markout.probe_size_base,
                                                   max_campaign_base=p.markout.probe_campaign_base,
                                                   create=create, path=Path(self.service.config.state_dir) / 'markout_probe.json')
            self.service.controller._markout_monitor = self.markout_source
            self.service.controller.install_markout_probe_guard(self.markout_probe)

    def _markout_probe_binding_matches(self):
        c, p, guard = self.service.controller, self.policy.markout, self.markout_probe
        return (p is not None and guard is not None and c._markout_monitor is self.markout_source
                and c._markout_probe_guard is guard and guard.monitor is self.markout_source
                and guard.reservations is self.store._ledger
                and (guard.max_quote_base, guard.max_campaign_base) == (p.probe_size_base, p.probe_campaign_base))

    def probe_limit(self):
        """Cache-only ceiling; pending and campaign exposure still need native proof."""
        caps = []
        if self.gate.state == 'DEGRADED' and self._probe_binding_matches():
            caps.append(self.policy.recovery_probe_base)
        if self._markout_bootstrap and self._markout_probe_binding_matches():
            caps.append(self.policy.markout.probe_size_base)
        return min(caps) if caps else None

    def markout_permissions_ready(self):
        ready = self.markout_ready()
        self._markout_bootstrap = bool(not ready and self._markout_probe_binding_matches()
                                       and self.markout_probe.capacity_available())
        return ready or self._markout_bootstrap

    def _probe_binding_matches(self):
        # Cache-only identity/policy checks. Journal and capacity are checked on
        # the worker; a loop fence cannot turn changed policy into permission.
        c, p = self.service.controller, self.policy
        return (self.probe is not None and p.recovery_probe_campaign_base is not None
                and c._runtime_risk_gate is self.gate and c._recovery_probe_guard is self.probe
                and self.probe.reservations is self.store._ledger
                and self.probe.max_quote_base == p.recovery_probe_base
                and self.probe.max_campaign_base == p.recovery_probe_campaign_base
                and c._recovery_probe_policy_matches())

    def _guard(self):
        self.store.assert_worker_thread()
        meta = self.store._read()
        p = self.policy
        if (self.service.config.risk != p or thaw(meta.resources.get('risk_service_policy')) != p.binding_json()
                or (self.gate.max_drawdown_bps, self.gate.min_margin_buffer_quote, self.gate.stable_data_ms,
                    self.gate.recovery_probe_base) != (p.max_drawdown_bps, p.min_margin_buffer_quote,
                                                       p.stable_data_ms, p.recovery_probe_base)
                or not self.gate.journal_verified()):
            raise StorageError('SERVICE_RISK_POLICY_OR_JOURNAL_CHANGED')
        if p.recovery_probe_campaign_base is not None:
            if not self._probe_binding_matches():
                raise StorageError('SERVICE_RECOVERY_PROBE_BINDING_CHANGED')
            with self.probe.journal.locked():
                if not self.probe._policy_matches():
                    raise StorageError('SERVICE_RECOVERY_PROBE_POLICY_CHANGED')
        if p.markout is not None and p.markout.probe_size_base is not None:
            if not self._markout_probe_binding_matches() or not self.markout_probe._policy_verified():
                raise StorageError('SERVICE_MARKOUT_PROBE_POLICY_CHANGED')
        view = self.store._snapshot()
        if (self.store._incomplete(meta.resources) or dict(view.cut.components) != dict(meta.resources['journal_hashes'])
                or meta.ownership.token != self.service.spot.owner):
            raise StorageError('SERVICE_RISK_ACCOUNTING_UNQUALIFIED')
        return meta

    def _drawdown(self, meta, observation_grant_id=None):
        src, p = self.service.sources, self.policy
        now = utc(self.store._clock())
        at = int(now.timestamp() * 1000)
        value = src.nav_observation()
        if (type(value) is not IndependentNavObservation or value.source_kind != 'independent_market'
                or type(value.value_at_ms) is not int or type(value.observed_at_ms) is not int
                or not 0 < value.value_at_ms <= value.observed_at_ms <= at
                or at - value.value_at_ms > p.max_value_age_ms
                or not value.price_usdt.is_finite() or value.price_usdt <= 0):
            raise StorageError('SERVICE_RISK_NAV_SOURCE_UNQUALIFIED')
        if self.service.joint is not None:
            # Native joint NAV includes positions/collateral and signed SWAP cash.
            # A SPOT-only NAV must never replace it or double-count collateral.
            recovery = self.service.joint._recovery
            snapshot = recovery.capital.qualified_snapshot()
            measured = recovery.measure()
            if measured is None or snapshot.index_price_usdt != value.price_usdt:
                raise StorageError('SERVICE_RISK_JOINT_NAV_UNQUALIFIED')
            margin = snapshot.collateral_quote - snapshot.initial_margin_quote - snapshot.maintenance_margin_quote - snapshot.funding_liability_quote
            return measured.drawdown_bps, margin, value
        latest_id = meta.resources.get('capital', {}).get('latest_operation_id')
        if latest_id is not None:
            prior = meta.resources['nav_operations'][latest_id]['command']
            if (value.value_at_ms < prior['value_at_ms'] or value.observed_at_ms < prior['reference_observed_at_ms']
                    or value.value_at_ms == prior['value_at_ms'] and value.price_usdt != Decimal(prior['independent_price_usdt'])):
                raise StorageError('SERVICE_RISK_NAV_VALUE_CHANGED')
        margin = self.store._snapshot().balance.available_quote_usdt
        money = self.store._money_fingerprint(meta.resources)
        terms = {'money': money, 'price': str(value.price_usdt),
                 'at': value.value_at_ms, 'observed': value.observed_at_ms}
        if observation_grant_id is not None:
            terms['observation_grant_id'] = observation_grant_id
        identity = fingerprint(terms)
        command = ObserveSpotNAV('risk-nav:' + identity, value.price_usdt, value.value_at_ms,
                                 value.observed_at_ms, value.source_kind, now, observation_grant_id)
        # Original observation time is retained on same-price/money readback.
        existing = meta.resources.get('nav_operations', {}).get(command.operation_id)
        if existing is not None:
            return self.store._capital().drawdown_bps, margin, value
        return self.store._observe_nav(command, self.store._snapshot().cut, self.service.spot.owner).capital.drawdown_bps, margin, value

    def local_ready(self, *, normal_only=False, reduce_only=False):
        # Cache-only fence after worker/transport waits. This cannot issue a
        # grant, write a journal or replace fresh worker qualification.
        if not (self._last_reduction_allowed if reduce_only else self._last_allowed) or self._qualified_sources is None:
            return False
        invalidate_stability = True
        try:
            nav, observation = self._qualified_sources
            current_nav = self.service.sources.nav_observation()
            current_risk = self.service.sources.safety_observation()
            now = int(utc(self.store._clock()).timestamp() * 1000)
            mono = self.service.sources.monotonic_clock() * 1000
            cached_fresh = (0 <= now - nav.value_at_ms <= self.policy.max_value_age_ms
                            and 0 <= mono - observation.observed_monotonic_ms <= self.policy.max_observation_age_ms
                            and (reduce_only or self._markout_expires_at is None or now <= self._markout_expires_at))
            data_good = (type(current_nav) is IndependentNavObservation
                         and type(current_nav.value_at_ms) is int and type(current_nav.observed_at_ms) is int
                         and nav.value_at_ms <= current_nav.value_at_ms <= current_nav.observed_at_ms <= now
                         and current_nav.source_kind == 'independent_market'
                         and type(current_nav.price_usdt) is Decimal and current_nav.price_usdt.is_finite() and current_nav.price_usdt > 0
                         and (current_nav.value_at_ms > nav.value_at_ms or current_nav.price_usdt == nav.price_usdt)
                         and type(current_risk) is SafetyObservation
                         and type(current_risk.observed_monotonic_ms) is int
                         and observation.observed_monotonic_ms <= current_risk.observed_monotonic_ms <= mono
                         and all(v is True for v in (current_risk.market_data_fresh, current_risk.latency_ok,
                                                     current_risk.account_ready, current_risk.model_ready))
                         and type(current_risk.margin_buffer_quote) is Decimal and current_risk.margin_buffer_quote.is_finite()
                         and current_risk.margin_buffer_quote >= self.policy.min_margin_buffer_quote)
            invalidate_stability = not (cached_fresh and data_good)
            # A benign timestamp refresh cannot extend the original proof.
            # A changed price requires worker revaluation without declaring an
            # otherwise valid feed unstable merely because its price moved.
            allowed = (not self.service._revoked and self.policy == self.service.config.risk
                       and (self.policy.recovery_probe_campaign_base is None or self._probe_binding_matches())
                       and (not self._markout_bootstrap or self._markout_probe_binding_matches())
                       and (self.gate.state == 'NORMAL'
                            or self.gate.state == 'DEGRADED' and (reduce_only or self._probe_binding_matches()))
                       and cached_fresh and data_good
                       and current_nav.price_usdt == nav.price_usdt)
        except Exception:
            allowed = False
        if not allowed:
            self._last_allowed = self._last_reduction_allowed = False
            if invalidate_stability:
                self._invalidations += 1
        # A stricter product query must not revoke a valid SPOT probe proof.
        return allowed and (not normal_only or self.gate.state == 'NORMAL' and not self._markout_bootstrap)

    def observation_ready(self, grant_id):
        """Read-only successor qualification preserves cold/manual rearm.

        Independent NAV may advance its existing highwater, never wallet, fills,
        budgets or a volatile quote/reduction permission. Breaches still HALT.
        """
        self.store.assert_worker_thread()
        self._last_allowed = self._last_reduction_allowed = False
        try:
            meta = self._guard()
            self.store._session_observation_guard(self.store._snapshot(), meta, self.service.spot.owner, grant_id)
            if self.service._revoked or self.gate.state == 'HALTED':
                return False
            src, p = self.service.sources, self.policy
            observed, now_ms = src.safety_observation(), src.monotonic_clock() * 1000
            if (type(observed) is not SafetyObservation or type(observed.observed_monotonic_ms) is not int
                    or not 0 <= now_ms - observed.observed_monotonic_ms <= p.max_observation_age_ms
                    or any(v is not True for v in (observed.market_data_fresh, observed.latency_ok,
                                                   observed.account_ready, observed.model_ready))
                    or not isinstance(observed.margin_buffer_quote, Decimal)
                    or not observed.margin_buffer_quote.is_finite() or observed.margin_buffer_quote < 0):
                raise StorageError('SERVICE_RISK_OBSERVATION_UNQUALIFIED')
            drawdown, margin, _ = self._drawdown(meta, grant_id)
            if drawdown > p.max_drawdown_bps:
                self.gate.halt('DRAWDOWN_LIMIT_BREACHED')
                return False
            if min(margin, observed.margin_buffer_quote) < p.min_margin_buffer_quote:
                self.gate.halt('MARGIN_BUFFER_BREACHED')
                return False
            return True
        except Exception:
            return False

    def ready(self, *, normal_only=False, reduce_only=False):
        self.store.assert_worker_thread()
        self._last_allowed = self._last_reduction_allowed = False
        invalidations = self._invalidations
        try:
            if invalidations != self._evaluated_invalidations:
                self.gate.invalidate('RISK_INPUTS_CHANGED')
                self._evaluated_invalidations = invalidations
            meta = self._guard()
            if self.store._cold_unarmed or self.service._revoked:
                return False
            src = self.service.sources
            observed, now_ms = src.safety_observation(), src.monotonic_clock() * 1000
            if (type(observed) is not SafetyObservation or type(observed.observed_monotonic_ms) is not int
                    or not 0 <= now_ms - observed.observed_monotonic_ms <= self.policy.max_observation_age_ms):
                raise StorageError('SERVICE_RISK_OBSERVATION_UNQUALIFIED')
            # Caller-supplied drawdown is replaced by qualified native accounting.
            drawdown, margin, value = self._drawdown(meta)
            decision = self.gate.evaluate(replace(observed, drawdown_bps=drawdown,
                                                  margin_buffer_quote=min(observed.margin_buffer_quote, margin)))
            self.reason_code = self.service.controller.runtime_risk_reason_code = decision.reason_code
            permitted_state = (decision.state == 'NORMAL' or decision.state == 'DEGRADED'
                               and self._probe_binding_matches() and self.probe.capacity_available())
            if reduce_only:
                self._last_reduction_allowed = (decision.state in ('NORMAL', 'DEGRADED')
                                                and invalidations == self._invalidations and not self.service._revoked)
                if self._last_reduction_allowed:
                    self._qualified_sources = value, observed
                return self._last_reduction_allowed
            self._last_allowed = (decision.allow_new_quotes and permitted_state and self.markout_permissions_ready()
                                  and invalidations == self._invalidations and not self.service._revoked)
            if self._last_allowed:
                self._qualified_sources = value, observed
            return self._last_allowed and (not normal_only or decision.state == 'NORMAL' and not self._markout_bootstrap)
        except Exception:
            self.gate.invalidate('SERVICE_RISK_UNQUALIFIED')
            self.reason_code = self.service.controller.runtime_risk_reason_code = 'SERVICE_RISK_UNQUALIFIED'
            return False

    def markout_ready(self):
        self.store.assert_worker_thread()
        p = self.policy.markout
        if p is None:
            return True
        try:
            meta = self._guard()
            now = int(utc(self.store._clock()).timestamp() * 1000)
            state = thaw(meta.resources['risk_markouts'])
            if state['last_checked_ms'] is not None and now < state['last_checked_ms']:
                raise StorageError('SERVICE_MARKOUT_CLOCK_ROLLBACK')
            previous = fingerprint(state)
            expiries = []
            pending = missing = False
            tracker = MarkoutTracker(horizons_ms=p.horizons_ms, min_samples=p.min_samples,
                                     size_bucket_edges=(p.size_cutoff_base,))
            commands = {row['command']['intent_id']: row['command'] for row in meta.resources['commands'].values()}
            fills = meta.resources.get('fills', {})
            if not set(state['observations']).issubset(fills):
                raise StorageError('SERVICE_MARKOUT_FILL_SCOPE_CHANGED')
            for key, row in fills.items():
                fill = SpotFillObservation.from_json(row['fill'])
                if fill.fill_at_ms < now - p.cohort_window_ms:
                    continue
                expiries.append(fill.fill_at_ms + p.cohort_window_ms)
                tracker.record_fill(key, commands[row['intent_id']]['side'], fill.quantity_base_life,
                                    fill.price_usdt, fill_at_ms=fill.fill_at_ms)
                observations = state['observations'].setdefault(key, {})
                if not set(observations).issubset({str(h) for h in p.horizons_ms}):
                    raise StorageError('SERVICE_MARKOUT_HORIZON_CHANGED')
                for h in p.horizons_ms:
                    target = fill.fill_at_ms + h
                    raw = observations.get(str(h))
                    if raw is None:
                        if target > now:
                            pending = True
                            continue
                        value = self.service.sources.horizon_observation(fill.trade_id, h)
                        if value is None:
                            missing = True
                            continue
                        if (type(value) is not IndependentHorizonObservation or value.source_kind != 'independent_market'
                                or type(value.value_at_ms) is not int or type(value.observed_at_ms) is not int
                                or not target <= value.value_at_ms <= value.observed_at_ms <= now
                                or value.observed_at_ms - target > p.max_horizon_lag_ms
                                or not value.price_usdt.is_finite() or value.price_usdt <= 0):
                            raise StorageError('SERVICE_MARKOUT_SOURCE_UNQUALIFIED')
                        raw = {'price': str(value.price_usdt), 'value_at': value.value_at_ms,
                               'at': value.observed_at_ms, 'source': value.source_kind}
                        observations[str(h)] = raw
                    if (raw['source'] != 'independent_market'
                            or not target <= raw['value_at'] <= raw['at'] <= min(now, target + p.max_horizon_lag_ms)):
                        raise StorageError('SERVICE_MARKOUT_SAVED_OBSERVATION_INVALID')
                    tracker.observe(key, h, Decimal(raw['price']), observed_at_ms=raw['at'], source_kind='independent_market')
            state['last_checked_ms'] = now
            if fingerprint(state) != previous:
                self.store._commit(meta.cut, self.service.spot.owner, 'risk-markout:' + fingerprint(state),
                                   (('risk_markouts', encode(state)),), observation=True)
            summaries = [tracker.summary(side, bucket, h, now_ms=now) for side, bucket, h in p.monitored_cohorts]
            adverse = any(s.mean_markout_quote is not None and s.mean_markout_quote < p.min_mean_markout_quote for s in summaries)
            insufficient = any(s.mean_markout_quote is None for s in summaries)
            allowed = not (pending or missing or adverse or insufficient)
            self._markout_tracker = tracker
            self._markout_expires_at = min(expiries) if expiries else None
            if not allowed:
                self._last_allowed = False
            self.service.controller.markout_reason_code = ('MARKOUT_ADVERSE' if adverse else
                                                           'MARKOUT_OBSERVATION_MISSING' if missing else 'MARKOUT_HORIZON_PENDING' if pending else
                                                           'MARKOUT_INSUFFICIENT_SAMPLES' if insufficient else 'MARKOUT_READY')
            return allowed
        except Exception:
            self._last_allowed = False
            self.service.controller.markout_reason_code = 'MARKOUT_UNAVAILABLE'
            return False


class FileMarkoutSource(MarkoutProbeSource):
    """Cohorts from the repository's sole accounted fill stream; no attributor."""

    def __init__(self, risk):
        self.risk = risk
        self.path = risk.store._path

    @property
    def reservations(self):
        return self.risk.store._ledger

    @property
    def reason_code(self):
        return self.risk.service.controller.markout_reason_code

    @property
    def size_cutoff_base(self):
        return self.risk.policy.markout.size_cutoff_base

    @property
    def monitored_cohorts(self):
        return self.risk.policy.markout.monitored_cohorts

    def _policy(self):
        return {'binding': self.risk.store.binding.json(), 'markout': self.risk.policy.markout.binding_json()}

    def summary(self, side, size_bucket, horizon_ms):
        self.risk.store.assert_worker_thread()
        tracker = self.risk._markout_tracker
        if tracker is None:
            return MarkoutSummary('MARKOUT_UNAVAILABLE', 0, None)
        return tracker.summary(side, size_bucket, horizon_ms, now_ms=int(utc(self.risk.store._clock()).timestamp() * 1000))
