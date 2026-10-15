"""Explicit post-only SELL reduction on the repository's original money authority."""
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.economics import ExitInputs, evaluate_exit
from hummingbot.strategy_v2.life_liquidity.exit_actions import ExitObservation
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerMode,
    StorageError,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.inventory_application import FileInventoryApplication

ZERO = Decimal('0')


@dataclass(frozen=True)
class ExitCandidate:
    allowed: bool
    quantity_base: Decimal
    price_usdt: Decimal
    estimated_exit_loss_quote: Decimal


class FileExitApplication(FileInventoryApplication):
    children_key = 'exit_children'

    def __init__(self, service):
        self.service, self.store, self.policy = service, service.store, service.config.exit
        self.budget = self.store._subsidy.exit
        self._configs, self.active = {}, False
        self.reason_code = 'EXIT_NOT_REQUESTED'
        self._quote_admission = service.spot.admission

    def _install(self, create):
        self.store.assert_worker_thread()
        meta = self.store._read()
        prior = meta.resources.get('exit_service_policy')
        if prior is None:
            if not create or not self.store._created_offline:
                raise StorageError('SERVICE_EXIT_IMPORT_REQUIRED')
            self.store._commit(meta.cut, self.service.spot.owner, 'exit-service:bind', (
                ('exit_service_policy', encode(self.policy.model_dump(mode='json'))),
                ('exit_mode', encode({'active': False, 'request': None})), ('exit_requests', encode({})),
                ('exit_stops', encode({})),
                (self.children_key, encode({}))))
        elif thaw(prior) != self.policy.model_dump(mode='json'):
            raise StorageError('SERVICE_EXIT_POLICY_MISMATCH')
        self.active = self.store._read().resources['exit_mode']['active']
        self._read()
        self.service.spot.admission = self.admission

    def _read(self):
        self.store.assert_worker_thread()
        meta = self.store._read()
        if (self.service.config.exit != self.policy
                or thaw(meta.resources.get('exit_service_policy')) != self.policy.model_dump(mode='json')
                or self.budget is not self.store._subsidy.exit or self.budget.campaign_id != self.policy.budget.campaign_id
                or self.budget.path != self.store._files.exit_subsidy
                or self.budget._policy() != {k: v for k, v in self.policy.budget.json().items() if k != 'campaign_id'}):
            raise StorageError('SERVICE_EXIT_POLICY_CHANGED')
        mode = meta.resources['exit_mode']
        if mode['request'] is not None and mode['request'] not in meta.resources['exit_requests']:
            raise StorageError('SERVICE_EXIT_REQUEST_MISSING')
        if mode['active'] != self.active:
            raise StorageError('SERVICE_EXIT_MODE_CHANGED')
        return meta

    def request(self, operation_id, snapshot):
        identifier(operation_id)
        self.store.assert_worker_thread()
        meta = self._read()
        if (self.service.control._snapshot is not snapshot or self.store._snapshot().cut != snapshot.cut
                or self.store._cold_unarmed or self.service._revoked or self.service.spot._revoked
                or meta.ownership.mode is not OwnerMode.ACTIVE or meta.ownership.token != self.service.spot.owner
                or self.store._wal.active_records() or self.service.quotes.native.action_journal.active_records()
                or self.service.joint is not None and self.service.joint._recovery.capital.claim_ids()
                or snapshot.facts.accepted_wire_ids or snapshot.facts.runner_wire_ids
                or not 0 <= (utc(self.store._clock()) - snapshot.facts.observed_at).total_seconds() * 1000 <= self.policy.max_age_ms
                or snapshot.facts.liability_base_life != 0 or snapshot.facts.liability_quote_usdt != 0
                or not all((snapshot.facts.order_scope_complete, snapshot.facts.bill_scope_complete, snapshot.facts.runner_scope_complete))
                or self.service.inventory is not None and not self.service.inventory.mm_ready()):
            raise StorageError('SERVICE_EXIT_FRESH_QUIESCENCE_REQUIRED')
        requests = thaw(meta.resources['exit_requests'])
        if operation_id in requests:
            return False  # Historical requests cannot restore a mode or permit.
        if self.active:
            raise StorageError('SERVICE_EXIT_ALREADY_ACTIVE')
        requests[operation_id] = {'owner': self.service.spot.owner.json(), 'cut': snapshot.cut.json(),
                                  'facts': snapshot.facts.json(), 'policy': fingerprint(self.policy.model_dump(mode='json'))}
        self.store._commit(meta.cut, self.service.spot.owner, 'exit-request:' + operation_id, (
            ('exit_requests', encode(requests)), ('exit_mode', encode({'active': True, 'request': operation_id}))))
        self.active = True
        return True

    def recorded(self, operation_id, kind):
        identifier(operation_id)
        if kind not in ('exit_requests', 'exit_stops'):
            raise StorageError('SERVICE_EXIT_OPERATION_KIND_INVALID')
        return operation_id in self._read().resources[kind]

    def stop(self, operation_id, snapshot):
        identifier(operation_id)
        meta = self._read()
        if (self.service.control._snapshot is not snapshot or self.store._snapshot().cut != snapshot.cut
                or self.store._cold_unarmed or self.service._revoked or self.service.spot._revoked
                or meta.ownership.mode is not OwnerMode.ACTIVE or meta.ownership.token != self.service.spot.owner
                or self.store._wal.active_records() or self.service.quotes.native.action_journal.active_records()
                or self.service.joint is not None and self.service.joint._recovery.capital.claim_ids()
                or snapshot.facts.accepted_wire_ids or snapshot.facts.runner_wire_ids
                or not 0 <= (utc(self.store._clock()) - snapshot.facts.observed_at).total_seconds() * 1000 <= self.policy.max_age_ms
                or not all((snapshot.facts.order_scope_complete, snapshot.facts.bill_scope_complete, snapshot.facts.runner_scope_complete))):
            raise StorageError('SERVICE_EXIT_FRESH_QUIESCENCE_REQUIRED')
        stops = thaw(meta.resources['exit_stops'])
        if operation_id in stops:
            return False
        stops[operation_id] = {'owner': self.service.spot.owner.json(), 'cut': snapshot.cut.json(), 'facts': snapshot.facts.json()}
        mode = {'active': False, 'request': meta.resources['exit_mode']['request']}
        self.store._commit(meta.cut, self.service.spot.owner, 'exit-stop:' + operation_id,
                           (('exit_stops', encode(stops)), ('exit_mode', encode(mode))))
        self.active = False
        return True

    def permissions(self):
        self.store.assert_worker_thread()
        s, c = self.service, self.service.controller
        try:
            meta = self._read()
            original = meta.resources['exit_requests'].get(meta.resources['exit_mode']['request'])
            return (self.active and original is not None and original['owner'] == s.spot.owner.json()
                    and not s._revoked and not s.admission_paused and not self.store._cold_unarmed
                    and s.risk.ready(reduce_only=True) and s.runtime._local_ready(allow_reduction=True)
                    and c._order_safety_manager.can_reduce() and c._joint_recovery_ready()
                    and c._carry_ready() and c._hedge_ready() and c._spot_quote_gates_ready()
                    and not c._runner_stops_pending() and not c.has_unverified_runner_order_events())
        except Exception:
            return False

    def pending_exit_ready(self):
        # This probe runs behind the watchdog admission barrier. It observes
        # risk/reference only; it cannot activate an executor or owner.
        try:
            self._read()
            return (self.active and not self.service._revoked and self.service.risk.ready(reduce_only=True)
                    and self.service.quotes.native._current_snapshot(
                        self.service.controller._order_safety_manager.current_session, require_active=False) is not None)
        except Exception:
            return False

    def _decision(self, *, exclude=None, proposal_id=None):
        if not self.permissions():
            raise StorageError('EXIT_SAFETY_BLOCKED')
        s, c, p = self.service, self.service.controller, self.policy
        current = c._order_safety_manager.current_session
        observed = s.quotes.native._current_snapshot(current, require_active=False,
                                                     pre_send_intent_id=None if exclude is None else exclude.intent_id)
        value = s.sources.exit_observation()
        at = int(utc(self.store._clock()).timestamp() * 1000)
        evidence = c._own_depth_decision.evidence
        if (observed is None or type(value) is not ExitObservation or value.source_kind != 'independent_market'
                or type(value.observed_at_ms) is not int or not 0 <= at - value.observed_at_ms <= p.max_age_ms
                or any(type(v) is not Decimal or not v.is_finite() or v <= 0 for v in (
                    value.independent_value_usdt, value.limit_price_usdt, value.best_bid_usdt, value.depth_base))
                or (value.best_bid_usdt, value.rules, value.fee_rate, value.independent_value_usdt) != (
                    observed.best_bid_usdt, observed.rules, observed.costs.maker_fee_rate, observed.qualified_reference_usdt)
                or value.limit_price_usdt <= value.best_bid_usdt
                or value.limit_price_usdt % value.rules.tick_size != 0
                or evidence is None or value.depth_base > min(evidence.bid_depth_base, evidence.ask_depth_base)):
            raise StorageError('EXIT_MARKET_UNQUALIFIED')
        preview = self.store._ledger.preview(exclude_open_intent=exclude)
        if preview.pending_orders():
            raise StorageError('EXIT_CANCEL_RECONCILIATION_REQUIRED')
        available_base = preview.life_balance - p.target_base - s.config.quotes.fee_buffer_base_life
        quantity = min(p.max_child_base, max(ZERO, available_base / (1 + value.fee_rate)))
        if proposal_id is not None:
            row = self._read().resources[self.children_key].get(proposal_id)
            if row is not None:
                quantity = min(quantity, OrderExecutorConfig.model_validate_json(row['config']).amount)
        quantity -= quantity % value.rules.lot_size
        if quantity < value.rules.min_size or quantity > value.depth_base:
            raise StorageError('EXIT_TARGET_OR_DEPTH_BLOCKED')
        status = self.budget.verified_status(session_id=current.session_id, at_utc=utc(self.store._clock()))
        available = status.available_quote
        if any((status.session_committed_quote > self.budget.session_limit_quote,
                status.day_committed_quote > self.budget.day_limit_quote,
                status.campaign_committed_quote > self.budget.campaign_limit_quote)):
            raise StorageError('EXIT_LOSS_BUDGET_EXCEEDED')
        if exclude is not None:
            available += self.budget.reserved_for(exclude.intent_id, session_id=exclude.session_id) or ZERO
        decision = evaluate_exit(ExitInputs(preview.life_balance - p.target_base, 'SELL', quantity,
                                            value.limit_price_usdt, value.independent_value_usdt, p.max_slippage_bps, available))
        cost = (decision.expected_loss_quote or ZERO) + quantity * value.limit_price_usdt * value.fee_rate
        if not decision.allowed or cost > available:
            raise StorageError(decision.reason_code if not decision.allowed else 'EXIT_LOSS_BUDGET_EXCEEDED')
        self.reason_code = 'EXIT_READY'
        return ExitCandidate(True, quantity, value.limit_price_usdt, cost), observed

    def propose(self):
        self.store.assert_worker_thread()
        try:
            self.recover()
            meta = self._read()
            if (self.store._wal.active_records() or self.service.quotes.native.action_journal.active_records()
                    or any(r['state'] not in ('TERMINAL', 'REJECTED') for r in meta.resources[self.children_key].values())):
                return ()
            decision, _ = self._decision()
            config = OrderExecutorConfig(id='exit:' + uuid4().hex, timestamp=self.store._clock().timestamp(),
                                         controller_id=self.service.controller.config.id,
                                         connector_name=self.service.controller.config.strategy.spot.connector,
                                         trading_pair='LIFE-USDT', side=TradeType.SELL, amount=decision.quantity_base,
                                         price=decision.price_usdt, level_id='0', execution_strategy=ExecutionStrategy.LIMIT_MAKER)
            current = self.service.controller._order_safety_manager.current_session
            children = thaw(self._read().resources[self.children_key])
            children[config.id] = {'config': config.model_dump_json(), 'owner': self.service.spot.owner.json(),
                                   'state': 'CLAIMING', 'session': [current.session_id, current.epoch, current.config_version], 'target': None}
            self.store._commit(self._read().cut, self.service.spot.owner, 'exit-propose:' + config.id,
                               ((self.children_key, encode(children)),))
            self._finish_claim(config.id)
            self._configs[config.id] = config.model_dump_json()
            self.service.quotes.native._issued[config.id] = (current.session_id, current.epoch, current.config_version, config)
            return ((config.controller_id, config.model_dump_json()),)
        except Exception as error:
            self.reason_code = getattr(error, 'code', 'EXIT_UNQUALIFIED')
            return ()
