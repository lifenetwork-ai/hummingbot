"""Bounded, GET-only resolution of managed order submissions with a lost ACK.

Positive venue evidence may settle an attempt. Not-found, elapsed time and retry
count never prove absence. This worker has no order-submission authority.
"""
import asyncio
import random
import time
from contextvars import copy_context
from copy import deepcopy
from decimal import Decimal

from sqlalchemy import func, select, update

from hummingbot.connector.okx_order_status import OKXOrderNotFoundError, is_expired_rejection_receipt, order_status_data
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
        self.durable_read_slot = asyncio.Semaphore(1)
        self.quarantined = {}
        self.attempt_retry = {}

    @property
    def isolated(self):
        return bool(getattr(self.owner, 'scoped_recovery_enabled', False) is True and self.active and self.quarantined
                    and self.status.get('cold_recovery_supported') is True)

    def _isolate(self, pending):
        """Opt-in eligibility is never proof of an order's terminal outcome."""
        self.quarantined = {}
        self.status.update(isolated=False, quarantined_orders=0, admission_mode='paused',
                           reserved_base={}, reserved_quote_worst_case='0')
        if getattr(self.owner, 'scoped_recovery_enabled', False) is not True or self.status.get('cold_recovery_supported') is not True:
            return False
        from hummingbot.core.uncertain_order_reservations import cohort
        row = self.owner.check_owner()
        maximum = (((row.get('recovery_state') or {}).get('spot_outflow') or {}).get('policy') or {}).get('max_net_outflow_quote')
        current = sorted(self._bindings(), key=digest)
        try:
            for attempt in pending:
                if (attempt['context'].get('bot_id') != self.owner.store.bot_id or not eligible(attempt['context'])
                        or attempt['context']['credential_fingerprint'] != credential_fingerprint(
                            self.owner.connectors[attempt['context']['executor']['connector_name']])):
                    return False
            normalized = deepcopy(pending)
            for attempt in normalized:
                attempt['context']['bindings'] = sorted(attempt['context']['bindings'], key=digest)
            holds, quote = cohort(normalized, bindings=current, environment=environment_identity(),
                                  config_file_path=self.owner.config_file_path, max_quote=maximum)
        except (KeyError, ValueError, ArithmeticError, TypeError, MarketReadinessBlocked):
            return False
        self.quarantined = {attempt['order_id']: attempt for attempt in pending}
        self.attempt_retry = {identity: retry for identity, retry in self.attempt_retry.items() if identity in self.quarantined}
        self.status.update(isolated=True, quarantined_orders=len(self.quarantined), admission_mode='funded_spot_sell',
                           reserved_base={key: str(value) for key, value in holds.items()}, reserved_quote_worst_case=str(quote))
        return True

    def authorize_isolated_order(self, connector, order, *, payload=None):
        from hummingbot.connector.markets_recorder import MarketsRecorder
        from hummingbot.core.uncertain_order_reservations import authorize_cash_sell, cohort
        self.owner.check_owner(new_risk=True)
        recorder = MarketsRecorder._shared_instance
        self._terminal_fence(recorder)
        pending = self._pending()  # Fresh durable scope immediately before each send.
        if not self._isolate(pending):
            self.owner.allow_new_orders = False
            raise MarketReadinessBlocked('uncertain_reservation_unavailable')
        # Other live POSTs may have crossed transport before their callback
        # marks them uncertain. Reserve those effects too, without letting the
        # proof worker race the still-running submission coroutine.
        sends = [attempt for attempt in self._pending(all_sends=True) if attempt['order_id'] != order.client_order_id]
        normalized = deepcopy(sends)
        for attempt in normalized:
            current_connector = self.owner.connectors.get(attempt['context']['executor'].get('connector_name'))
            if (current_connector is None or attempt['context'].get('bot_id') != self.owner.store.bot_id
                    or not eligible(attempt['context'])
                    or attempt['context'].get('credential_fingerprint') != credential_fingerprint(current_connector)):
                raise MarketReadinessBlocked('uncertain_reservation_scope')
            attempt['context']['bindings'] = sorted(attempt['context']['bindings'], key=digest)
        row = self.owner.check_owner(new_risk=True)
        maximum = row['recovery_state']['spot_outflow']['policy']['max_net_outflow_quote']
        holds, _ = cohort(normalized, bindings=sorted(self._bindings(), key=digest), environment=environment_identity(),
                          config_file_path=self.owner.config_file_path, max_quote=maximum)
        if order.client_order_id in self.quarantined:
            raise MarketReadinessBlocked('uncertain_identity_reserved')
        if payload is not None:
            expected = dict(clOrdId=order.client_order_id, tdMode='cash', ordType='post_only', side='sell',
                            instId=order.trading_pair)
            try:
                attempt = self.owner.connection.execute(select(SubmissionAttempt.context).where(
                    SubmissionAttempt.order_id == order.client_order_id)).scalar_one()
                original = attempt['order']
                if (set(payload) != {*expected, 'px', 'sz'} or any(payload.get(k) != v for k, v in expected.items())
                        or original['trade_type'] != 'SELL' or original['trading_pair'] != order.trading_pair
                        or Decimal(original['amount']) != order.amount or Decimal(original['price']) != order.price
                        or Decimal(str(payload['sz'])) != order.amount or Decimal(str(payload['px'])) != order.price):
                    raise ValueError
            except (KeyError, ValueError, ArithmeticError, TypeError):
                raise MarketReadinessBlocked('uncertain_transport_identity_mismatch') from None
        authorize_cash_sell(connector, order, holds, now=float(row['database_now']), max_age=5)

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
        if self._isolate(self._pending()):
            row = self.owner.check_owner()
            state = row['recovery_state'] or {}
            self.owner.allow_new_orders = bool(not state.get('halt') and not state.get('pending_mutations')
                                               and not self.owner.shutdown_requested and not self.owner.stopping()
                                               and row['desired_state'] == 'running'
                                               and row['control_version'] == self.owner.version)
        self.next_poll = 0
        return True

    def _pending(self, *, all_sends=False):
        with self.owner.engine.connect() as connection:
            return [dict(row) for row in connection.execute(select(SubmissionAttempt).where(
                SubmissionAttempt.state.in_(('uncertain', 'possibly_sent') if all_sends else ('uncertain',)))
                .order_by(SubmissionAttempt.order_id).limit(MAX_ATTEMPTS + 1)).mappings()]

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
            return [dict(order_id=row.order_id, state=row.state, context=row.context, receipt=row.receipt) for row in rows]

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
        cost = (attempt['context'].get('reservation') or {}).get('cash_sell_base_cost')
        if cost is not None:
            order._cash_sell_reserved_base = Decimal(cost)
        return order

    async def _reject_saved_expiry(self, core, connector, attempt, order):
        receipt = attempt.get('receipt')
        if not is_expired_rejection_receipt(receipt, attempt['order_id']):
            return False
        self.owner.check_owner()
        context = attempt['context']
        if (context.get('environment') != environment_identity()
                or context['credential_fingerprint'] != credential_fingerprint(connector)
                or context.get('config_file_path') != self.owner.config_file_path
                or sorted(context['bindings'], key=digest) != sorted(self._bindings(), key=digest)):
            raise SubmissionBlocked('recovery_account_binding_changed')
        if order.exchange_order_id or order.executed_amount_base or order.order_fills:
            raise SubmissionBlocked('recovery_order_identity_mismatch')
        core.markets_recorder.reject_unsent_order(connector, order.client_order_id,
                                                  require_existing=True, expiry_receipt=receipt)
        await connector._order_tracker.process_order_update(OrderUpdate(
            order.trading_pair, time.time(), OrderState.FAILED, order.client_order_id))
        self.owner.sent_intents.discard(order.client_order_id)
        self.owner._release_unsent_admission(order.client_order_id)
        return True

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

    def _terminal_fence(self, recorder):
        from hummingbot.connector.markets_recorder import MarketsRecorder
        self.owner.check_owner()
        if (recorder is None or MarketsRecorder._shared_instance is not recorder
                or recorder.sql_manager.engine is not self.owner.engine):
            raise SubmissionBlocked('Terminal proof has no matching recorder')
        recorder.sql_manager.assert_persistence_healthy()

    async def _read_durable_fills(self, identity, trades, *, missing_ok=False):
        """One bounded read on a worker-owned connection; no writer/owner access.

        The caller holds terminal_slots until this read and its connection close
        finish, including repeated cancellation. Only immutable input snapshots
        cross threads; native state and physical ownership stay on the loop.
        """
        async with self.durable_read_slot:
            engine, snapshot = self.owner.engine, deepcopy(trades)

            def read():
                with engine.connect() as connection:
                    return durable_fills(connection, identity, snapshot, missing_ok=missing_ok)

            # A bare Future is not canceled by loop-wide Task cancellation.
            # Shield/drain it before returning either slot or the connection.
            job = asyncio.get_running_loop().run_in_executor(None, copy_context().run, read)
            try:
                return await asyncio.shield(job), snapshot
            except asyncio.CancelledError:
                while not job.done():
                    try:
                        await asyncio.shield(job)
                    except asyncio.CancelledError:
                        continue
                    except BaseException:
                        break
                if job.done() and not job.cancelled():
                    job.exception()  # Retrieve a late read failure without publishing proof.
                raise

    async def terminal_update(self, connector, order, update):
        """A cancel ACK is not terminal; complete private evidence stays fast."""
        from hummingbot.connector.markets_recorder import MarketsRecorder
        recorder = MarketsRecorder._shared_instance
        task = asyncio.current_task()
        self.owner.terminal_tasks.add(task)
        try:
            async with self.terminal_slots:
                self._terminal_fence(recorder)
                raw = deepcopy((update.misc_updates or {}).get('venue_order'))
                if isinstance(raw, dict):
                    try:
                        state, filled = decode_order_evidence(connector, order, raw)
                    except (KeyError, ValueError, ArithmeticError):
                        state, filled = None, None
                    if state == update.new_state and filled == order.executed_amount_base:
                        durable, snapshot = await self._read_durable_fills(
                            order.client_order_id, list(order.order_fills.values()), missing_ok=True)
                        if durable:
                            self._terminal_fence(recorder)
                            proof_covers_local(order, update, snapshot)
                            state, filled = decode_order_evidence(connector, order, raw)
                            if state != update.new_state or filled != order.executed_amount_base:
                                raise IOError('Native terminal evidence changed during durable read')
                            order._managed_terminal_proved = True
                            return update
                self.owner.check_owner()
                proof, trades = await asyncio.wait_for(read_order_proof(connector, order), timeout=10)
                self.owner.check_owner()
                proof_covers_local(order, proof, trades)
                persist_proved_trades(recorder, connector, order, trades)
                _, snapshot = await self._read_durable_fills(order.client_order_id, trades)
                self._terminal_fence(recorder)
                proof_covers_local(order, proof, snapshot)
                order._managed_terminal_proved = True
                return proof
        finally:
            self.owner.terminal_tasks.discard(task)

    def note_bootstrap_wait(self, error, delay, *, order_id=None):
        self.status.setdefault('started_at', time.time())
        reason = 'order_not_found_inconclusive' if isinstance(error, OKXOrderNotFoundError) else 'order_proof_pending'
        self.status.update(phase='bootstrapping_recovery', reason_code=reason,
                           operator_required=time.time() - self.status['started_at'] >= 300,
                           next_retry_at=time.time() + delay)
        if order_id is not None:
            self.status['order_id'] = order_id
        self.owner._mutate_recovery(lambda state: state.update(automatic_recovery=dict(self.status)))

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
        pending = self._bootstrap_pending(core)
        candidates = deepcopy([attempt for attempt in pending if attempt['state'] in ('possibly_sent', 'uncertain')])
        for attempt in candidates:
            attempt['state'] = 'uncertain'
        if self._isolate(candidates):
            # A successor owns the physical fence. A prior possibly-sent POST is
            # uncertain, never a new request to replay. Ordinary live sends in
            # this process are not added to the recovery worker's queue.
            identities = list(self.quarantined)
            self.owner._mutate_recovery(lambda state: self.owner.connection.execute(update(SubmissionAttempt).where(
                SubmissionAttempt.order_id.in_(identities), SubmissionAttempt.state == 'possibly_sent').values(state='uncertain')))
            pending = self._bootstrap_pending(core)
            self._isolate(self._pending())
            self.resume_scope = None  # Quarantine is not the prior session's trading authority.
        for attempt in pending:
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
            if await self._reject_saved_expiry(core, connector, attempt, order):
                continue
            if self.isolated and attempt['order_id'] in self.quarantined:
                continue  # Restore its reservation now; the existing GET worker resolves it in background.
            self.bootstrap_orders[order.client_order_id] = (attempt, order)
            failures = 0
            while True:
                self.owner.check_owner()
                try:
                    await self._merge(core, connector, attempt, order)
                    break
                except (IOError, asyncio.TimeoutError) as error:
                    # Includes explicit 51603. A long outage remains a visible
                    # initialization pause, not a repeated POST or timed reset.
                    failures = min(failures + 1, 5)
                    delay = min(15, 2 ** (failures - 1)) * random.uniform(.8, 1.2)
                    self.note_bootstrap_wait(error, delay, order_id=order.client_order_id)
                    await asyncio.sleep(delay)
        if self.isolated:
            self._isolate(self._pending())

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
            if self.isolated and identity in self.quarantined:
                continue  # The original journal work remains unapplied until exact terminal proof.
            attempt = self._attempt(identity)
            if not attempt or not eligible(attempt['context']):
                raise SubmissionBlocked('recovery_fill_context_missing')
            raw = attempt['context']['executor']
            model = OrderExecutorConfig if raw.get('type') == 'order_executor' else PositionExecutorConfig
            config = model.model_validate(raw)
            orchestrator = core.strategy.executor_orchestrator
            # Position durability is also required when optional drawdown
            # limits are disabled. In that case require the actual controller's
            # managed session/market, rather than treating an absent risk
            # policy as a financial conflict or accepting arbitrary journals.
            controller = orchestrator.strategy.controllers.get(config.controller_id)
            current = getattr(controller, 'config', None)
            covered = (orchestrator._mm_contains(config.controller_id, config.connector_name, config.trading_pair)
                       if orchestrator._mm_scope() else current is not None
                       and getattr(current, 'mm_session_id', None) == config.mm_session_id
                       and (getattr(current, 'connector_name', None), getattr(current, 'trading_pair', None))
                       == (config.connector_name, config.trading_pair))
            if (not covered
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

    def attach_quarantined_entries(self, core):
        """Restore native lifecycle ownership without constructing another POST."""
        from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig
        from hummingbot.strategy_v2.executors.position_executor.position_executor import PositionExecutor
        orchestrator = core.strategy.executor_orchestrator
        existing = {executor.config.id: executor for rows in orchestrator.active_executors.values() for executor in rows}
        prepared = []
        for identity, attempt in self.quarantined.items():
            config = PositionExecutorConfig.model_validate(attempt['context']['executor'])
            controller = core.strategy.controllers.get(config.controller_id)
            current = getattr(controller, 'config', None)
            if ((getattr(current, 'connector_name', None), getattr(current, 'trading_pair', None))
                    != (config.connector_name, config.trading_pair)):
                raise SubmissionBlocked('recovery_position_application_conflict')
            order = self.owner.connectors[config.connector_name]._order_tracker.fetch_order(identity)
            if order is None:
                raise SubmissionBlocked('recovery_order_identity_mismatch')
            if config.id in existing:
                executor = existing[config.id]
                if not executor._open_order or executor._open_order.order is not order:
                    raise SubmissionBlocked('recovery_position_application_conflict')
                continue
            if any(item.config.id == config.id for item in prepared):
                raise SubmissionBlocked('recovery_position_application_conflict')
            # Native amount/price were quantized before the immutable intent;
            # the proposal may have finer precision than the venue permits.
            config = config.model_copy(update={'amount': order.amount, 'entry_price': order.price})
            executor = PositionExecutor(core.strategy, config)
            executor.bind_pending_entry(order)
            prepared.append(executor)
        for executor in prepared:
            orchestrator.active_executors.setdefault(executor.config.controller_id, []).append(executor)
            executor.start()

    def defer_shutdown(self, core):
        """Detach only journal-owned uncertain entries, never fabricate an order terminal."""
        if not self.isolated or not self._isolate(self._pending()):
            return set()
        records = self.quarantined
        configs = {row['context']['executor']['id'] for row in records.values()}
        if core.markets_recorder.applied_position_executors(configs):
            return set()
        orchestrator = getattr(getattr(core, 'strategy', None), 'executor_orchestrator', None)
        remove = []
        if orchestrator is not None:
            for controller_id, executors in orchestrator.active_executors.items():
                for executor in executors:
                    if executor.config.id not in configs:
                        continue
                    if executor.config.type != 'position_executor':
                        return set()
                    tracked = [*executor._failed_orders, executor._open_order, executor._close_order, executor._take_profit_limit_order]
                    identities = {item.order_id for item in tracked if item}
                    expected = {identity for identity, row in records.items()
                                if row['context']['executor']['id'] == executor.config.id}
                    if (not expected <= identities or any(item and item.order and item.executed_amount_base
                                                          and item.order_id not in expected for item in tracked)):
                        return set()  # Mixed financial effects still require full native proof.
                    remove.append((controller_id, executor))
            for controller_id, executor in remove:
                orchestrator.active_executors[controller_id].remove(executor)
                executor.stop()  # Stop its loop/callbacks; the native order and durable work stay unresolved.
        self.status.update(phase='settled_stopped', reason_code='uncertain_effects_retained')
        return set(records)

    async def step(self, core, *, force=False):
        if not self.active or not force and time.monotonic() < self.next_poll:
            return
        self.owner.check_owner()
        core.trade_fill_db.assert_persistence_healthy()
        pending = self._pending()
        if not self.isolated and self._isolate(pending):
            row = self.owner.check_owner()
            recovery = row['recovery_state'] or {}
            self.owner.allow_new_orders = bool(not recovery.get('halt') and not recovery.get('pending_mutations')
                                               and not self.owner.shutdown_requested and row['desired_state'] == 'running'
                                               and row['control_version'] == self.owner.version)
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
                if self.isolated and not force and time.monotonic() < self.attempt_retry.get(attempt['order_id'], (0, 0))[0]:
                    continue
                try:
                    if not await self._reject_saved_expiry(core, connector, attempt, order):
                        await asyncio.wait_for(self._merge(core, connector, attempt, order), timeout=10)
                except (IOError, asyncio.TimeoutError) as error:
                    if not self.isolated:
                        raise
                    previous = self.attempt_retry.get(attempt['order_id'], (0, 0))[1]
                    failures = min(previous + 1, 5)
                    delay = min(15, 2 ** (failures - 1)) * random.uniform(.8, 1.2)
                    self.attempt_retry[attempt['order_id']] = (time.monotonic() + delay, failures)
                    self.status.update(reason_code='order_not_found_inconclusive' if isinstance(error, OKXOrderNotFoundError)
                                       else 'order_proof_pending', next_retry_at=time.time() + delay)
                    continue  # Another positively provable intent still gets a turn.
                self.attempt_retry.pop(attempt['order_id'], None)
                self.status['last_progress_at'] = time.time()
            remaining = self._pending()
            if self.isolated and remaining:
                if not self._isolate(remaining):
                    self.owner.allow_new_orders = False
                    raise MarketReadinessBlocked('uncertain_reservation_unavailable')
                row = self.owner.check_owner()
                recovery = row['recovery_state'] or {}
                self.owner.allow_new_orders = bool(not recovery.get('halt') and not recovery.get('pending_mutations')
                                                   and not self.owner.shutdown_requested and row['desired_state'] == 'running'
                                                   and row['control_version'] == self.owner.version)
                self.status.update(phase='reconciling' if self.owner.allow_new_orders else 'settled_stopped',
                                   operator_required=time.time() - self.status.get('started_at', time.time()) >= 300)
                now = time.monotonic()
                self.next_poll = min(self.attempt_retry.get(attempt['order_id'], (now + 1, 0))[0] for attempt in remaining)
                self.status['next_retry_at'] = time.time() + max(0, self.next_poll - now)
                self.owner._mutate_recovery(lambda state: state.update(automatic_recovery=dict(self.status)))
                return
            if not remaining:
                self.quarantined.clear()
                self.attempt_retry.clear()
                self.status.update(isolated=False, quarantined_orders=0, admission_mode='paused',
                                   reserved_base={}, reserved_quote_worst_case='0')
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
