"""Bounded inventory children on the same wallet, WAL and subsidy authority.

Campaign terms never reset at retry. This application journals proposals only;
preparation, fees, fills, loss, terminal and net-flat settlement stay in the
existing financial repository. Cold readback restores no volatile child config.
"""
from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from hummingbot.core.data_type.common import TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.action_journal import QuoteActionRecord
from hummingbot.strategy_v2.life_liquidity.inventory_execution import InventoryExecutionSnapshot, plan_inventory_child
from hummingbot.strategy_v2.life_liquidity.inventory_runner import InventoryMarketObservation
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerMode,
    StorageError,
    digest,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import ControlReceipt
from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.spot_application import SpotAdmission

ZERO = Decimal('0')


@dataclass(frozen=True)
class InventoryModeCommand:
    operation_id: str
    mode: str
    campaign_id: str
    policy_digest: str

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.campaign_id)
        digest(self.policy_digest)
        if len(self.operation_id) > 96 or self.mode not in ('MM', 'INVENTORY'):
            raise StorageError('SERVICE_INVENTORY_MODE_INVALID')

    def json(self):
        return {'kind': 'INVENTORY_MODE', 'operation_id': self.operation_id, 'mode': self.mode, 'campaign_id': self.campaign_id, 'policy_digest': self.policy_digest}


class FileInventoryApplication:
    children_key = 'inventory_children'

    def __init__(self, service):
        self.service, self.store, self.policy = service, service.store, service.config.inventory
        self.budget = self.store._subsidy
        self._configs = {}
        self.reason_code = 'INVENTORY_NOT_REQUESTED'
        self._quote_admission = service.spot.admission

    @classmethod
    async def install(cls, service, create):
        app = cls(service)
        await app.store._call(app._install, create)
        return app

    def _install(self, create):
        self.store.assert_worker_thread()
        meta = self.store._read()
        prior = meta.resources.get('inventory_service_policy')
        if prior is None:
            if not create or not self.store._created_offline:
                raise StorageError('SERVICE_INVENTORY_IMPORT_REQUIRED')
            self.store._commit(meta.cut, self.service.spot.owner, 'inventory-service:bind', (
                ('inventory_service_policy', encode(self.policy.model_dump(mode='json'))),
                ('inventory_mode', encode({'mode': 'MM', 'control_operation': None})),
                (self.children_key, encode({}))))
        elif thaw(prior) != self.policy.model_dump(mode='json'):
            raise StorageError('SERVICE_INVENTORY_POLICY_MISMATCH')
        self.service.spot.admission = self.admission

    def _read(self):
        self.store.assert_worker_thread()
        meta = self.store._read()
        if (self.service.config.inventory != self.policy
                or thaw(meta.resources.get('inventory_service_policy')) != self.policy.model_dump(mode='json')
                or self.budget is not self.store._subsidy):
            raise StorageError('SERVICE_INVENTORY_POLICY_CHANGED')
        return meta

    def mm_ready(self):
        try:
            return self._read().resources['inventory_mode']['mode'] == 'MM'
        except Exception:
            return False

    def handles(self, config):
        original = self._configs.get(getattr(config, 'id', None))
        return original is not None and original == config.model_dump_json()

    def apply_mode(self, command, approval, snapshot):
        meta = self._read()
        row = meta.resources.get('control_operations', {}).get(command.operation_id)
        if row is not None:
            if thaw(row['payload']) != command.json() or row['approval'] != approval.body():
                raise StorageError('SERVICE_INVENTORY_CONTROL_CONFLICT')
            return ControlReceipt(self.store.binding, command.operation_id, meta.ownership.mode, meta.ownership.token, True)
        self.store._operator_keys.verify(approval, self.store.binding, 'INVENTORY', command.json(), snapshot.cut,
                                         snapshot.facts, utc(self.store._clock()))
        view = self.store._snapshot()
        facts = snapshot.facts
        if (command.campaign_id != self.policy.campaign_id or command.policy_digest != fingerprint(self.policy.model_dump(mode='json'))
                or view.cut != snapshot.cut
                or meta.ownership.mode is not OwnerMode.ACTIVE or meta.ownership.token != self.service.spot.owner
                or not self.service.spot.owner.valid_from <= utc(self.store._clock()) < self.service.spot.owner.expires_at
                or any(r.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND') for r in self.store._wal.all_records())
                or self.service.joint is not None and self.service.joint._recovery.capital.claim_ids()
                or facts.accepted_wire_ids or facts.runner_wire_ids
                or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0
                or self.store._incomplete(meta.resources) or self.store._reporting_pending(meta.resources)
                or self.service.quotes.native.action_journal.active_records()
                or any(v['state'] not in ('TERMINAL', 'REJECTED') for v in meta.resources[self.children_key].values())):
            raise StorageError('SERVICE_INVENTORY_QUIESCENCE_REQUIRED')
        self.store._verify_recovery_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                               meta.resources.get('cashflows', {}), facts)
        replayed = self.store._control_commit(command.operation_id, snapshot.cut, approval, command.json(),
                                              {'mode': meta.ownership.mode.value, 'token': meta.ownership.token.json()}, facts,
                                              {'inventory_mode': {'mode': command.mode, 'control_operation': command.operation_id}})
        return ControlReceipt(self.store.binding, command.operation_id, meta.ownership.mode, meta.ownership.token, replayed)

    def pending_exit_ready(self):
        # Session watchdog runs behind the admission barrier; readiness uses
        # fresh market/accounting proof, never that barrier as an activation grant.
        self.store.assert_worker_thread()
        s = self.service
        try:
            if (self._read().resources['inventory_mode']['mode'] != 'INVENTORY'
                    or s._revoked or not self.policy.started_at <= utc(self.store._clock()) < self.policy.policy.deadline_utc
                    or not s.financial_ready(check_loss=True) or not s.risk.ready(normal_only=True)):
                return False
            current = s.controller._order_safety_manager.current_session
            return s.quotes.native._current_snapshot(current, require_active=False) is not None
        except Exception:
            return False

    def permissions(self):
        self.store.assert_worker_thread()
        s, c = self.service, self.service.controller
        try:
            meta = self._read()
            return (not s._revoked and not s.admission_paused and not self.store._cold_unarmed
                    and meta.resources['inventory_mode']['mode'] == 'INVENTORY'
                    and s.runtime._local_ready() and s.risk.ready(normal_only=True)
                    and self.policy.started_at <= utc(self.store._clock()) < self.policy.policy.deadline_utc
                    and c._joint_recovery_ready() and c._carry_ready() and c._hedge_ready()
                    and c._spot_quote_gates_ready() and not c._runner_stops_pending() and not c.has_unverified_runner_order_events())
        except Exception:
            return False

    def _decision(self, *, exclude=None, proposal_id=None):
        if not self.permissions():
            raise StorageError('SERVICE_INVENTORY_PERMISSION_REQUIRED')
        s, cfg, p = self.service, self.policy, self.policy.policy
        meta, view = self._read(), self.store._snapshot()
        current = s.controller._order_safety_manager.current_session
        observed = s.quotes.native._current_snapshot(current, pre_send_intent_id=None if exclude is None else exclude.intent_id)
        market = s.sources.inventory_observation()
        now = utc(self.store._clock())
        if (observed is None or type(market) is not InventoryMarketObservation
                or market.source_kind != 'independent_market' or market.volume_anchor != cfg.campaign_id
                or type(market.observed_at_ms) is not int or not 0 <= int(now.timestamp() * 1000) - market.observed_at_ms <= cfg.max_age_ms
                or (market.best_bid_usdt, market.best_ask_usdt) != (observed.best_bid_usdt, observed.best_ask_usdt)
                or market.fee_rate != observed.costs.maker_fee_rate
                or (p.tick_size, p.lot_size, p.min_size_base) != (observed.rules.tick_size, observed.rules.lot_size, observed.rules.min_size)):
            raise StorageError('SERVICE_INVENTORY_MARKET_UNQUALIFIED')
        evidence = self.service.controller._own_depth_decision.evidence
        if evidence is None or market.independent_depth_base > min(evidence.bid_depth_base, evidence.ask_depth_base):
            raise StorageError('SERVICE_INVENTORY_DEPTH_UNPROVEN')
        children = meta.resources[self.children_key]
        fills = [SpotFillObservation.from_json(row['fill']) for row in meta.resources.get('fills', {}).values()
                 if row['intent_id'] in children]
        # Filled amounts, not the current wallet, determine remaining campaign
        # target; unrelated deposits cannot reset target or participation.
        filled = sum((v.quantity_base_life for v in fills), ZERO)
        filled_notional = sum((v.quantity_base_life * v.price_usdt for v in fills), ZERO)
        used = sum((max(ZERO, (v.price_usdt - p.benchmark_price_usdt if p.side == 'BUY' else
                               p.benchmark_price_usdt - v.price_usdt) * v.quantity_base_life)
                    + max(ZERO, -v.signed_fee * (v.independent_price_usdt if v.fee_currency == 'LIFE' else 1)) for v in fills), ZERO)
        filled_by_id = {}
        for row in meta.resources.get('fills', {}).values():
            filled_by_id[row['intent_id']] = filled_by_id.get(row['intent_id'], ZERO) + Decimal(row['fill']['quantity_base_life'])
        pending = pending_notional = ZERO
        for key, row in children.items():
            if row['state'] in ('REJECTED', 'TERMINAL') or key == proposal_id or exclude is not None and key == exclude.intent_id:
                continue
            child = OrderExecutorConfig.model_validate_json(row['config'])
            remainder = max(ZERO, child.amount - filled_by_id.get(key, ZERO))
            pending += remainder
            pending_notional += remainder * child.price
        own_volume = sum((Decimal(row['fill']['quantity_base_life']) for row in meta.resources.get('fills', {}).values()
                          if int(row['fill']['fill_at_ms']) >= int(cfg.started_at.timestamp() * 1000)), ZERO)
        base, quote = view.balance.available_base_life, view.balance.available_quote_usdt
        if exclude is not None:
            reservation = self.store._ledger.reservation_state(exclude.intent_id)
            if reservation is None or not self.store._ledger.matches_open_intent(exclude):
                raise StorageError('SERVICE_INVENTORY_ORIGINAL_HOLD_REQUIRED')
            command = next(row['command'] for row in meta.resources['commands'].values() if row['command']['intent_id'] == exclude.intent_id)
            base += (exclude.quantity_base if exclude.side == 'SELL' else ZERO) + Decimal(command['fee_buffer_base_life'])
            quote += (exclude.quantity_base * exclude.limit_price_usdt if exclude.side == 'BUY' else ZERO) + Decimal(command['fee_buffer_quote_usdt'])
        snapshot = InventoryExecutionSnapshot(now, market.best_bid_usdt, market.best_ask_usdt,
                                              market.public_volume_base, max(market.known_own_volume_base, own_volume),
                                              market.independent_depth_base, market.expected_impact_quote, base, quote, filled, pending,
                                              filled_notional, pending_notional, used, True, True)
        decision = plan_inventory_child(p, snapshot, fee_rate=market.fee_rate)
        available = view.budget.subsidy_available_usdt
        if exclude is not None:
            available += self.budget.reserved_for(exclude.intent_id, session_id=exclude.session_id) or ZERO
        if view.budget.loss_exhausted or decision.estimated_exit_loss_quote > available:
            raise StorageError('SERVICE_INVENTORY_ACCOUNT_BUDGET_DENIED')
        self.reason_code = decision.reason_code
        return decision, observed

    def propose(self):
        self.store.assert_worker_thread()
        try:
            self.recover()
            decision, observed = self._decision()
            if not decision.allowed:
                return ()
            meta = self._read()
            if self.store._wal.active_records():
                return ()
            if any(v['state'] not in ('REJECTED', 'TERMINAL') for v in meta.resources[self.children_key].values()):
                return ()
            config = OrderExecutorConfig(id='inventory:' + uuid4().hex, timestamp=self.store._clock().timestamp(),
                                         controller_id=self.service.controller.config.id, connector_name=self.service.controller.config.strategy.spot.connector,
                                         trading_pair='LIFE-USDT', side=TradeType[self.policy.policy.side], amount=decision.quantity_base,
                                         price=decision.price_usdt, level_id='0', execution_strategy=ExecutionStrategy.LIMIT_MAKER)
            current = self.service.controller._order_safety_manager.current_session
            row = {'config': config.model_dump_json(), 'owner': self.service.spot.owner.json(), 'state': 'CLAIMING',
                   'session': [current.session_id, current.epoch, current.config_version], 'target': None}
            children = thaw(meta.resources[self.children_key])
            children[config.id] = row
            self.store._commit(meta.cut, self.service.spot.owner, 'inventory-propose:' + config.id, ((self.children_key, encode(children)),))
            self._finish_claim(config.id)
            self._configs[config.id] = config.model_dump_json()
            self.service.quotes.native._issued[config.id] = (current.session_id, current.epoch, current.config_version, config)
            return ((config.controller_id, config.model_dump_json()),)
        except Exception as error:
            self.reason_code = getattr(error, 'code', 'SERVICE_INVENTORY_UNQUALIFIED')
            return ()

    def _finish_claim(self, key):
        meta = self._read()
        children = thaw(meta.resources[self.children_key])
        row = children[key]
        config = OrderExecutorConfig.model_validate_json(row['config'])
        session, epoch, version = row['session']
        self.service.quotes.native.action_journal.claim_batch([QuoteActionRecord(key, config.controller_id, session, epoch,
                                                                                 version, config.trading_pair, config.side.name, 0)])
        row['state'] = 'PROPOSED'
        self.store._commit(meta.cut, self.service.spot.owner, 'inventory-proposed:' + key, ((self.children_key, encode(children)),), observation=True)

    def authorizes_config(self, config, *, exclude=None):
        try:
            if not self.handles(config):
                return False
            row = self._read().resources[self.children_key][config.id]
            if row['state'] not in ('PROPOSED', 'DISPATCHED') or row['owner'] != self.service.spot.owner.json():
                return False
            # Final qualification may exclude only the original, zero-fill
            # native hold already issued by this process. Cold configs stay empty.
            if exclude is None and self.store._wal.has_intent(config.id):
                exclude = self.service.controller._protected_spot_sender._issued_intents.get(config.id)
                if exclude is None or self.store._wal.get(config.id).state not in ('PREPARED', 'SEND_UNKNOWN'):
                    return False
            decision, _ = self._decision(exclude=exclude, proposal_id=config.id)
            return decision.allowed and decision.price_usdt == config.price and config.amount <= decision.quantity_base
        except Exception:
            return False

    def admission(self, config):
        if not self.handles(config):
            return self._quote_admission(config)
        if not self.authorizes_config(config):
            raise StorageError('SERVICE_INVENTORY_CHILD_CHANGED')
        decision, observed = self._decision(proposal_id=config.id)
        cfg = self.service.config.quotes
        if cfg.fee_buffer_quote_usdt < config.amount * config.price * observed.costs.maker_fee_rate or cfg.fee_buffer_base_life < config.amount * observed.costs.maker_fee_rate:
            raise StorageError('SERVICE_INVENTORY_FEE_BUFFER_INSUFFICIENT')
        return SpotAdmission(observed.qualified_reference_usdt, decision.estimated_exit_loss_quote,
                             cfg.fee_buffer_quote_usdt, cfg.fee_buffer_base_life)

    def authorizes_permit(self, permit, intent):
        raw = self._configs.get(permit.intent_id)
        if raw is None or permit.price_usdt != intent.limit_price_usdt or permit.quantity_base != intent.quantity_base:
            return False
        config = OrderExecutorConfig.model_validate_json(raw)
        if not self.authorizes_config(config, exclude=intent):
            return False
        decision, _ = self._decision(exclude=intent)
        held = self.budget.reserved_for(intent.intent_id, session_id=intent.session_id)
        return held is not None and held >= decision.estimated_exit_loss_quote

    def transition(self, payload, state):
        self.store.assert_worker_thread()
        config = OrderExecutorConfig.model_validate_json(payload[1])
        meta, children = self._read(), thaw(self._read().resources[self.children_key])
        row = children[config.id]
        if row['config'] != payload[1] or state not in ('DISPATCHED', 'REJECTED'):
            raise StorageError('SERVICE_INVENTORY_ACTION_CONFLICT')
        if row['state'] == state:
            return True
        if row['state'] == 'PROPOSED':
            row['state'], row['target'] = 'TRANSITIONING', state
            self.store._commit(meta.cut, self.service.spot.owner, 'inventory-action:' + state + ':' + config.id,
                               ((self.children_key, encode(children)),), observation=True)
        elif row['state'] != 'TRANSITIONING' or row['target'] != state:
            raise StorageError('SERVICE_INVENTORY_ACTION_CONFLICT')
        claim = self.service.quotes.native.action_journal.verified_get(config.id)
        if claim.state == 'PROPOSED':
            self.service.quotes.native.action_journal.transition(config.id, expected='PROPOSED', state=state)
        elif claim.state != state:
            raise StorageError('SERVICE_INVENTORY_ACTION_NATIVE_CHANGED')
        row['state'], row['target'] = state, None
        self.store._commit(self._read().cut, self.service.spot.owner, 'inventory-action-done:' + state + ':' + config.id,
                           ((self.children_key, encode(children)),), observation=True)
        return True

    def recover(self):
        self.store.assert_worker_thread()
        meta = self._read()
        for key, row in meta.resources[self.children_key].items():
            if row['owner'] != self.service.spot.owner.json():
                if row['state'] in ('TERMINAL', 'REJECTED'):
                    continue
                raise StorageError('SERVICE_INVENTORY_ORIGINAL_WRITER_REQUIRED')
            if row['state'] == 'CLAIMING':
                self._finish_claim(key)
            elif row['state'] == 'TRANSITIONING':
                self.transition((self.service.controller.config.id, row['config']), row['target'])
            elif row['state'] in ('PROPOSED', 'DISPATCHED') and self.store._wal.has_intent(key):
                attempt = self.store._wal.get(key)
                if attempt.state in ('TERMINAL', 'ABORTED_BEFORE_SEND'):
                    self.service.quotes.native._release_reconciled_proposals()
                    current = self._read()
                    children = thaw(current.resources[self.children_key])
                    children[key]['state'] = 'TERMINAL'
                    self.store._commit(current.cut, self.service.spot.owner, 'inventory-terminal:' + key,
                                       ((self.children_key, encode(children)),), observation=True)
