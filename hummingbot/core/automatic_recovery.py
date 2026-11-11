"""Bounded, GET-only resolution of managed order submissions with a lost ACK.

Positive venue evidence may settle an attempt. Not-found, elapsed time and retry
count never prove absence. This worker has no order-submission authority.
"""
import asyncio
import random
import time
from decimal import Decimal

from sqlalchemy import func, select, update

from hummingbot.connector.okx_order_status import OKXOrderNotFoundError, order_status_data
from hummingbot.core.data_type.in_flight_order import OrderState, OrderUpdate
from hummingbot.core.order_submission_guard import MarketReadinessBlocked, SessionSubmissionBlocked, SubmissionBlocked
from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import SubmissionAttempt, credential_fingerprint, digest, environment_identity

MAX_ATTEMPTS = 64
PAGE_SIZE = 100
MAX_FILL_PAGES = 10
MANUAL_PROOFS = frozenset({'recovery_order_identity_mismatch', 'recovery_position_side_mismatch',
                          'recovery_fill_identity_mismatch', 'recovery_fill_conflict', 'recovery_fill_fee_invalid',
                           'recovery_account_binding_changed', 'recovery_position_application_conflict'})


def eligible(context):
    config, scope = context.get('executor') or {}, context.get('session')
    barriers, order = config.get('triple_barrier_config') or {}, context.get('order') or {}
    side = 'BUY' if config.get('side') in (1, 'BUY') else 'SELL' if config.get('side') in (2, 'SELL') else None
    common = (isinstance(scope, list) and len(scope) == 2
              and scope == [config.get('mm_session_id'), config.get('mm_generation')]
              and config.get('connector_name') in ('okx', 'okx_perpetual') and not context.get('cleanup')
              and order.get('trade_type') == side
              and order.get('order_type') == 'LIMIT_MAKER' and isinstance(context.get('credential_fingerprint'), str))
    opening = (config.get('type') == 'position_executor' and order.get('position') in ('OPEN', 'NIL')
               and not any(barriers.get(key) is not None for key in ('stop_loss', 'take_profit', 'time_limit', 'trailing_stop')))
    closing = (config.get('type') == 'order_executor' and config.get('connector_name') == 'okx_perpetual'
               and config.get('position_action') == order.get('position') == 'CLOSE'
               and config.get('execution_strategy') == 'LIMIT_MAKER' and type(config.get('mm_generation')) is int
               and all(isinstance(config.get(key), str) and config[key] for key in ('id', 'controller_id', 'level_id', 'mm_session_id')))
    return bool(common and (opening or closing))


def proof_covers_local(order, status, trades):
    observed = {trade.trade_id: trade for trade in trades}
    if not order.order_fills.keys() <= observed.keys():
        raise IOError('Newer native fills are not yet visible in the REST proof')
    if order.current_state in (OrderState.FILLED, OrderState.CANCELED) and status.new_state != order.current_state:
        raise IOError('The REST proof is older than native terminal evidence')
    for identity, local in order.order_fills.items():
        actual = observed[identity]
        if (actual.fill_base_amount != local.fill_base_amount or actual.fill_quote_amount != local.fill_quote_amount
                or actual.fill_price != local.fill_price or actual.fee != local.fee):
            raise SubmissionBlocked('recovery_fill_conflict')


def decode_order_evidence(connector, order, actual):
    size, filled = Decimal(str(actual['sz'])), Decimal(str(actual['accFillSz']))
    if connector.name == 'okx_perpetual':
        size = connector._format_size_to_amount(order.trading_pair, size)
        filled = connector._format_size_to_amount(order.trading_pair, filled)
    if (actual.get('clOrdId') != order.client_order_id or not actual.get('ordId')
            or actual.get('instId') != order.trading_pair + ('-SWAP' if connector.name == 'okx_perpetual' else '')
            or actual.get('side') != order.trade_type.name.lower() or actual.get('ordType') != 'post_only'
            or Decimal(str(actual['px'])) != order.price or size != order.amount
            or not filled.is_finite() or not 0 <= filled <= order.amount
            or order.exchange_order_id and str(actual['ordId']) != order.exchange_order_id):
        raise SubmissionBlocked('recovery_order_identity_mismatch')
    if connector.name == 'okx_perpetual':
        side = 'long' if (order.trade_type.name == 'BUY') == (order.position.name == 'OPEN') else 'short'
        if actual.get('posSide') != side:
            raise SubmissionBlocked('recovery_position_side_mismatch')
    state = {'live': OrderState.OPEN, 'partially_filled': OrderState.PARTIALLY_FILLED,
             'filled': OrderState.FILLED, 'canceled': OrderState.CANCELED,
             'mmp_canceled': OrderState.CANCELED}.get(actual.get('state'))
    if state is None or state == OrderState.FILLED and filled != order.amount:
        raise SubmissionBlocked('recovery_order_state_invalid')
    return state, filled


async def read_order_proof(connector, order):
    """Use native authenticated reads/parsers; validate identity and full fills."""
    async def status():
        actual = order_status_data(await connector._request_order_update(order))
        state, filled = decode_order_evidence(connector, order, actual)
        return actual, state, filled

    first, _, _ = await status()
    # The identity is positive evidence; the tracker still receives state only
    # after complete trade proof. This also enables the native fills endpoint.
    order.update_exchange_order_id(str(first['ordId']))
    after, observed = None, {}
    for _ in range(MAX_FILL_PAGES):
        response = await connector._request_order_fills(order, after=after, limit=PAGE_SIZE)
        if str(response.get('code')) != '0' or not isinstance(response.get('data'), list):
            raise IOError('recovery_fills_unavailable')
        page = response['data']
        if len(page) > PAGE_SIZE:
            raise SubmissionBlocked('recovery_fill_page_invalid')
        for raw in page:
            if (str(raw.get('ordId')) != order.exchange_order_id or raw.get('instId') != first['instId']
                    or raw.get('side') != first['side']
                    or connector.name == 'okx_perpetual' and raw.get('posSide') != first['posSide']):
                raise SubmissionBlocked('recovery_fill_identity_mismatch')
            if (not raw.get('tradeId') or not raw.get('feeCcy') or not Decimal(str(raw.get('fee'))).is_finite()):
                raise SubmissionBlocked('recovery_fill_fee_invalid')
            trade = (connector._trade_update_from_fill(order, raw) if connector.name == 'okx'
                     else connector._parse_trade_update(raw, order))
            if (not trade.fill_base_amount.is_finite() or trade.fill_base_amount <= 0
                    or not trade.fill_price.is_finite() or trade.fill_price <= 0):
                raise SubmissionBlocked('recovery_fill_values_invalid')
            previous = observed.get(trade.trade_id)
            if previous is not None and previous != trade:
                raise SubmissionBlocked('recovery_fill_conflict')
            observed[trade.trade_id] = trade
        if len(page) < PAGE_SIZE:
            break
        cursor = str(page[-1].get('billId') or '')
        if not cursor or cursor == after:
            raise SubmissionBlocked('recovery_fill_cursor_invalid')
        after = cursor
    else:
        raise SubmissionBlocked('recovery_fill_scope_limit')
    last, state, filled = await status()
    if str(first['ordId']) != str(last['ordId']) or sum((t.fill_base_amount for t in observed.values()), Decimal(0)) != filled:
        raise IOError('recovery_fill_coverage_incomplete')
    return (OrderUpdate(order.trading_pair, int(last['uTime']) / 1000, state,
                        order.client_order_id, order.exchange_order_id, {'venue_order': last}), list(observed.values()))


def durable_fills(connection, identity, trades, *, missing_ok=False):
    from hummingbot.connector.markets_recorder import MarketsRecorder
    from hummingbot.model.trade_fill import TradeFill
    fills = connection.execute(select(*TradeFill.__table__.c).where(TradeFill.order_id == identity).limit(1001)).mappings().all()
    expected = {trade.trade_id: trade for trade in trades}
    if len(fills) != len(expected) or {fill['exchange_trade_id'] for fill in fills} != expected.keys():
        if missing_ok:
            return False
        raise SubmissionBlocked('recovery_fill_not_durable')
    for fill in fills:
        trade = expected[fill['exchange_trade_id']]
        if (fill['amount'] != trade.fill_base_amount or fill['price'] != trade.fill_price
                or fill['symbol'] != trade.trading_pair
                or not MarketsRecorder._same_trade_fee(fill['trade_fee'], trade.fee.to_json())):
            raise SubmissionBlocked('recovery_fill_conflict')
    return True


def persist_proved_trades(recorder, connector, order, trades):
    from hummingbot.core.event.events import MarketEvent, OrderFilledEvent
    from hummingbot.model.trade_fill import TradeFill
    recorder.sql_manager.assert_persistence_healthy()
    for trade in trades:
        connector._order_tracker.process_trade_update(trade)
    with recorder.sql_manager.get_new_session() as session:
        recorded = set(session.execute(select(TradeFill.exchange_trade_id).where(TradeFill.order_id == order.client_order_id)).scalars())
    for trade in trades:
        if trade.trade_id not in recorded:
            recorder._did_fill_order(MarketEvent.OrderFilled.value, connector, OrderFilledEvent(
                timestamp=trade.fill_timestamp, order_id=order.client_order_id, trading_pair=order.trading_pair,
                trade_type=order.trade_type, order_type=order.order_type, price=trade.fill_price,
                amount=trade.fill_base_amount, trade_fee=trade.fee, exchange_trade_id=trade.trade_id,
                leverage=order.leverage, position=order.position.value, exchange_order_id=trade.exchange_order_id))
    recorder.sql_manager.assert_persistence_healthy()


class AutomaticOrderRecovery:
    def __init__(self, owner):
        self.owner = owner
        self.status = {'phase': 'idle', 'operator_required': False, 'cold_recovery_supported': False}
        self.next_poll = 0
        self.failures = 0
        self.task = None
        self.resume_scope = None
        self.bootstrap_orders = {}
        self.terminal_slots = asyncio.Semaphore(3)

    @property
    def active(self):
        return self.status['phase'] in ('reconciling', 'waiting_for_readiness', 'bootstrapping_recovery', 'settled_stopped')

    def _attempt(self, identity):
        with self.owner.engine.connect() as connection:
            row = connection.execute(select(SubmissionAttempt).where(SubmissionAttempt.order_id == identity)).mappings().first()
            return dict(row) if row else None

    def _audit(self, action, identity, outcome, reason):
        from hummingbot.core.admin_identity import Principal
        from hummingbot.model.admin_access import AdminAccessStore
        AdminAccessStore(self.owner.engine, self.owner.store.bot_id).audit(
            self.owner.connection, actor=Principal('owner:' + self.owner.owner_uuid, 'runtime', 'write'),
            action=action, outcome=outcome, request_id='automatic:' + identity, target_id=identity,
            reason_code=reason, details={'version': self.owner.generation, 'order_id': identity})

    def pause(self, identity, *, cancel=False):
        self.owner.check_owner()
        attempt = self._attempt(identity)
        if not attempt or not eligible(attempt['context']) or identity in self.owner.cleanup_intents:
            return False
        if not cancel and attempt['state'] in ('acknowledged', 'terminal'):
            with self.owner.engine.connect() as connection:
                saved = connection.execute(select(Order.creation_pending, Order.exchange_order_id).where(Order.id == identity)).one()
            if not saved.creation_pending and saved.exchange_order_id:
                return True  # WS already supplied the durable ACK before HTTP failed.
        if attempt['state'] not in ('possibly_sent', 'uncertain', 'acknowledged', 'terminal'):
            return False
        self.owner.allow_new_orders = False
        self.owner.check_owner()
        status = dict(phase='reconciling', reason_code='cancel_outcome_unknown' if cancel else 'order_outcome_unknown',
                      operator_required=False, started_at=self.status.get('started_at', time.time()) if self.active else time.time(),
                      cold_recovery_supported=self.status.get('cold_recovery_supported', False),
                      next_retry_at=time.time(), order_id=identity)

        def record(state):
            if (state.get('halt') and state.get('halt_kind') != 'shutdown_pending') or state.get('pending_mutations'):
                raise SubmissionBlocked('Manual recovery already owns the safety stop')
            self.owner.connection.execute(update(SubmissionAttempt).where(
                SubmissionAttempt.order_id == identity).values(state='uncertain'))
            state['automatic_recovery'] = status
            if attempt['state'] != 'uncertain':
                self._audit('automatic_recovery_paused', identity, 'applying', 'order_outcome_unknown')

        self.owner._mutate_recovery(record)
        self.status = status
        self.owner.sent_intents.add(identity)
        self.resume_scope = tuple(attempt['context']['session'])
        self.next_poll = 0
        return True

    def _pending(self):
        with self.owner.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(SubmissionAttempt).where(
                SubmissionAttempt.state == 'uncertain').order_by(SubmissionAttempt.order_id).limit(MAX_ATTEMPTS + 1)).mappings()]

    def _bindings(self):
        from hummingbot.model.exchange_credential import ExchangeCredential
        with self.owner.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(
                ExchangeCredential.connector, ExchangeCredential.environment, ExchangeCredential.account_id,
                ExchangeCredential.version).where(ExchangeCredential.bot_id == self.owner.store.bot_id,
                                                  ExchangeCredential.state == 'active')).mappings()]

    def _bootstrap_pending(self, core):
        from hummingbot.model.runtime_journal import RuntimeJournal
        with core.trade_fill_db.get_new_session() as session:
            ids = set(session.execute(select(SubmissionAttempt.order_id).where(SubmissionAttempt.state.in_(
                ('prepared', 'possibly_sent', 'uncertain', 'acknowledged'))).limit(MAX_ATTEMPTS + 1)).scalars())
            ids.update(session.execute(select(Order.id).where(Order.creation_pending.is_(True)).limit(MAX_ATTEMPTS + 1)).scalars())
            ids.update(session.execute(select(RuntimeJournal.order_id).where(RuntimeJournal.kind == 'fill',
                                                                             RuntimeJournal.applied.is_(False)).distinct().limit(MAX_ATTEMPTS + 1)).scalars())
            if len(ids) > MAX_ATTEMPTS:
                raise SubmissionBlocked('automatic_recovery_scope_limit')
            rows = session.execute(select(SubmissionAttempt).where(SubmissionAttempt.order_id.in_(ids))).scalars().all()
            if {row.order_id for row in rows} != ids or any(not eligible(row.context) for row in rows):
                raise SubmissionBlocked('automatic_recovery_context_missing')
            return [dict(order_id=row.order_id, state=row.state, context=row.context) for row in rows]

    def _restore_order(self, connector, attempt):
        from hummingbot.core.data_type.in_flight_order import InFlightOrder
        order = connector._order_tracker.fetch_order(attempt['order_id'])
        if order is None:
            order = InFlightOrder.from_json(attempt['context']['order'])
            connector._order_tracker.start_tracking_order(order)
        self.owner.active_intents.add(order.client_order_id)
        self.owner.sent_intents.add(order.client_order_id)
        self.owner.intent_scopes[order.client_order_id] = tuple(attempt['context']['session'])
        order._managed_recovery_order = eligible(attempt['context'])
        return order

    def _record_verified(self, core, attempt, status, trades):
        core.trade_fill_db.assert_persistence_healthy()

        def settled(state):
            saved = self.owner.connection.execute(select(Order).where(Order.id == attempt['order_id'])).mappings().one()
            if saved['creation_pending'] or saved['exchange_order_id'] != status.exchange_order_id:
                raise SubmissionBlocked('recovery_ack_not_durable')
            durable_fills(self.owner.connection, attempt['order_id'], trades)
            self.owner.connection.execute(update(SubmissionAttempt).where(
                SubmissionAttempt.order_id == attempt['order_id']).values(
                    state='terminal' if status.new_state in (OrderState.FILLED, OrderState.CANCELED) else 'acknowledged',
                    receipt={'exchange_order_id': status.exchange_order_id, 'state': status.new_state.name,
                             'fill_ids': sorted(t.trade_id for t in trades)}))
            self._audit('automatic_recovery_order_verified', attempt['order_id'], 'succeeded', 'positive_order_proof')
        self.owner._mutate_recovery(settled)

    async def _merge(self, core, connector, attempt, order):
        status, trades = await asyncio.wait_for(read_order_proof(connector, order), timeout=10)
        self.owner.check_owner()
        if (attempt['context'].get('environment') != environment_identity()
                or attempt['context']['credential_fingerprint'] != credential_fingerprint(connector)
                or sorted(attempt['context']['bindings'], key=digest) != sorted(self._bindings(), key=digest)):
            raise SubmissionBlocked('recovery_account_binding_changed')
        proof_covers_local(order, status, trades)
        persist_proved_trades(core.markets_recorder, connector, order, trades)
        await connector._order_tracker.process_order_update(status)
        proof_covers_local(order, status, trades)
        self._record_verified(core, attempt, status, trades)
        return status, trades

    async def terminal_update(self, connector, order, update):
        """A cancel ACK is not terminal; complete private evidence stays fast."""
        from hummingbot.connector.markets_recorder import MarketsRecorder
        self.owner.check_owner()
        recorder = MarketsRecorder._shared_instance
        if recorder is None or recorder.sql_manager.engine is not self.owner.engine:
            raise SubmissionBlocked('Terminal proof has no matching recorder')
        task = asyncio.current_task()
        self.owner.terminal_tasks.add(task)
        try:
            raw = (update.misc_updates or {}).get('venue_order')
            if isinstance(raw, dict):
                try:
                    state, filled = decode_order_evidence(connector, order, raw)
                except (KeyError, ValueError, ArithmeticError):
                    state, filled = None, None
                if state == update.new_state and filled == order.executed_amount_base:
                    with self.owner.engine.connect() as connection:
                        if durable_fills(connection, order.client_order_id, list(order.order_fills.values()), missing_ok=True):
                            order._managed_terminal_proved = True
                            return update
            async with self.terminal_slots:
                self.owner.check_owner()
                proof, trades = await asyncio.wait_for(read_order_proof(connector, order), timeout=10)
                self.owner.check_owner()
                proof_covers_local(order, proof, trades)
                persist_proved_trades(recorder, connector, order, trades)
                with self.owner.engine.connect() as connection:
                    durable_fills(connection, order.client_order_id, trades)
                order._managed_terminal_proved = True
                return proof
        finally:
            self.owner.terminal_tasks.discard(task)

    async def restore(self, core):
        """Before native startup cancellation, restore only journaled live effects."""
        from hummingbot.model.runtime_journal import RuntimeCheckpoint
        with core.trade_fill_db.get_new_session() as session:
            checkpoint = session.get(RuntimeCheckpoint, self.owner.config_file_path)
            anchors = (checkpoint.payload if checkpoint else {}).get('credential_fingerprints') or {}
        for name, connector in self.owner.connectors.items():
            if name in anchors and anchors[name] != credential_fingerprint(connector):
                raise SubmissionBlocked('recovery_account_binding_changed')
        self.bootstrap_orders = {}
        for attempt in self._bootstrap_pending(core):
            context = attempt['context']
            if (context.get('environment') != environment_identity() or context['config_file_path'] != self.owner.config_file_path
                    or sorted(context['bindings'], key=digest) != sorted(self._bindings(), key=digest)):
                raise SubmissionBlocked('recovery_account_binding_changed')
            connector = self.owner.connectors[context['executor']['connector_name']]
            if context['credential_fingerprint'] != credential_fingerprint(connector):
                raise SubmissionBlocked('recovery_account_binding_changed')
            order = self._restore_order(connector, attempt)
            if attempt['state'] == 'prepared':
                # Every transport must commit possibly_sent first. A prepared
                # record under a successor fence therefore proves no send.
                core.markets_recorder.reject_unsent_order(connector, attempt['order_id'], require_existing=True)
                await connector._order_tracker.process_order_update(OrderUpdate(
                    order.trading_pair, time.time(), OrderState.FAILED, order.client_order_id))
                self.owner.sent_intents.discard(order.client_order_id)
                continue
            self.bootstrap_orders[order.client_order_id] = (attempt, order)
            failures = 0
            while True:
                self.owner.check_owner()
                try:
                    await self._merge(core, connector, attempt, order)
                    break
                except (IOError, asyncio.TimeoutError):
                    # Includes explicit 51603. A long outage remains a visible
                    # initialization pause, not a repeated POST or timed reset.
                    failures = min(failures + 1, 5)
                    delay = min(15, 2 ** (failures - 1)) * random.uniform(.8, 1.2)
                    self.status.update(phase='bootstrapping_recovery', reason_code='order_proof_pending',
                                       next_retry_at=time.time() + delay)
                    self.owner._mutate_recovery(lambda state: state.update(automatic_recovery=dict(self.status)))
                    await asyncio.sleep(delay)

    async def project(self, core):
        """Apply only terminal, fully proved orders missing their native hold transfer."""
        from hummingbot.model.runtime_journal import RuntimeJournal
        from hummingbot.strategy_v2.executors.order_executor.data_types import OrderExecutorConfig
        from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig
        from hummingbot.strategy_v2.models.base import RunnableStatus
        from hummingbot.strategy_v2.models.executors import CloseType
        from hummingbot.strategy_v2.models.executors_info import ExecutorInfo
        proved = {}
        for identity, (attempt, order) in self.bootstrap_orders.items():
            connector = self.owner.connectors[attempt['context']['executor']['connector_name']]
            status, trades = await self._merge(core, connector, attempt, order)
            if status.new_state not in (OrderState.FILLED, OrderState.CANCELED):
                raise IOError('Recovery is waiting for native startup cancellation to become terminal')
            proved[identity] = status, trades
        with core.trade_fill_db.get_new_session() as session:
            ids = list(session.execute(select(RuntimeJournal.order_id).where(RuntimeJournal.kind == 'fill',
                                                                             RuntimeJournal.applied.is_(False)).group_by(RuntimeJournal.order_id).order_by(func.min(RuntimeJournal.id)).limit(MAX_ATTEMPTS + 1)).scalars())
        if len(ids) > MAX_ATTEMPTS:
            raise SubmissionBlocked('automatic_recovery_scope_limit')
        groups = {}
        for identity in ids:
            attempt = self._attempt(identity)
            if not attempt or not eligible(attempt['context']):
                raise SubmissionBlocked('recovery_fill_context_missing')
            raw = attempt['context']['executor']
            model = OrderExecutorConfig if raw.get('type') == 'order_executor' else PositionExecutorConfig
            config = model.model_validate(raw)
            orchestrator = core.strategy.executor_orchestrator
            if (not orchestrator._mm_contains(config.controller_id, config.connector_name, config.trading_pair)
                    or core.markets_recorder.applied_position_executors([config.id])):
                raise SubmissionBlocked('recovery_position_application_conflict')
            connector = self.owner.connectors[config.connector_name]
            order = self._restore_order(connector, attempt)
            status, trades = proved.get(identity) or await self._merge(core, connector, attempt, order)
            if status.new_state not in (OrderState.FILLED, OrderState.CANCELED):
                raise SubmissionBlocked('recovery_terminal_proof_required')
            from hummingbot.model.runtime_journal import held_order_snapshot
            saved = held_order_snapshot(order)
            group = groups.setdefault(config.id, {'config': config, 'orders': []})
            group['orders'].append(saved)
        for group in groups.values():
            config, orders = group['config'], group['orders']
            info = ExecutorInfo(id=config.id, timestamp=config.timestamp, type=config.type,
                                status=RunnableStatus.TERMINATED, close_type=CloseType.POSITION_HOLD,
                                close_timestamp=time.time(), config=config, controller_id=config.controller_id,
                                net_pnl_pct=Decimal(0), net_pnl_quote=Decimal(0), is_active=False, is_trading=False,
                                cum_fees_quote=sum((Decimal(o['cumulative_fee_paid_quote']) for o in orders), Decimal(0)),
                                filled_amount_quote=sum((Decimal(o['executed_amount_quote']) for o in orders), Decimal(0)),
                                custom_info={'held_position_orders': orders, 'recovered': True})
            core.strategy.executor_orchestrator._update_positions_from_done_executors({config.controller_id: [info]})
            core.strategy.executor_orchestrator._update_cached_performance(config.controller_id, info)

    async def step(self, core, *, force=False):
        if not self.active or not force and time.monotonic() < self.next_poll:
            return
        self.owner.check_owner()
        core.trade_fill_db.assert_persistence_healthy()
        pending = self._pending()
        if len(pending) > MAX_ATTEMPTS:
            self.owner.require_recovery('automatic_recovery_scope_limit')
            self.status.update(phase='manual_required', operator_required=True, reason_code='automatic_recovery_scope_limit')
            return
        try:
            # One bounded worker per bot; no task per lifetime order and no POST.
            for attempt in pending:
                context = attempt['context']
                if not eligible(context) or sorted(context['bindings'], key=digest) != sorted(self._bindings(), key=digest):
                    raise SubmissionBlocked('recovery_account_binding_changed')
                connector = self.owner.connectors[context['executor']['connector_name']]
                order = connector._order_tracker.fetch_order(attempt['order_id'])
                if order is None:
                    order = self._restore_order(connector, attempt)
                await self._merge(core, connector, attempt, order)
                self.status['last_progress_at'] = time.time()
            row = self.owner.check_owner()
            recovery = row['recovery_state'] or {}
            if (recovery.get('halt') or recovery.get('pending_mutations') or self.owner.shutdown_requested
                    or row['desired_state'] != 'running' or row['control_version'] != self.owner.version
                    or row['config_file_path'] != self.owner.config_file_path):
                self.status.update(phase='settled_stopped', reason_code='current_control_blocks_resume')
                self.owner._mutate_recovery(lambda state: state.update(automatic_recovery=dict(self.status)))
                return
            if self.resume_scope is not None:
                self.owner._check_mm_authority(row, self.resume_scope, submission=False)
            elif self.owner.mm_ready_check is not None and not self.owner.mm_ready_check():
                raise SubmissionBlocked('recovery_readiness_pending')
            self.owner._check_intents(core.markets_recorder)
            self.status.update(phase='idle', reason_code=None, operator_required=False)

            def resume(state):
                if state.get('halt') or state.get('pending_mutations'):
                    raise SessionSubmissionBlocked('Current safety state blocks resume')
                state.update(automatic_recovery=dict(self.status))
                self._audit('automatic_recovery_resumed', self.status.get('order_id', 'runtime'),
                            'succeeded', 'current_authority_verified')

            if self.owner._mutate_recovery(resume, expected_version=self.owner.version):
                self.owner.allow_new_orders = True
            self.failures = 0
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.owner.check_owner()  # Never hide loss of the physical fence.
            core.trade_fill_db.assert_persistence_healthy()
            if isinstance(error, SubmissionBlocked) and str(error) in MANUAL_PROOFS:
                self.owner.require_recovery('Automatic recovery evidence conflicts with its durable account/financial scope.')
                self.status.update(phase='manual_required', reason_code=str(error), operator_required=True)
                self.owner._mutate_recovery(lambda state: state.update(automatic_recovery=dict(self.status)))
                return
            self.failures = min(self.failures + 1, 10)
            delay = min(15, 2 ** min(self.failures - 1, 4)) * random.uniform(.8, 1.2)
            self.next_poll = time.monotonic() + delay
            reason = 'order_not_found_inconclusive' if isinstance(error, OKXOrderNotFoundError) else 'order_proof_pending'
            phase = 'reconciling'
            if isinstance(error, MarketReadinessBlocked):
                phase, reason = 'waiting_for_readiness', 'market_readiness_pending'
            elif isinstance(error, SessionSubmissionBlocked):
                phase, reason = 'settled_stopped', 'session_authority_changed'
            self.status.update(phase=phase, reason_code=reason, next_retry_at=time.time() + delay,
                               operator_required=time.time() - self.status.get('started_at', time.time()) >= 300)
            self.owner._mutate_recovery(lambda state: state.update(automatic_recovery=dict(self.status)))

    def poll(self, core):
        if self.task is not None and self.task.done():
            self.task.result()
            self.task = None
        if self.active and self.task is None and time.monotonic() >= self.next_poll:
            self.task = asyncio.create_task(self.step(core))

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            done, pending = await asyncio.wait({self.task}, timeout=1)
            if pending:
                raise RuntimeError('Automatic reconciliation did not drain; retain ownership until process exit')
            await asyncio.gather(*done, return_exceptions=True)
            self.task = None
