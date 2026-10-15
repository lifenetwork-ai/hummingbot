"""Configured native quote proofs on the repository's owning file worker."""
from uuid import uuid4

from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.config import QuotesConfig, parse_duration_seconds
from hummingbot.strategy_v2.life_liquidity.fees import FeeQuoteBinding
from hummingbot.strategy_v2.life_liquidity.quote_actions import QuoteActionPlanner, QuotePlanningSnapshot
from hummingbot.strategy_v2.life_liquidity.reference import ReferenceEngine
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, thaw
from hummingbot.strategy_v2.life_liquidity.storage.spot_application import SpotAdmission
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


class FileQuoteApplication:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self.controller, self.policy = service.controller, service.config.quotes

    @classmethod
    async def install(cls, service):
        app = cls(service)
        await service.store._call(app._install)
        return app

    def _install(self):
        self.store.assert_worker_thread()
        c, cfg, src = self.controller, self.policy, self.service.sources
        economics = c.config.strategy.economics
        reference = ReferenceEngine(cfg.reference_policy, life_anchor_usdt=cfg.life_anchor_usdt,
                                    model_version=cfg.model_version, life_source_id='okx:LIFE-USDT', source_anchors_usdt={})
        fees = FeeQuoteBinding(snapshot=src.fee_snapshot, exchange_now_ms=lambda: c.continuous_gate.exchange_time_ms,
                               account_id=self.store.binding.account_uid, connector_name=c.config.strategy.spot.connector,
                               instrument_id=c.config.strategy.spot.pair, group_id=cfg.fee_group_id,
                               max_age_ms=int(parse_duration_seconds(economics.fee_max_age) * 1000),
                               fee_policy=economics.fee_policy, fee_ceiling_bps=economics.fee_ceiling_bps)
        self.native = QuoteActionPlanner(c, wal=self.store._wal, reservations=self.store._ledger,
                                         snapshot=self._snapshot, monotonic_clock=src.monotonic_clock, intent_id_factory=lambda: 'quote:' + uuid4().hex,
                                         max_actions_per_tick=cfg.max_actions_per_tick, reference_engine=reference, fee_binding=fees,
                                         subsidy_budget=self.store._subsidy if economics.objective == 'liquidity_service' else None, utc_clock=src.clock)
        meta = self.store._read()
        prior = meta.resources.get('quote_service_policy')
        if prior is None:
            if not self.store._created_offline or self.native.action_journal.verified_records():
                raise StorageError('SERVICE_QUOTE_IMPORT_REQUIRED')
            if not self.native.action_journal.path.exists():
                self.native.action_journal.initialize_empty()
            self.store._commit(meta.cut, self.service.config.owner, 'quote-service:bind',
                               (('quote_service_policy', encode(cfg.model_dump(mode='json'))),))
        elif thaw(prior) != cfg.model_dump(mode='json') or not self.native.action_journal.path.is_file():
            raise StorageError('SERVICE_QUOTE_POLICY_OR_HISTORY_MISMATCH')
        self.native.action_journal.verified_records()
        c.install_quote_action_planner(self.native)
        # The physical lock is already held and UID was fetched asynchronously
        # before construction; no synthetic account-scope flag is inferred from SQL.
        c._order_safety_account_lock = self.store._account_lock
        c._account_uid_verified = True
        self.service.spot.admission = self.admission

    def _verify_policy(self):
        self.store.assert_worker_thread()
        meta = self.store._read()
        engine = self.native.reference_engine
        if (thaw(meta.resources.get('quote_service_policy')) != self.policy.model_dump(mode='json')
                or engine.policy != self.policy.reference_policy or engine.life_anchor_usdt != self.policy.life_anchor_usdt
                or engine.model_version != self.policy.model_version or engine.life_source_id != 'okx:LIFE-USDT'
                or dict(engine.source_anchors_usdt) != {} or self.native.fee_binding.group_id != self.policy.fee_group_id):
            raise StorageError('SERVICE_QUOTE_POLICY_CHANGED')

    def _snapshot(self):
        self._verify_policy()
        observed = self.service.sources.quote_snapshot()
        economics = self.controller.config.strategy.economics
        if (type(observed) is not QuotePlanningSnapshot
                or observed.policy.min_net_edge_bps != economics.min_net_edge_bps
                or observed.costs.uncertainty_bps != economics.uncertainty_buffer_bps
                or observed.rules != self.controller.listing_gate.instrument_rules):
            raise StorageError('SERVICE_QUOTE_SNAPSHOT_POLICY_MISMATCH')
        return observed

    def admission(self, config):
        self.store.assert_worker_thread()
        if not self.native.authorizes_config(config):
            raise StorageError('SERVICE_QUOTE_UNQUALIFIED')
        current = self.native.manager.current_session
        observed = self.native._current_snapshot(current)
        quotes = self.controller.config.strategy.quotes
        level = int(config.level_id)
        single = QuotesConfig(spreads_bps=(quotes.spreads_bps[level],), sizes_base=(quotes.sizes_base[level],),
                              buy_taper_start_base=quotes.buy_taper_start_base, buy_block_base=quotes.buy_block_base)
        plan = self.native._plan(observed, current, quotes=single, sides=(config.side.name,))
        candidate = next((v for v in plan.candidates if (v.side, v.price_usdt, v.quantity_base) == (
            config.side.name, config.price, config.amount)), None)
        if candidate is None:
            raise StorageError('SERVICE_QUOTE_CANDIDATE_CHANGED')
        rate = observed.costs.maker_fee_rate
        if (self.policy.fee_buffer_quote_usdt < config.amount * config.price * rate
                or self.policy.fee_buffer_base_life < config.amount * rate):
            raise StorageError('SERVICE_QUOTE_FEE_BUFFER_INSUFFICIENT')
        return SpotAdmission(observed.qualified_reference_usdt, candidate.economics.subsidy_reserved_quote,
                             self.policy.fee_buffer_quote_usdt, self.policy.fee_buffer_base_life)

    @staticmethod
    def capture(action):
        if type(action) is not CreateExecutorAction or type(action.executor_config) is not OrderExecutorConfig:
            raise StorageError('SERVICE_QUOTE_ACTION_TYPE_INVALID')
        return (action.controller_id, action.executor_config.model_dump_json())

    @staticmethod
    def restore(payload):
        controller_id, raw = payload
        return CreateExecutorAction(controller_id=controller_id, executor_config=OrderExecutorConfig.model_validate_json(raw))

    def transition(self, raw, state):
        self.store.assert_worker_thread()
        action = self.restore(raw)
        if state == 'DISPATCHED':
            return self.native.on_runner_action_dispatched(action)
        if state == 'REJECTED':
            return self.native.on_runner_action_rejected(action)
        raise StorageError('SERVICE_QUOTE_TRANSITION_INVALID')
