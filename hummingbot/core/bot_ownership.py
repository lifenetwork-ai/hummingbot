"""Dedicated PostgreSQL session ownership and conservative exchange recovery."""
import asyncio
import json
import logging
import time
import uuid
import weakref
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse

from sqlalchemy import select, text, update

from hummingbot.core.engine_diagnostics import active_shutdown_stages, trace_runtime
from hummingbot.core.order_submission_guard import (
    SUPPORTED_CONNECTORS,
    MarketReadinessBlocked,
    SessionSubmissionBlocked,
    SubmissionBlocked,
)
from hummingbot.core.startup_order_reconciliation import UntrackedOpenOrders, reconcile_open_order_visibility
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.db_migration.postgres.locks import OWNER_KEY, OWNER_NAMESPACE
from hummingbot.model.order import Order

ORDER_PATH = "/api/v5/trade/order"
CANCEL_PATHS = {"/api/v5/trade/cancel-order", "/api/v5/trade/cancel-batch-orders"}
ACCOUNT_PATHS = {"/api/v5/account/set-leverage", "/api/v5/account/set-position-mode"}


@contextmanager
def _ownership_read(connection):
    """One fresh SELECT on the lock-owning session, without a read-COMMIT wait.

    Restore transaction mode before any recovery mutation uses this connection.
    SQLAlchemy still ends its logical transaction; DBAPI commit is a no-op only
    inside this read scope. Never reconnect an invalidated ownership session.
    """
    from psycopg2.extensions import TRANSACTION_STATUS_IDLE

    raw = connection.connection.driver_connection
    autocommit = raw.autocommit
    # Preserve the original SELECT/commit behavior for an already-open read
    # transaction, including one opened by a direct DBAPI cursor.
    change_mode = not connection.in_transaction() and raw.get_transaction_status() == TRANSACTION_STATUS_IDLE
    try:
        # Repeated SQLAlchemy isolation execution_options register unbounded
        # pool-reset callbacks on this process-lifetime connection. Change only
        # the documented Psycopg flag; SQLAlchemy's transaction is ended below.
        if change_mode:
            raw.autocommit = True
        yield
    finally:
        if not connection.closed:
            try:
                if connection.in_transaction():
                    connection.rollback()
                if connection.invalidated:
                    connection.close()
                elif change_mode:
                    raw.autocommit = autocommit
            except Exception:
                # close() alone returns a live DBAPI session to the pool. Discard
                # an uncertain mode before it can reach any later writer.
                connection.invalidate()
                connection.close()
                raise


class OwnershipLost(SubmissionBlocked):
    pass


class BotOwnership:
    def __init__(self, engine, bot_id, *, stopping=None, scoped_recovery=False):
        self.engine = engine
        self.store = BotStateStore(engine, bot_id)
        self.owner_uuid = str(uuid.uuid4())
        self.generation = None
        self.connection = None
        self.backend_pid = None
        self.lost = False
        self.allow_new_orders = False
        self.scoped_recovery_enabled = scoped_recovery
        self.shutdown_requested = False
        self.stopping = stopping or (lambda: False)
        self.reconciling = False
        self.version = None
        self.config_file_path = None
        self.connectors = {}
        self.active_intents = set()
        self.sent_intents = set()
        # Proof follows the actual rejected order object, including references
        # captured by shutdown, without retaining retired IDs indefinitely.
        self.unsent_rejections = weakref.WeakValueDictionary()
        self.expired_rejections = weakref.WeakValueDictionary()
        self.submission_tasks = set()
        self.terminal_tasks = set()
        self.transport_tasks = {}
        self.account_lock = asyncio.Lock()
        self.account_token = None
        self.mm_ready_check = None
        self.mm_order_ready_check = None
        self.mm_order_readiness_reason = None
        self.mm_quote_check = None
        self.runtime_ready_check = None
        self.intent_scopes = {}
        self.cleanup_operation_id = None
        self.cleanup_versions = {}
        self.cleanup_intents = {}
        self.spot_outflow = None
        self.listing_bootstrap = None
        self.volatility_checkpoints = None
        from hummingbot.core.automatic_recovery import AutomaticOrderRecovery
        self.automatic_recovery = AutomaticOrderRecovery(self)

    def begin_shutdown(self):
        """Process-lifetime fence; cleanup/cancellation still retains ownership."""
        self.shutdown_requested = True
        self.allow_new_orders = False
        self.reconciling = False

    def _check_shutdown(self):
        if self.shutdown_requested or self.stopping():
            raise SessionSubmissionBlocked("Service shutdown requested; new exchange mutations are closed.")

    def try_acquire(self) -> bool:
        if self.lost:
            raise OwnershipLost("Lost ownership cannot be reconnected in place; stop tasks and restart the service.")
        if self.connection is not None:
            self.check_owner()
            return True
        connection = self.engine.connect()
        locked = False
        try:
            locked = connection.execute(text("SELECT pg_try_advisory_lock(:namespace, :key)"),
                                        {"namespace": OWNER_NAMESPACE, "key": OWNER_KEY}).scalar()
            if not locked:
                return False
            row = connection.execute(select(BotState).where(BotState.bot_id == self.store.bot_id).with_for_update()).mappings().one()
            recovery = dict(row["recovery_state"] or {})
            pending = connection.execute(select(Order.id).where(Order.creation_pending.is_(True)).limit(1)).first()
            shutdown_fence = (recovery.get('halt') and recovery.get('halt_kind') == 'shutdown_pending'
                              and not recovery.get('reason'))
            # A second cleanup can drain runtime_active after an earlier timeout
            # while retaining its fence. Recheck the same verified checkpoint on
            # successor acquisition even when no live/pending work remains.
            if pending or recovery.get("pending_mutations") or recovery.get("runtime_active") or shutdown_fence:
                from sqlalchemy.orm import Session

                from hummingbot.model.runtime_journal import can_bootstrap
                with Session(bind=connection) as session:
                    automatic = ((not recovery.get('halt') or shutdown_fence)
                                 and not recovery.get('pending_mutations')
                                 and can_bootstrap(session, row))
                if automatic:
                    if recovery.get('halt_kind') == 'shutdown_pending':
                        recovery.pop('halt', None)
                        recovery.pop('halt_kind', None)
                    recovery['automatic_recovery'] = dict(phase='bootstrapping_recovery', reason_code='owner_handover',
                                                          operator_required=False, started_at=time.time(), cold_recovery_supported=True)
                elif not recovery.get('halt'):
                    recovery.update(halt=True, reason="A previous process has unresolved exchange effects; reconcile and verify it is stopped.")
            self.generation = row["owner_generation"] + 1
            recovery.update(runtime_journal_version=1, journal_owner_generation=self.generation)
            connection.execute(update(BotState).where(BotState.bot_id == self.store.bot_id).values(
                owner_uuid=self.owner_uuid, owner_generation=self.generation,
                heartbeat=text("clock_timestamp()"), recovery_state=recovery))
            self.backend_pid = connection.execute(text("SELECT pg_backend_pid()")).scalar()
            connection.commit()
            self.connection = connection
            self.automatic_recovery.status = dict(recovery.get('automatic_recovery') or self.automatic_recovery.status)
            return True
        finally:
            if self.connection is None:
                try:
                    connection.rollback()
                    if locked:
                        connection.execute(text("SELECT pg_advisory_unlock(:namespace, :key)"),
                                           {"namespace": OWNER_NAMESPACE, "key": OWNER_KEY})
                        connection.commit()
                finally:
                    connection.close()

    def prepare(self, version, config_file_path):
        self.check_owner()
        self.unsent_rejections.clear()
        self.allow_new_orders = False
        self.reconciling = True
        self.version = version
        self.config_file_path = config_file_path
        self._mutate_recovery(lambda state: state.update(runtime_active=True))

    @trace_runtime('owner.read')
    def _read_owner(self, *, identity_only=False):
        """Fresh proof on the physical owner session; writes need no state JSON."""
        connection = self.connection
        if self.lost or connection is None or connection.closed or connection.invalidated:
            self.lost = True
            self.allow_new_orders = False
            raise OwnershipLost("The bot ownership session is unavailable.")
        try:
            # Only this internal write-fence path omits the state payload. The
            # observer and submission gates always retain their complete fresh
            # row. Neither projection reuses authority or changes the lock query.
            columns = "pg_backend_pid() AS backend_pid" if identity_only else """
                b.bot_id, b.config_file_path, b.config_type, b.desired_state, b.control_version,
                b.acknowledged_version, b.owner_uuid, b.owner_generation, b.heartbeat, b.revision,
                b.recovery_state, b.mm_session, pg_backend_pid() AS backend_pid,
                EXTRACT(EPOCH FROM clock_timestamp()) AS database_now""".strip()
            with _ownership_read(connection):
                row = connection.execute(text(f"""
                    SELECT {columns} FROM public.bot_state b
                    WHERE bot_id=:bot_id AND owner_uuid=:owner AND owner_generation=:generation
                    AND EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND granted
                        AND pid=pg_backend_pid() AND classid=:namespace AND objid=:key AND objsubid=2)
                """), {"bot_id": self.store.bot_id, "owner": self.owner_uuid, "generation": self.generation,
                       "namespace": OWNER_NAMESPACE, "key": OWNER_KEY}).mappings().first()
                connection.commit()
            if not row or row["backend_pid"] != self.backend_pid:
                raise OwnershipLost("The bot ownership lock/generation no longer matches.")
        except Exception:
            self.lost = True
            self.allow_new_orders = False
            raise OwnershipLost("The bot ownership session failed; submissions are closed.") from None
        return dict(row)

    def check_owner(self, *, new_risk=False, allow_setup=False):
        if new_risk:
            self._check_shutdown()
        row = self._read_owner()
        # A signal can arrive while the synchronous database check is in flight.
        if new_risk:
            self._check_shutdown()
        if new_risk and ((not self.allow_new_orders and not allow_setup) or row["desired_state"] != "running"
                         or row["control_version"] != self.version or row["config_file_path"] != self.config_file_path
                         or (row["recovery_state"] or {}).get("halt")
                         or (row["recovery_state"] or {}).get("pending_mutations")):
            recovery = row["recovery_state"] or {}
            if (not recovery.get("halt") and not recovery.get("pending_mutations")
                    and row["config_file_path"] == self.config_file_path):
                if row["desired_state"] != "running":
                    raise SessionSubmissionBlocked("Trading is stopped; new order submissions are closed.")
                if row["control_version"] != self.version:
                    raise SessionSubmissionBlocked("Trading control version changed; this executor can no longer submit.")
            raise SubmissionBlocked("Trading is stopped, unreconciled, superseded or requires recovery.")
        if new_risk:
            from hummingbot.connector.markets_recorder import MarketsRecorder
            recorder = MarketsRecorder._shared_instance
            if recorder is not None and recorder.sql_manager.engine is self.engine:
                recorder.sql_manager.assert_persistence_healthy()
        return dict(row)

    def _mutate_recovery(self, change, *, expected_version=None):
        self.check_owner()
        with self.connection.begin():
            row = self.connection.execute(select(BotState.recovery_state, BotState.control_version).where(
                BotState.bot_id == self.store.bot_id, BotState.owner_uuid == self.owner_uuid,
                BotState.owner_generation == self.generation).with_for_update()).one()
            if expected_version is not None and row[1] != expected_version:
                return False
            recovery = dict(row[0] or {})
            change(recovery)
            self.connection.execute(update(BotState).where(BotState.bot_id == self.store.bot_id,
                                                           BotState.owner_uuid == self.owner_uuid,
                                                           BotState.owner_generation == self.generation)
                                    .values(recovery_state=recovery))
        return True

    def require_recovery(self, reason):
        self.allow_new_orders = False
        try:
            self._mutate_recovery(lambda state: state.update(halt=True, halt_kind='manual', reason=reason))
        except Exception:
            self.lost = True

    def _check_intents(self, recorder, *, admitting=False):
        from hummingbot.core.automatic_recovery import MAX_ATTEMPTS
        recorder.sql_manager.assert_persistence_healthy()
        try:
            with recorder.sql_manager.get_new_session() as session:
                pending = session.query(Order.id, Order.market).filter(Order.creation_pending.is_(True)).limit(MAX_ATTEMPTS + 1).all()
        except Exception:
            recorder.sql_manager.persistence_healthy = False
            self.allow_new_orders = False
            raise SubmissionBlocked("Recorder state is unavailable; reconciliation is required.") from None
        if len(pending) > MAX_ATTEMPTS:
            self.require_recovery('Pending order recovery exceeds its bounded scope.')
            raise SubmissionBlocked('Pending order recovery exceeds its bounded scope.')
        if admitting and len(pending) == MAX_ATTEMPTS:
            raise MarketReadinessBlocked('Pending submission admission is at capacity; wait for durable acknowledgments.')
        ids = {order_id for order_id, _ in pending}
        self.active_intents.intersection_update(ids)
        self.sent_intents.intersection_update(ids)
        self.intent_scopes = {key: value for key, value in self.intent_scopes.items() if key in ids}
        self.cleanup_intents = {key: value for key, value in self.cleanup_intents.items() if key in ids}
        if self.listing_bootstrap is not None:
            self.listing_bootstrap.deadlines = {key: value for key, value in self.listing_bootstrap.deadlines.items() if key in ids}
        for order_id, market in pending:
            connector = next((value for value in self.connectors.values() if value.display_name == market), None)
            tracked = connector.in_flight_orders.get(order_id) if connector is not None else None
            if (self.automatic_recovery.isolated and order_id in self.automatic_recovery.quarantined
                    and tracked is not None and getattr(tracked, '_managed_recovery_order', False)):
                continue  # Durable quarantine still owns this unresolved identity.
            if order_id not in self.active_intents or tracked is None or tracked.is_done:
                self.require_recovery("An order submission has an unresolved outcome; no automatic resubmission is allowed.")
                raise SubmissionBlocked("Unresolved order submission requires reconciliation.")

    def before_order(self, connector, order):
        from hummingbot.core.cleanup_authority import current_cleanup_scope, validate_close
        from hummingbot.core.order_submission_guard import current_session_scope
        cleanup = current_cleanup_scope()
        row = self.check_owner(new_risk=cleanup is None)
        if cleanup is not None:
            validate_close(self, row, connector, order, cleanup)
        if cleanup is None and self.runtime_ready_check is not None and not self.runtime_ready_check():
            raise SubmissionBlocked("A required runtime task is not healthy.")
        scope = current_session_scope()
        if cleanup is None:
            self._check_mm_authority(row, scope, connector=connector, order=order)
        if connector.name not in SUPPORTED_CONNECTORS:
            raise SubmissionBlocked("This connector has not been verified for the PostgreSQL service.")
        from hummingbot.connector.markets_recorder import MarketsRecorder
        recorder = MarketsRecorder._shared_instance
        if (recorder is None or recorder.sql_manager.engine is not self.engine
                or recorder.config_file_path != self.config_file_path or connector not in recorder._markets):
            raise SubmissionBlocked("The active recorder/configuration does not match the service owner.")
        self._check_intents(recorder, admitting=True)
        if cleanup is None and self.scoped_recovery_enabled and self.automatic_recovery.active:
            self.automatic_recovery.authorize_isolated_order(connector, order)
        try:
            if cleanup is None and self.listing_bootstrap is not None:
                self.listing_bootstrap.before_order(connector, order, row)
            if cleanup is None and self.spot_outflow is not None:
                self.spot_outflow.before_order(connector, order, row)
            if cleanup is None and self.mm_quote_check is not None:
                self.mm_quote_check(connector, order)
        except BaseException:
            # Admission has not attempted a durable write or transport. Only
            # unwind a new local identity, never an existing/acknowledged order.
            if (order.client_order_id not in self.active_intents and order.client_order_id not in self.sent_intents
                    and not order.exchange_order_id):
                self._release_unsent_admission(order.client_order_id)
            raise
        # Track the write attempt too: a lost commit acknowledgment still needs
        # a fenced durable rejection before releasing its local reservations.
        self.active_intents.add(order.client_order_id)
        self.intent_scopes[order.client_order_id] = scope
        if cleanup is not None:
            self.cleanup_intents[order.client_order_id] = cleanup
        recorder.record_order_intent(connector, order)
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is not None:
            self.submission_tasks.add(task)
            task.add_done_callback(self.submission_tasks.discard)

    def order_failed(self, connector, order, exception=None):
        from hummingbot.connector.okx_order_status import OKXExpiredOrderRejection
        if (isinstance(exception, OKXExpiredOrderRejection)
                and exception.client_order_id == order.client_order_id
                and connector is not None and connector.name in SUPPORTED_CONNECTORS
                and order.client_order_id in self.sent_intents and not order.exchange_order_id):
            try:
                self.check_owner()
                from hummingbot.connector.markets_recorder import MarketsRecorder
                recorder = MarketsRecorder._shared_instance
                recorder.reject_unsent_order(connector, order.client_order_id, require_existing=True)
                self.expired_rejections[order.client_order_id] = order
                self.sent_intents.discard(order.client_order_id)
                self._release_unsent_admission(order.client_order_id)
            except Exception:
                self.require_recovery("An exchange-rejected order could not be resolved durably.")
            return order.client_order_id in self.sent_intents
        if order.exchange_order_id:
            try:
                if self.automatic_recovery.pause(order.client_order_id):
                    return True
            except Exception:
                self.require_recovery('Automatic order reconciliation could not be recorded durably.')
                return True
        if order.client_order_id not in self.sent_intents and not order.exchange_order_id:
            # This hook runs only in this process's create coroutine. Retain
            # positive no-transport evidence even if a later cancel-not-found
            # acknowledgement changes FAILED to CANCELED in the tracker.
            self.unsent_rejections[order.client_order_id] = order
        if order.client_order_id in self.sent_intents:
            try:
                if self.automatic_recovery.pause(order.client_order_id):
                    return True
            except Exception:
                self.require_recovery('Automatic order reconciliation could not be recorded durably.')
                return True
            self.require_recovery("An order submission did not finish with a durable acknowledgment.")
            # No terminal order outcome has been proved. Leave the native
            # tracker able to consume a late create/fill instead of emitting a
            # false failure event (and prompting an executor retry).
            return True
        elif order.client_order_id in self.active_intents:
            # No transport attempt passed the final gate in this process. It is
            # safe to reject that local intent, if this process still owns the DB.
            try:
                self.check_owner()
                from hummingbot.connector.markets_recorder import MarketsRecorder
                recorder = MarketsRecorder._shared_instance
                recorder.reject_unsent_order(connector, order.client_order_id)
                self._release_unsent_admission(order.client_order_id)
            except Exception:
                self.require_recovery("An unsent order intent could not be resolved durably.")
        return False

    def _release_unsent_admission(self, client_id):
        if self.spot_outflow is not None:
            self.spot_outflow.release_unsent(client_id)
        self.active_intents.discard(client_id)
        self.intent_scopes.pop(client_id, None)
        self.cleanup_intents.pop(client_id, None)
        if self.listing_bootstrap is not None:
            self.listing_bootstrap.deadlines.pop(client_id, None)

    def transport_started(self):
        task = asyncio.current_task()
        self.transport_tasks[task] = self.transport_tasks.get(task, 0) + 1

    def mutation_transport_failed(self, request):
        try:
            if urlparse(request.url).path == ORDER_PATH:
                identity = json.loads(request.data)['clOrdId']
                if self.automatic_recovery.pause(identity):
                    return
            elif urlparse(request.url).path in CANCEL_PATHS and self.cleanup_operation_id is None:
                data = json.loads(request.data)
                rows = data if isinstance(data, list) else [data]
                if 0 < len(rows) <= 20 and all(isinstance(row, dict) and row.get('clOrdId') for row in rows):
                    if all(self.automatic_recovery.pause(row['clOrdId'], cancel=True) for row in rows):
                        return
        except Exception:
            pass
        self.require_recovery('An exchange mutation transport ended without a confirmed outcome.')

    def cancel_failed(self, connector, order):
        if self.cleanup_operation_id is not None:
            return False
        try:
            return self.automatic_recovery.pause(order.client_order_id, cancel=True)
        except Exception:
            self.require_recovery('Automatic order reconciliation could not be recorded durably.')
            return True

    async def record_transport_receipt(self, request, response):
        if urlparse(request.url).path != ORDER_PATH:
            return
        from hummingbot.model.runtime_journal import SubmissionAttempt
        identity = json.loads(request.data)['clOrdId']
        try:
            body = await response.json()
        except (ValueError, TypeError):
            body = {}
        receipt = {'http_status': response.status}
        if isinstance(body, dict):
            receipt['code'] = str(body.get('code', ''))[:32]
            rows = body.get('data')
            if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict):
                receipt['order'] = {key: str(rows[0].get(key, ''))[:128] for key in ('clOrdId', 'ordId', 'sCode')}
        self.check_owner()
        with self.connection.begin():
            self.connection.execute(update(SubmissionAttempt).where(SubmissionAttempt.order_id == identity).values(receipt=receipt))

    def _record_send_attempt(self, client_id, data, deadline, headers):
        """A committed, immutable transport identity precedes any network call."""
        from hummingbot.model.runtime_journal import SubmissionAttempt, digest
        self.check_owner()
        # A lost COMMIT reply is ambiguous even though this coroutine will not
        # deliberately invoke transport after the exception.
        with self.connection.begin():
            row = self.connection.execute(select(SubmissionAttempt).where(
                SubmissionAttempt.order_id == client_id).with_for_update()).mappings().one()
            if (row['state'] != 'prepared' or row['context']['owner_uuid'] != self.owner_uuid
                    or row['context']['generation'] != self.generation):
                raise SubmissionBlocked('Submission attempt was already sent or belongs to another owner.')
            self._validate_close_transport(row['context'], client_id, data)
            from hummingbot.core.automatic_recovery import eligible
            if eligible(row['context']):
                key = headers.get('OK-ACCESS-KEY')
                expected = digest(['credential_identity', row['context']['environment'],
                                   row['context']['executor']['connector_name'], key])
                if not key or expected != row['context']['credential_fingerprint']:
                    raise SubmissionBlocked('Authenticated transport identity does not match its recorded account scope.')
            self.sent_intents.add(client_id)
            self.connection.execute(update(SubmissionAttempt).where(SubmissionAttempt.order_id == client_id).values(
                state='possibly_sent', payload_hash=digest(data), deadline_ms=deadline))

    def _validate_close_transport(self, context, identity, data):
        """Bind a managed close to its immutable intent at the existing SQL gate."""
        config = context.get('executor') or {}
        if (config.get('type') != 'order_executor' or config.get('position_action') != 'CLOSE'
                or config.get('mm_session_id') is None):
            return
        from hummingbot.core.data_type.common import OrderType, PositionAction
        try:
            original = context['order']
            connector = self.connectors[config['connector_name']]
            order = connector.in_flight_orders[identity]
            side = 'BUY' if config['side'] in (1, 'BUY') else 'SELL' if config['side'] in (2, 'SELL') else None
            quantity, price = Decimal(original['amount']), Decimal(original['price'])
            allowed = {'clOrdId', 'tdMode', 'ordType', 'instId', 'side', 'sz', 'px', 'posSide'}
            if (connector.name != 'okx_perpetual' or set(data) != allowed or original['client_order_id'] != identity
                    or original['trading_pair'] != config['trading_pair'] or original['trading_pair'] != order.trading_pair
                    or original['trade_type'] != side or order.trade_type.name != side
                    or original['position'] != 'CLOSE' or order.position != PositionAction.CLOSE
                    or original['order_type'] != 'LIMIT_MAKER' or order.order_type != OrderType.LIMIT_MAKER
                    or not quantity.is_finite() or quantity <= 0 or order.amount != quantity
                    or not price.is_finite() or price <= 0 or order.price != price
                    or data['clOrdId'] != identity or data['tdMode'] != 'cross' or data['ordType'] != 'post_only'
                    or data['instId'] != order.trading_pair + '-SWAP' or data['side'] != side.lower()
                    or data['posSide'] != ('short' if side == 'BUY' else 'long')
                    or Decimal(data['px']) != price
                    or connector._format_size_to_amount(order.trading_pair, Decimal(data['sz'])) != quantity):
                raise ValueError
        except (KeyError, ValueError, TypeError, ArithmeticError, AttributeError):
            raise SubmissionBlocked('reduction_transport_identity_mismatch: closing payload differs from its durable intent') from None

    def transport_finished(self):
        task = asyncio.current_task()
        count = self.transport_tasks.get(task, 0)
        if count > 1:
            self.transport_tasks[task] = count - 1
        else:
            self.transport_tasks.pop(task, None)

    def fence_session(self, session):
        self._read_owner(identity_only=True)
        transaction = session.get_transaction()
        if transaction is not None and session.info.get("ownership_fenced_transaction") is transaction:
            return
        row = session.connection().execute(select(BotState.owner_generation).where(
            BotState.bot_id == self.store.bot_id, BotState.owner_uuid == self.owner_uuid,
            BotState.owner_generation == self.generation).with_for_update(read=True)).first()
        if not row:
            raise OwnershipLost("A stale process cannot write trading/recovery state.")
        session.info["ownership_fenced_transaction"] = session.get_transaction()

    async def authorize_rest(self, request):
        path = urlparse(request.url).path
        try:
            data = json.loads(getattr(request, "data", None) or "{}") if path == ORDER_PATH else {}
            if not isinstance(data, dict):
                raise ValueError
        except (TypeError, ValueError):
            raise SubmissionBlocked("Order submission requires a valid object payload.") from None
        cleanup = self.cleanup_intents.get(data.get("clOrdId")) if path == ORDER_PATH else None
        row = self.check_owner(new_risk=path == ORDER_PATH and cleanup is None)
        if path in CANCEL_PATHS:
            return None
        if path == ORDER_PATH:
            if cleanup is not None:
                from hummingbot.connector.markets_recorder import MarketsRecorder
                from hummingbot.core.cleanup_authority import validate_close, validate_payload
                client_id = data.get("clOrdId")
                connector, order = next(((connector, connector.in_flight_orders[client_id]) for connector in self.connectors.values()
                                         if client_id in connector.in_flight_orders), (None, None))
                if order is None:
                    raise SubmissionBlocked("Disconnect close tracking is unavailable")
                validate_close(self, row, connector, order, cleanup, final=True)
                validate_payload(connector, order, cleanup, data)
                recorder = MarketsRecorder._shared_instance
                if recorder is None or recorder.sql_manager.engine is not self.engine:
                    raise SubmissionBlocked("Disconnect close recorder is unavailable")
                self._check_intents(recorder)
                if client_id not in self.active_intents or client_id in self.sent_intents:
                    raise SubmissionBlocked("Disconnect close was already sent or needs reconciliation")
                headers = dict(request.headers or {})
                headers["expTime"] = str(min(cleanup.deadline_ms, int(headers.get("expTime", cleanup.deadline_ms))))
                request.headers = headers
                self._record_send_attempt(client_id, data, int(headers['expTime']), headers)
                return None
            if self.runtime_ready_check is not None and not self.runtime_ready_check():
                raise SubmissionBlocked("A required runtime task is not healthy.")
            client_id = data.get("clOrdId")
            connector, order = next(((connector, connector.in_flight_orders[client_id])
                                     for connector in self.connectors.values() if client_id in connector.in_flight_orders), (None, None))
            self._check_mm_authority(row, self.intent_scopes.get(client_id), connector=connector, order=order)
            from hummingbot.connector.markets_recorder import MarketsRecorder
            recorder = MarketsRecorder._shared_instance
            if recorder is None:
                raise SubmissionBlocked("Order submission has no active recorder.")
            self._check_intents(recorder)
            if client_id not in self.active_intents or client_id in self.sent_intents:
                raise SubmissionBlocked("Order identity is absent or was already sent; reconcile instead of retrying.")
            if self.scoped_recovery_enabled and self.automatic_recovery.active:
                connector, order = next(((connector, connector.in_flight_orders[client_id])
                                         for connector in self.connectors.values() if client_id in connector.in_flight_orders), (None, None))
                if order is None:
                    raise MarketReadinessBlocked('uncertain_order_tracking_unavailable')
                # This gate is after throttling/authentication, before transport.
                self.automatic_recovery.authorize_isolated_order(connector, order, payload=data)
            bootstrap_deadline = self.listing_bootstrap.authorize(data, row) if self.listing_bootstrap is not None else None
            if self.spot_outflow is not None:
                self.spot_outflow.authorize(data, row)
            if self.mm_quote_check is not None:
                from hummingbot.core.data_type.common import OrderType
                connector, order = next(((connector, connector.in_flight_orders[client_id])
                                         for connector in self.connectors.values() if client_id in connector.in_flight_orders), (None, None))
                if order is None:
                    raise SubmissionBlocked("Spread policy tracked order unavailable")
                size = Decimal(str(data.get("sz")))
                if connector.name == "okx_perpetual":
                    size = connector._format_size_to_amount(order.trading_pair, size)
                instrument = order.trading_pair + ("-SWAP" if connector.name == "okx_perpetual" else "")
                if (data.get("instId") != instrument or data.get("side") != order.trade_type.name.lower()
                        or Decimal(str(data.get("px"))) != order.price or size != order.amount
                        or data.get("ordType") != "post_only" or order.order_type != OrderType.LIMIT_MAKER):
                    raise SubmissionBlocked("Spread policy order payload changed")
                self.mm_quote_check(connector, order)
            # OKX's documented expTime deadline also constrains a process paused
            # after this gate but before transport. It does not replace recovery.
            headers = dict(request.headers or {})
            stamp = headers.get("OK-ACCESS-TIMESTAMP")
            try:
                now = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() if stamp else time.time()
                expiry = int(now * 1000) + 5000
                if bootstrap_deadline is not None:
                    expiry = min(expiry, int(bootstrap_deadline * 1000))
                if "expTime" in headers:
                    expiry = min(expiry, int(headers["expTime"]))
                if row["config_type"] == "mm-session":
                    mm = row["mm_session"]
                    for deadline in (mm['expires_at'], mm['step_expires_at']):
                        if deadline is not None:
                            expiry = min(expiry, int(deadline * 1000))
            except (ValueError, TypeError):
                raise SubmissionBlocked("Order request expiration header is invalid.") from None
            headers["expTime"] = str(expiry)
            request.headers = headers
            self._record_send_attempt(client_id, data, expiry, headers)
            return None
        if path not in ACCOUNT_PATHS:
            raise SubmissionBlocked("Unreviewed authenticated exchange mutation is blocked.")
        await self.account_lock.acquire()
        try:
            self.check_owner(new_risk=True, allow_setup=self.reconciling)
            token = str(uuid.uuid4())

            def record(state):
                if state.get("pending_mutations") or state.get("halt"):
                    raise SubmissionBlocked("An account mutation is unresolved.")
                state["pending_mutations"] = [{"token": token, "path": path}]
            self._mutate_recovery(record)
            self.account_token = token
            return token
        except BaseException:
            self.account_lock.release()
            raise

    def _check_mm_authority(self, row, scope, *, connector=None, order=None, submission=True):
        """Recovery may check readiness; each actual send still requires its native order."""
        if row["config_type"] != "mm-session":
            if scope is not None:
                raise SessionSubmissionBlocked("A previous MM executor cannot submit in another strategy.")
            return
        from hummingbot.core.mm_session import session_permits
        if scope is None:
            raise SubmissionBlocked("MM order submission has no session authority.")
        if not session_permits(row["mm_session"] or {}, session_id=scope[0], generation=scope[1], now=float(row["database_now"])):
            raise SessionSubmissionBlocked("MM session/step authority expired or changed.")
        if self.mm_ready_check is None:
            raise SubmissionBlocked("MM market readiness check is unavailable.")
        markets = ((row['mm_session'] or {}).get('catalogue') or {}).get('markets') or {}
        reduction = any((market.get('inventory_reduction') or {}).get('mode') == 'close_first'
                        for market in markets.values() if isinstance(market, dict))
        if reduction and (self.mm_order_ready_check is None or self.mm_quote_check is None
                          or submission and (connector is None or order is None)):
            raise SubmissionBlocked('Close-first order authority is unavailable')
        if self.mm_order_ready_check is None or connector is None or order is None:
            self.mm_order_readiness_reason = None
        ready = (self.mm_order_ready_check(connector, order) if self.mm_order_ready_check is not None
                 and connector is not None and order is not None else self.mm_ready_check())
        if not ready:
            reason = getattr(self, 'mm_order_readiness_reason', None)
            detail = (' reason=' + reason if isinstance(reason, str) and 0 < len(reason) <= 80
                      and reason.isascii() and all(c.islower() or c.isdigit() or c == '_' for c in reason) else '')
            raise MarketReadinessBlocked("MM markets/data/exposure are not jointly ready." + detail)

    async def finish_account_mutation(self, token, response):
        try:
            payload = await asyncio.wait_for(response.json(), timeout=5)
            if response.status >= 400 or str(payload.get("code")) != "0":
                raise SubmissionBlocked("Account mutation was not positively acknowledged.")

            def finish(state):
                pending = state.get("pending_mutations") or []
                if [item.get("token") for item in pending] != [token]:
                    raise SubmissionBlocked("Account mutation acknowledgment does not match durable state.")
                state.pop("pending_mutations", None)
            self._mutate_recovery(finish)
        except BaseException:
            self.account_mutation_failed(token)
            raise
        finally:
            if self.account_token == token:
                self.account_token = None
                self.account_lock.release()

    def account_mutation_failed(self, token):
        self.require_recovery("An account mutation has an unresolved acknowledgment; verify exchange settings before recovery.")
        if self.account_token == token:
            self.account_token = None
            self.account_lock.release()

    def bind_mm_drawdown(self, strategy):
        """Extend the existing strategy checkpoint; peaks are bot/market scoped."""
        row = self.check_owner()
        if row["config_type"] != "mm-session" or self.config_file_path != "life_mm_profiles.yaml":
            raise SubmissionBlocked("Managed drawdown requires the MM service identity.")
        if row["control_version"] != self.version:
            return False  # A superseded initialization must not bind old policy.
        checkpoint = (row["recovery_state"] or {}).get("strategy_checkpoints", {}).get(self.config_file_path, {})
        policy = strategy.mm_drawdown_policy()
        bound = checkpoint.get("mm_drawdown_policy")
        if bound is not None and bound != policy:
            self.require_recovery("drawdown_policy_mismatch")
            raise SubmissionBlocked("Drawdown policy/scope changed; operator-reviewed maintenance is required.")
        try:
            peak = Decimal(checkpoint.get("max_global_pnl", "0"))
            peaks = {identity: Decimal(value) for identity, value in checkpoint.get("max_pnl_by_controller", {}).items()}
            if (not peak.is_finite() or peak < 0 or any(not value.is_finite() or value < 0 for value in peaks.values())
                    or (bound is None and (peak != 0 or any(peaks.values())))
                    or any(value != 0 and identity not in policy["scope"] for identity, value in peaks.items())):
                raise ValueError("Invalid or unbound peak scope")
        except (ValueError, ArithmeticError, TypeError):
            self.require_recovery("drawdown_checkpoint_invalid")
            raise SubmissionBlocked("Drawdown checkpoint coverage is invalid; operator review is required.") from None
        strategy.max_global_pnl = peak
        strategy.max_pnl_by_controller = {identity: peaks.get(identity, Decimal("0")) for identity in policy["scope"]}
        strategy._is_stop_triggered = bool(checkpoint.get("_is_stop_triggered", False))
        strategy.drawdown_exited_controllers = list(checkpoint.get("drawdown_exited_controllers", []))
        strategy._mm_drawdown_breach = checkpoint.get("mm_drawdown_breach")
        saved = strategy.mm_drawdown_checkpoint()
        if not self.checkpoint_mm_drawdown(saved, bind_version=self.version):
            return False
        strategy.mm_drawdown_checkpoint_callback = self.checkpoint_mm_drawdown
        strategy._mm_drawdown_last_checkpoint = saved
        strategy._mm_drawdown_bound = True
        return True

    def checkpoint_mm_drawdown(self, checkpoint, *, bind_version=None):
        def save(state):
            checkpoints = dict(state.get("strategy_checkpoints") or {})
            previous = dict(checkpoints.get(self.config_file_path) or {})
            bound = previous.get("mm_drawdown_policy")
            if bound is not None and bound != checkpoint["mm_drawdown_policy"]:
                raise SubmissionBlocked("Drawdown policy changed during checkpoint publication.")
            old = dict(previous)
            previous.update(checkpoint)
            previous["max_global_pnl"] = str(max(Decimal(checkpoint["max_global_pnl"]), Decimal(old.get("max_global_pnl", "0"))))
            previous["max_pnl_by_controller"] = {
                identity: str(max(Decimal(value), Decimal((old.get("max_pnl_by_controller") or {}).get(identity, "0"))))
                for identity, value in checkpoint["max_pnl_by_controller"].items()}
            if old.get("_is_stop_triggered"):
                previous.update(_is_stop_triggered=True, drawdown_exited_controllers=old.get("drawdown_exited_controllers", []),
                                mm_drawdown_breach=old.get("mm_drawdown_breach"))
            checkpoints[self.config_file_path] = previous
            state["strategy_checkpoints"] = checkpoints
        return self._mutate_recovery(save, expected_version=bind_version)

    async def reconcile(self, core):
        """Reuse connector read APIs and tracker events before adding strategy to clock."""
        row = self.check_owner()
        self.connectors = core.connector_manager.connectors
        self.allow_new_orders = False
        halted = bool((row["recovery_state"] or {}).get("halt"))
        checkpoint = (row["recovery_state"] or {}).get("strategy_checkpoints", {}).get(self.config_file_path, {})
        for name in ("_is_stop_triggered", "drawdown_exited_controllers"):
            if name in checkpoint and hasattr(core.strategy, name):
                setattr(core.strategy, name, checkpoint[name])
        if "max_global_pnl" in checkpoint and hasattr(core.strategy, "max_global_pnl"):
            core.strategy.max_global_pnl = Decimal(checkpoint["max_global_pnl"])
        if "max_pnl_by_controller" in checkpoint and hasattr(core.strategy, "max_pnl_by_controller"):
            core.strategy.max_pnl_by_controller = {key: Decimal(value) for key, value in checkpoint["max_pnl_by_controller"].items()}
        scope = getattr(getattr(core.strategy, "config", None), "mm_drawdown_scope", None)
        if isinstance(scope, dict) and scope:
            self.bind_mm_drawdown(core.strategy)

        may_have_exchange_effects = False
        automatic_bootstrap = (self.automatic_recovery.status['phase'] == 'bootstrapping_recovery'
                               or self.automatic_recovery.isolated)

        async def perform():
            nonlocal may_have_exchange_effects
            # Query the dedicated account's complete open-order set. Unknown client
            # IDs require reconciliation; never cancel another writer's orders blindly.
            known = {order_id for connector in self.connectors.values() for order_id in connector.in_flight_orders}
            for connector in self.connectors.values():
                open_orders = await self._open_orders(connector)
                await reconcile_open_order_visibility(self, core.trade_fill_db, connector, open_orders, known)
            for connector in self.connectors.values():
                restored = [order for order in connector.in_flight_orders.values()
                            if not (self.automatic_recovery.isolated
                                    and order.client_order_id in self.automatic_recovery.quarantined)]
                await self._refresh_orders(connector, restored)
                if any(not order.is_done for order in restored):
                    # Once a cancellation may have reached the exchange, an
                    # interrupted reconciliation still requires recovery.
                    may_have_exchange_effects = True
                    if self.automatic_recovery.isolated:
                        for order in restored:
                            if not order.is_done:
                                connector.cancel(order.trading_pair, order.client_order_id)
                    else:
                        await connector.cancel_all(5)
                    while any(not order.is_done for order in restored):
                        self.check_owner()
                        await self._refresh_orders(connector, [order for order in restored if not order.is_done])
                        await asyncio.sleep(0.1)
                if hasattr(connector, "_update_positions"):
                    await connector._update_positions()
            if automatic_bootstrap:
                await self.automatic_recovery.project(core)
            for connector in self.connectors.values():
                if hasattr(connector, '_update_positions'):
                    self._verify_positions(core, connector)
            core.trade_fill_db.assert_persistence_healthy()
            self._check_intents(core.markets_recorder)
        try:
            # Native connector tasks reconnect in the background. Waiting for
            # readiness grants no send authority and attempts no cancellation;
            # it must not consume the deadline for exchange reconciliation.
            # Stop/Switch still cancels this task and ownership is rechecked.
            while not all(connector.ready for connector in self.connectors.values()):
                self.check_owner()
                await asyncio.sleep(.1)
            if automatic_bootstrap:
                # Each venue request/work unit is bounded. Unknown outcomes may
                # outlast startup's ordinary drain deadline without inventing a
                # terminal order or requiring a process restart.
                await self.automatic_recovery.restore(core)
                if self.automatic_recovery.isolated:
                    self.automatic_recovery.attach_quarantined_entries(core)
            if automatic_bootstrap:
                while True:
                    try:
                        await perform()
                        break
                    except (IOError, asyncio.TimeoutError) as error:
                        self.check_owner()
                        self.automatic_recovery.note_bootstrap_wait(error, 1)
                        await asyncio.sleep(1)
            else:
                await asyncio.wait_for(perform(), timeout=30)
        except asyncio.CancelledError:
            # Stop/Switch may interrupt readiness or GET-only initialization.
            # No send authority has been granted and no exchange mutation was
            # attempted. Preserve prior HALT/intents, but do not invent a new
            # unresolved-effect HALT for a cleanly cancelled read.
            if may_have_exchange_effects and not automatic_bootstrap:
                self.require_recovery("Exchange reconciliation did not complete; new risk remains blocked.")
            raise
        except BaseException as error:
            if isinstance(error, UntrackedOpenOrders):
                self._mutate_recovery(lambda state, evidence=error.evidence: state.update(startup_untracked_orders=evidence))
            self.require_recovery("Exchange reconciliation did not complete; new risk remains blocked.")
            raise
        finally:
            self.reconciling = False
        if halted or getattr(core.strategy, "_is_stop_triggered", False):
            raise SubmissionBlocked("Reconciliation ran, but prior unresolved effects still require operator recovery.")
        final_state = self.check_owner()["recovery_state"] or {}
        if final_state.get("halt") or final_state.get("pending_mutations"):
            raise SubmissionBlocked("Reconciliation has unresolved durable effects.")
        from hummingbot.core.spot_outflow import SpotOutflowPolicy
        policy = getattr(getattr(core.strategy, "config", None), "mm_spot_outflow_policy", None)
        if isinstance(policy, SpotOutflowPolicy):
            from hummingbot.core.spot_outflow_guard import SpotOutflowGuard
            self.spot_outflow = SpotOutflowGuard(self, policy.model_dump(mode="json"))
            # Controllers may still finish account setup while a large window
            # is reconstructed. Keep setup permission without admitting orders.
            self.reconciling = True
            try:
                await self.spot_outflow.initialize()
                policies = getattr(core.strategy.config, "mm_bootstrap_policies", None)
                if policies:
                    from hummingbot.core.listing_bootstrap import ListingBootstrapGuard
                    self.listing_bootstrap = ListingBootstrapGuard(
                        self, {key: value.model_dump() for key, value in policies.items()}, core.strategy.controllers)
                    self.listing_bootstrap.bind_if_ready()
                elif final_state.get("listing_bootstrap"):
                    self.require_recovery("bootstrap_policy_unavailable")
                    raise SubmissionBlocked("Armed listing_bootstrap requires its original policy")
            finally:
                self.reconciling = False
            if (self.check_owner()["recovery_state"] or {}).get("halt"):
                raise SubmissionBlocked("spot_outflow binding requires recovery before new orders")
        elif final_state.get("spot_outflow"):
            self.require_recovery("spot_outflow_policy_unavailable")
            raise SubmissionBlocked("Armed spot_outflow requires its original managed policy")
        controllers = getattr(core.strategy, 'controllers', {})
        if (any(getattr(getattr(controller.config, 'spread_policy', None), 'volatility', None) is not None
                and controller.config.spread_policy.volatility.defense is not None for controller in controllers.values())
                or (final_state.get('volatility_defense') or {}).get('pauses')):
            from hummingbot.core.volatility_defense import VolatilityCheckpoints
            self.volatility_checkpoints = VolatilityCheckpoints(self, controllers)
            self.volatility_checkpoints.attach()
        else:
            self.volatility_checkpoints = None
        if self.automatic_recovery.status['phase'] == 'settled_stopped' and not self.automatic_recovery.isolated:
            # A separate explicit Start has just refreshed/cancelled restored
            # orders and verified durable intents and current risk. Retire the
            # previous session's resume scope only after its proof task drains.
            await self.automatic_recovery.close()
            from hummingbot.core.automatic_recovery import AutomaticOrderRecovery
            self.automatic_recovery = AutomaticOrderRecovery(self)
        if row.get('config_type') == 'mm-session':
            self.automatic_recovery.status['cold_recovery_supported'] = bool(core.markets_recorder.ensure_runtime_checkpoint())
        if automatic_bootstrap:
            self.automatic_recovery.status.update(phase='reconciling' if self.automatic_recovery.isolated else 'idle',
                                                  reason_code='order_outcome_unknown' if self.automatic_recovery.isolated else None,
                                                  operator_required=False)
            self.automatic_recovery.bootstrap_orders.clear()

            def recovered(state):
                state.update(automatic_recovery=dict(self.automatic_recovery.status))
                state.pop('shutdown_incomplete', None)
            self._mutate_recovery(recovered)
        elif row.get('config_type') == 'mm-session':
            self._mutate_recovery(lambda state: state.update(automatic_recovery=dict(self.automatic_recovery.status)))
        self.allow_new_orders = True

    def fill_committed(self, row):
        if row.get('late_held_fill'):
            self.require_recovery('A new fill arrived after its durable position transfer; financial reconciliation is required.')
        if self.spot_outflow is not None:
            try:
                self.spot_outflow.fill_committed(row)
            except (ValueError, TypeError, KeyError, ArithmeticError):
                self.spot_outflow.reason_code = "spot_outflow_fill_invalid"

    @staticmethod
    async def _refresh_orders(connector, orders, *, limiter=None):
        # Failed maker attempts remain part of terminal checkpoint proof. Read
        # every order, but do not serialize their network latency across the
        # unchanged shutdown deadline. A fixed worker bound avoids one task per
        # historical attempt; connector throttling still controls both endpoints.
        pending = iter(orders)
        limiter = limiter if limiter is not None else asyncio.Semaphore(3)

        async def refresh():
            for order in pending:
                async with limiter:
                    status = await asyncio.wait_for(connector._request_order_status(order), timeout=5)
                    if status.exchange_order_id:
                        order.update_exchange_order_id(status.exchange_order_id)
                    fills = await asyncio.wait_for(connector._all_trade_updates_for_order(order), timeout=5)
                    for fill in fills:
                        connector._order_tracker.process_trade_update(fill)
                    # Apply fills before terminal state for each individual order.
                    await connector._order_tracker.process_order_update(status)

        workers = [asyncio.create_task(refresh()) for _ in range(min(3, len(orders)))]
        try:
            await asyncio.gather(*workers)
        finally:
            # A failed/cancelled read cannot leave sibling network requests
            # mutating the tracker after the checkpoint caller has returned.
            for worker in workers:
                worker.cancel()
            await asyncio.gather(*workers, return_exceptions=True)

    @staticmethod
    async def _open_orders(connector, max_pages=100):
        # OKX documents cursor pagination by ordId and a maximum page size of 100:
        # https://www.okx.com/docs-v5/en/#order-book-trading-trade-get-order-list
        # Reuse the existing authenticated client and its conservative order-query
        # rate bucket instead of adding another HTTP client/rate-limit subsystem.
        if connector.name == "okx":
            limit_id = ORDER_PATH
        else:
            from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_web_utils import (
                get_rest_api_limit_id_for_endpoint,
            )
            limit_id = get_rest_api_limit_id_for_endpoint("GET", ORDER_PATH)
        result, after = [], None
        for _ in range(max_pages):
            params = {"limit": "100"}
            if after:
                params["after"] = after
            response = await asyncio.wait_for(connector._api_get(path_url="/api/v5/trade/orders-pending", params=params,
                                                                 is_auth_required=True, limit_id=limit_id), timeout=5)
            if str(response.get("code")) != "0" or not isinstance(response.get("data"), list):
                raise SubmissionBlocked("Open-order reconciliation response is invalid.")
            page = response["data"]
            result.extend(page)
            if len(page) < 100:
                return result
            cursor = str(page[-1]["ordId"])
            if cursor == after:
                raise SubmissionBlocked("Open-order pagination did not progress.")
            after = cursor
        raise SubmissionBlocked("Open-order reconciliation exceeded its bounded page count.")

    @staticmethod
    def _verify_positions(core, connector):
        orchestrator = getattr(core.strategy, "executor_orchestrator", None)
        expected = {}
        if orchestrator is not None:
            orchestrator.initialize_initial_positions()
            for positions in orchestrator.positions_held.values():
                for position in positions:
                    if position.connector_name != connector.name or not position.net_amount_base:
                        continue
                    side = "LONG" if position.net_amount_base > 0 else "SHORT"
                    key = (position.trading_pair, side)
                    expected[key] = expected.get(key, Decimal("0")) + abs(position.net_amount_base)
        actual = {}
        for position in connector.account_positions.values():
            amount = Decimal(position.amount)
            if amount:
                side = position.position_side.name
                if side not in ("LONG", "SHORT"):
                    side = "LONG" if amount > 0 else "SHORT"
                key = (position.trading_pair, side)
                actual[key] = actual.get(key, Decimal("0")) + abs(amount)
        if actual != expected:
            raise SubmissionBlocked("Exchange positions do not match restored position holds/configured initial positions.")

    def release(self):
        """Caller must stop all order-producing tasks before releasing the session."""
        self.allow_new_orders = False
        if any(not task.done() for task in self.submission_tasks | self.terminal_tasks | set(self.transport_tasks)) or self.account_token:
            raise SubmissionBlocked("Stop all order-producing/transport tasks before releasing ownership.")
        connection, self.connection = self.connection, None
        if connection is not None:
            if connection.closed or connection.invalidated:
                # A new physical connection cannot release the old session's
                # advisory lock. Teardown must not reconnect or mutate through it.
                connection.close()
                return
            try:
                connection.rollback()
                statement = update(BotState).where(
                    BotState.bot_id == self.store.bot_id, BotState.owner_uuid == self.owner_uuid,
                    BotState.owner_generation == self.generation).values(owner_uuid=None)
                connection.execute(statement)
                connection.execute(text("SELECT pg_advisory_unlock(:namespace, :key)"),
                                   {"namespace": OWNER_NAMESPACE, "key": OWNER_KEY})
                connection.commit()
            except Exception:
                connection.invalidate()
            finally:
                connection.close()

    @trace_runtime('shutdown.total')
    async def shutdown_core(self, core, timeout=8.0) -> bool:
        """Retain ownership until tasks stop; preserve uncertainty before cleanup.

        False means the host must exit without returning ownership to standby while
        remaining tasks can act. The service host supplies the process hard deadline.
        """
        self.allow_new_orders = False
        self.reconciling = False
        row = self.check_owner()
        was_halted = bool((row["recovery_state"] or {}).get("halt"))
        checkpoint = {}
        strategy = core.strategy
        scope = None
        if strategy is not None:
            for name in ("_is_stop_triggered", "drawdown_exited_controllers"):
                if hasattr(strategy, name):
                    checkpoint[name] = getattr(strategy, name)
            if hasattr(strategy, "max_global_pnl"):
                checkpoint["max_global_pnl"] = str(strategy.max_global_pnl)
            if hasattr(strategy, "max_pnl_by_controller"):
                checkpoint["max_pnl_by_controller"] = {key: str(value) for key, value in strategy.max_pnl_by_controller.items()}
            scope = getattr(getattr(strategy, "config", None), "mm_drawdown_scope", None)
            if isinstance(scope, dict) and scope and getattr(strategy, "_mm_drawdown_bound", False):
                checkpoint.update(strategy.mm_drawdown_checkpoint())
            if "_is_stop_triggered" in checkpoint:
                from hummingbot.core.mm_drawdown import checkpoint_stop_triggered
                checkpoint["_is_stop_triggered"] = checkpoint_stop_triggered(strategy)

        from sqlalchemy.orm import Session

        from hummingbot.model.runtime_journal import can_bootstrap
        with Session(self.engine) as session:
            recoverable_shutdown = (not was_halted and not checkpoint.get('_is_stop_triggered')
                                    and not (row['recovery_state'] or {}).get('pending_mutations')
                                    and can_bootstrap(session, row))
        deferred = self.automatic_recovery.defer_shutdown(core) if recoverable_shutdown else set()

        def begin_shutdown(state):
            state.update(halt=True, shutdown_incomplete=True)
            if recoverable_shutdown and not state.get('reason'):
                state['halt_kind'] = 'shutdown_pending'
            if self.config_file_path is not None:
                checkpoints = dict(state.get("strategy_checkpoints") or {})
                previous = dict(checkpoints.get(self.config_file_path) or {})
                if not (previous.get("mm_drawdown_policy") is not None
                        and isinstance(scope, dict) and scope and not getattr(strategy, "_mm_drawdown_bound", False)):
                    previous.update(checkpoint)
                checkpoints[self.config_file_path] = previous
                state["strategy_checkpoints"] = checkpoints
        self._mutate_recovery(begin_shutdown)
        deadline = asyncio.get_running_loop().time() + timeout
        # A cancel acknowledgment alone is not terminal. Read back known orders
        # while connectors/recorder still run, before returning ownership.
        tracked = [(connector, list(connector.in_flight_orders.values())) for connector in self.connectors.values()]
        # V2's terminal-first checkpoint covers live PositionExecutors. Retain
        # the final change guard for older holds and unsupported executors.
        held_snapshot = hasattr(strategy, "executor_orchestrator")
        execution_before_stop = {order.client_order_id: order.executed_amount_base
                                 for _, orders in tracked for order in orders}

        async def read_terminal_orders():
            # Share the existing three read slots across connectors. A delayed
            # Spot tail must not prevent all Perpetual proof from even starting.
            # Both checkpoint phases still read every tracked order afresh.
            limiter = asyncio.Semaphore(3)

            async def refresh_connector(connector, orders):
                remote_orders = []
                for order in orders:
                    if order.client_order_id in deferred:
                        continue  # Durable quarantined intent survives shutdown; no terminal claim.
                    # A create can be rejected by our final send gate while
                    # shutdown is already awaiting earlier cancels. Such a
                    # failed, never-transported order cannot exist remotely;
                    # asking OKX for it yields an empty not-found response.
                    # Sent/uncertain failures still require exchange readback.
                    if (order.is_done and (self.unsent_rejections.get(order.client_order_id) is order
                                           or self.expired_rejections.get(order.client_order_id) is order) and not order.exchange_order_id
                            and order.client_order_id not in self.sent_intents):
                        continue
                    remote_orders.append(order)
                if remote_orders:
                    await self._refresh_orders(connector, remote_orders, limiter=limiter)
                while any(not order.is_done and order.client_order_id not in deferred for order in orders):
                    self.check_owner()
                    await asyncio.sleep(0.1)
                    await self._refresh_orders(connector, [order for order in orders
                                                           if not order.is_done and order.client_order_id not in deferred], limiter=limiter)

            refreshes = [asyncio.create_task(refresh_connector(connector, orders)) for connector, orders in tracked]
            try:
                await asyncio.gather(*refreshes)
            finally:
                # No sibling may keep mutating the tracker after proof failure
                # or cancellation; each reader also drains its bounded workers.
                for task in refreshes:
                    task.cancel()
                await asyncio.gather(*refreshes, return_exceptions=True)
            core.trade_fill_db.assert_persistence_healthy()
            if self.spot_outflow is not None:
                if deferred:
                    await self.spot_outflow.reconcile_terminal(all_orders=True, deferred=deferred)
                else:
                    await self.spot_outflow.reconcile_terminal(all_orders=True)

        def changed_orders():
            return {order.client_order_id for _, orders in tracked for order in orders
                    if order.client_order_id not in deferred
                    and order.executed_amount_base != execution_before_stop[order.client_order_id]}

        def reject_checkpoint_change():
            self.require_recovery("A fill arrived during position checkpoint cleanup; operator reconciliation is required.")
            raise SubmissionBlocked("Position checkpoint changed during shutdown.")

        @trace_runtime('shutdown.proof_before')
        async def before_position_checkpoint(checkpoint_orders):
            # The orchestrator has stopped its PositionExecutor control loops,
            # retaining live order objects but taking no held-position snapshot.
            # An order may have left connector.in_flight_orders while controllers
            # were draining; its retained reference still needs terminal readback.
            by_connector = {id(connector): orders for connector, orders in tracked}
            for order_id, (name, order) in checkpoint_orders.items():
                orders = by_connector.get(id(self.connectors.get(name)))
                if orders is None or order.client_order_id != order_id:
                    raise SubmissionBlocked('Position checkpoint order identity is unavailable.')
                if any(existing.client_order_id == order_id and existing is not order for existing in orders):
                    raise SubmissionBlocked('Position checkpoint order reference does not match the connector.')
                if all(existing is not order for existing in orders):
                    orders.append(order)
                    execution_before_stop.setdefault(order_id, order.executed_amount_base)
            await core.cancel_outstanding_orders()
            await read_terminal_orders()
            if not changed_orders() <= checkpoint_orders.keys():
                reject_checkpoint_change()  # An older/already-processed hold is not repairable here.
            proof = {order.client_order_id: order.to_json() for _, orders in tracked for order in orders
                     if order.client_order_id in checkpoint_orders}
            execution_before_stop.update({order.client_order_id: order.executed_amount_base
                                          for _, orders in tracked for order in orders})
            return proof

        @trace_runtime('shutdown.proof_after')
        async def confirm_cancellations():
            await read_terminal_orders()
            if held_snapshot and changed_orders():
                # A change after terminal proof/snapshot is still uncertainty.
                # Unsupported executor types also retain their original guard.
                reject_checkpoint_change()

        core.before_position_checkpoint = before_position_checkpoint if held_snapshot else None
        core.after_order_cancellation = confirm_cancellations
        core.KILL_TIMEOUT = min(core.KILL_TIMEOUT, max(0.1, timeout / 3))
        shutdown = asyncio.create_task(core.shutdown(keep_positions=True, shutdown_timeout=timeout / 3))
        done, pending = await asyncio.wait({shutdown}, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        if pending:
            stages = active_shutdown_stages()
            shutdown.cancel()
            logging.getLogger(__name__).error(
                "Runtime shutdown exceeded deadline timeout_s=%.3f tracked_orders=%d stages=%s; ownership and recovery fence retained.",
                timeout, sum(len(orders) for _, orders in tracked), stages,
                extra={"event": "runtime_shutdown_timeout", "reason_code": "runtime_shutdown_timeout",
                       "tracked_order_count": sum(len(orders) for _, orders in tracked), "timeout_seconds": timeout,
                       "shutdown_stages": stages})
            return False
        if not shutdown.result():
            return False
        # Stop queued/in-flight create tasks as well as the clock/controllers. Their
        # cancellation handler resolves proven-unsent intents, keeping sent ones pending.
        requests = {task for task in self.submission_tasks | self.terminal_tasks | set(self.transport_tasks) if not task.done()}
        for task in requests:
            task.cancel()
        if requests:
            _, pending = await asyncio.wait(requests, timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if pending:
                return False
        with self.engine.connect() as connection:
            unresolved = connection.execute(select(Order.id).where(
                Order.creation_pending.is_(True), Order.id.not_in(deferred)).limit(1)).first()
        recovery = self.check_owner()["recovery_state"] or {}
        if unresolved or recovery.get("pending_mutations"):
            return False

        def finish(state):
            state.pop("shutdown_incomplete", None)
            state["runtime_active"] = False
            if not was_halted and not state.get("reason"):
                state.pop("halt", None)
                if state.get('halt_kind') == 'shutdown_pending':
                    state.pop('halt_kind', None)
                if deferred:
                    self.automatic_recovery.status.update(phase='settled_stopped', reason_code='uncertain_effects_retained')
                    state['automatic_recovery'] = dict(self.automatic_recovery.status)
                elif self.automatic_recovery.active or state.get('automatic_recovery'):
                    self.automatic_recovery.status.update(phase='idle', reason_code=None, operator_required=False)
                    state['automatic_recovery'] = dict(self.automatic_recovery.status)
        self._mutate_recovery(finish)
        return True
