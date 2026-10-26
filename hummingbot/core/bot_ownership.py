"""Dedicated PostgreSQL session ownership and conservative exchange recovery."""
import asyncio
import json
import time
import uuid
from datetime import datetime
from decimal import Decimal
from urllib.parse import urlparse

from sqlalchemy import select, text, update

from hummingbot.core.order_submission_guard import SUPPORTED_CONNECTORS, SubmissionBlocked
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import BotStateStore
from hummingbot.model.order import Order

OWNER_NAMESPACE = 1212305236
OWNER_KEY = 1
ORDER_PATH = "/api/v5/trade/order"
CANCEL_PATHS = {"/api/v5/trade/cancel-order", "/api/v5/trade/cancel-batch-orders"}
ACCOUNT_PATHS = {"/api/v5/account/set-leverage", "/api/v5/account/set-position-mode"}


class OwnershipLost(SubmissionBlocked):
    pass


class BotOwnership:
    def __init__(self, engine, bot_id):
        self.engine = engine
        self.store = BotStateStore(engine, bot_id)
        self.owner_uuid = str(uuid.uuid4())
        self.generation = None
        self.connection = None
        self.backend_pid = None
        self.lost = False
        self.allow_new_orders = False
        self.reconciling = False
        self.version = None
        self.config_file_path = None
        self.connectors = {}
        self.active_intents = set()
        self.sent_intents = set()
        self.unsent_rejections = set()
        self.submission_tasks = set()
        self.transport_tasks = {}
        self.account_lock = asyncio.Lock()
        self.account_token = None
        self.mm_ready_check = None
        self.runtime_ready_check = None
        self.intent_scopes = {}

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
            if pending or recovery.get("pending_mutations") or recovery.get("runtime_active"):
                recovery.update(halt=True, reason="A previous process has unresolved exchange effects; reconcile and verify it is stopped.")
            self.generation = row["owner_generation"] + 1
            connection.execute(update(BotState).where(BotState.bot_id == self.store.bot_id).values(
                owner_uuid=self.owner_uuid, owner_generation=self.generation,
                heartbeat=text("clock_timestamp()"), recovery_state=recovery))
            self.backend_pid = connection.execute(text("SELECT pg_backend_pid()")).scalar()
            connection.commit()
            self.connection = connection
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

    def check_owner(self, *, new_risk=False, allow_setup=False):
        connection = self.connection
        if self.lost or connection is None or connection.closed or connection.invalidated:
            self.lost = True
            self.allow_new_orders = False
            raise OwnershipLost("The bot ownership session is unavailable.")
        try:
            row = connection.execute(text("""
                SELECT b.*, pg_backend_pid() AS backend_pid,
                    EXTRACT(EPOCH FROM clock_timestamp()) AS database_now FROM public.bot_state b
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
        if new_risk and ((not self.allow_new_orders and not allow_setup) or row["desired_state"] != "running"
                         or row["control_version"] != self.version or row["config_file_path"] != self.config_file_path
                         or (row["recovery_state"] or {}).get("halt")
                         or (row["recovery_state"] or {}).get("pending_mutations")):
            raise SubmissionBlocked("Trading is stopped, unreconciled, superseded or requires recovery.")
        if new_risk:
            from hummingbot.connector.markets_recorder import MarketsRecorder
            recorder = MarketsRecorder._shared_instance
            if recorder is not None and recorder.sql_manager.engine is self.engine:
                recorder.sql_manager.assert_persistence_healthy()
        return dict(row)

    def _mutate_recovery(self, change) -> None:
        self.check_owner()
        with self.connection.begin():
            row = self.connection.execute(select(BotState.recovery_state).where(
                BotState.bot_id == self.store.bot_id, BotState.owner_uuid == self.owner_uuid,
                BotState.owner_generation == self.generation).with_for_update()).one()
            recovery = dict(row[0] or {})
            change(recovery)
            self.connection.execute(update(BotState).where(BotState.bot_id == self.store.bot_id,
                                                           BotState.owner_uuid == self.owner_uuid,
                                                           BotState.owner_generation == self.generation)
                                    .values(recovery_state=recovery))

    def require_recovery(self, reason):
        self.allow_new_orders = False
        try:
            self._mutate_recovery(lambda state: state.update(halt=True, reason=reason))
        except Exception:
            self.lost = True

    def _check_intents(self, recorder):
        recorder.sql_manager.assert_persistence_healthy()
        try:
            with recorder.sql_manager.get_new_session() as session:
                pending = session.query(Order.id, Order.market).filter(Order.creation_pending.is_(True)).all()
        except Exception:
            recorder.sql_manager.persistence_healthy = False
            self.allow_new_orders = False
            raise SubmissionBlocked("Recorder state is unavailable; reconciliation is required.") from None
        ids = {order_id for order_id, _ in pending}
        self.active_intents.intersection_update(ids)
        self.sent_intents.intersection_update(ids)
        self.intent_scopes = {key: value for key, value in self.intent_scopes.items() if key in ids}
        for order_id, market in pending:
            connector = next((value for value in self.connectors.values() if value.display_name == market), None)
            tracked = connector.in_flight_orders.get(order_id) if connector is not None else None
            if order_id not in self.active_intents or tracked is None or tracked.is_done:
                self.require_recovery("An order submission has an unresolved outcome; no automatic resubmission is allowed.")
                raise SubmissionBlocked("Unresolved order submission requires reconciliation.")

    def before_order(self, connector, order):
        from hummingbot.core.order_submission_guard import current_session_scope
        row = self.check_owner(new_risk=True)
        if self.runtime_ready_check is not None and not self.runtime_ready_check():
            raise SubmissionBlocked("A required runtime task is not healthy.")
        scope = current_session_scope()
        self._check_mm_authority(row, scope)
        if connector.name not in SUPPORTED_CONNECTORS:
            raise SubmissionBlocked("This connector has not been verified for the PostgreSQL service.")
        from hummingbot.connector.markets_recorder import MarketsRecorder
        recorder = MarketsRecorder._shared_instance
        if (recorder is None or recorder.sql_manager.engine is not self.engine
                or recorder.config_file_path != self.config_file_path or connector not in recorder._markets):
            raise SubmissionBlocked("The active recorder/configuration does not match the service owner.")
        self._check_intents(recorder)
        recorder.record_order_intent(connector, order)
        self.active_intents.add(order.client_order_id)
        self.intent_scopes[order.client_order_id] = scope
        try:
            task = asyncio.current_task()
        except RuntimeError:
            task = None
        if task is not None:
            self.submission_tasks.add(task)
            task.add_done_callback(self.submission_tasks.discard)

    def order_failed(self, connector, order):
        if order.client_order_id not in self.sent_intents and not order.exchange_order_id:
            # This hook runs only in this process's create coroutine. Retain
            # positive no-transport evidence even if a later cancel-not-found
            # acknowledgement changes FAILED to CANCELED in the tracker.
            self.unsent_rejections.add(order.client_order_id)
        if order.client_order_id in self.sent_intents:
            self.require_recovery("An order submission did not finish with a durable acknowledgment.")
        elif order.client_order_id in self.active_intents:
            # No transport attempt passed the final gate in this process. It is
            # safe to reject that local intent, if this process still owns the DB.
            try:
                self.check_owner()
                from hummingbot.connector.markets_recorder import MarketsRecorder
                recorder = MarketsRecorder._shared_instance
                recorder.reject_unsent_order(connector, order.client_order_id)
                self.active_intents.discard(order.client_order_id)
                self.intent_scopes.pop(order.client_order_id, None)
            except Exception:
                self.require_recovery("An unsent order intent could not be resolved durably.")

    def transport_started(self):
        task = asyncio.current_task()
        self.transport_tasks[task] = self.transport_tasks.get(task, 0) + 1

    def transport_finished(self):
        task = asyncio.current_task()
        count = self.transport_tasks.get(task, 0)
        if count > 1:
            self.transport_tasks[task] = count - 1
        else:
            self.transport_tasks.pop(task, None)

    def fence_session(self, session):
        self.check_owner()
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
        row = self.check_owner(new_risk=path == ORDER_PATH)
        if path in CANCEL_PATHS:
            return None
        if path == ORDER_PATH:
            if self.runtime_ready_check is not None and not self.runtime_ready_check():
                raise SubmissionBlocked("A required runtime task is not healthy.")
            data = json.loads(request.data or "{}")
            client_id = data.get("clOrdId")
            self._check_mm_authority(row, self.intent_scopes.get(client_id))
            from hummingbot.connector.markets_recorder import MarketsRecorder
            recorder = MarketsRecorder._shared_instance
            if recorder is None:
                raise SubmissionBlocked("Order submission has no active recorder.")
            self._check_intents(recorder)
            if client_id not in self.active_intents or client_id in self.sent_intents:
                raise SubmissionBlocked("Order identity is absent or was already sent; reconcile instead of retrying.")
            # OKX's documented expTime deadline also constrains a process paused
            # after this gate but before transport. It does not replace recovery.
            headers = dict(request.headers or {})
            stamp = headers.get("OK-ACCESS-TIMESTAMP")
            try:
                now = datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() if stamp else time.time()
                expiry = int(now * 1000) + 5000
                if "expTime" in headers:
                    expiry = min(expiry, int(headers["expTime"]))
                if row["config_type"] == "mm-session":
                    mm = row["mm_session"]
                    expiry = min(expiry, int(min(mm["expires_at"], mm["step_expires_at"]) * 1000))
            except (ValueError, TypeError):
                raise SubmissionBlocked("Order request expiration header is invalid.") from None
            headers["expTime"] = str(expiry)
            request.headers = headers
            self.sent_intents.add(client_id)
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

    def _check_mm_authority(self, row, scope):
        if row["config_type"] != "mm-session":
            if scope is not None:
                raise SubmissionBlocked("A previous MM executor cannot submit in another strategy.")
            return
        from hummingbot.core.mm_session import session_permits
        if (scope is None or not session_permits(row["mm_session"] or {}, session_id=scope[0], generation=scope[1],
                                                 now=float(row["database_now"]))):
            raise SubmissionBlocked("MM session/step authority expired or changed.")
        if self.mm_ready_check is None or not self.mm_ready_check():
            raise SubmissionBlocked("MM markets/data/exposure are not jointly ready.")

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

        async def perform():
            while not all(connector.ready for connector in self.connectors.values()):
                await asyncio.sleep(0.1)
                self.check_owner()
            # Query the dedicated account's complete open-order set. Unknown client
            # IDs require reconciliation; never cancel another writer's orders blindly.
            known = {order_id for connector in self.connectors.values() for order_id in connector.in_flight_orders}
            for connector in self.connectors.values():
                open_orders = await self._open_orders(connector)
                if any(item.get("clOrdId") not in known for item in open_orders):
                    raise SubmissionBlocked("The exchange account has untracked open orders.")
            for connector in self.connectors.values():
                restored = list(connector.in_flight_orders.values())
                await self._refresh_orders(connector, restored)
                if any(not order.is_done for order in restored):
                    await connector.cancel_all(5)
                    while any(not order.is_done for order in restored):
                        self.check_owner()
                        await self._refresh_orders(connector, [order for order in restored if not order.is_done])
                        await asyncio.sleep(0.1)
                if hasattr(connector, "_update_positions"):
                    await connector._update_positions()
                    self._verify_positions(core, connector)
            core.trade_fill_db.assert_persistence_healthy()
            self._check_intents(core.markets_recorder)
        try:
            await asyncio.wait_for(perform(), timeout=30)
        except BaseException:
            self.require_recovery("Exchange reconciliation did not complete; new risk remains blocked.")
            raise
        finally:
            self.reconciling = False
        if halted or getattr(core.strategy, "_is_stop_triggered", False):
            raise SubmissionBlocked("Reconciliation ran, but prior unresolved effects still require operator recovery.")
        final_state = self.check_owner()["recovery_state"] or {}
        if final_state.get("halt") or final_state.get("pending_mutations"):
            raise SubmissionBlocked("Reconciliation has unresolved durable effects.")
        self.allow_new_orders = True

    @staticmethod
    async def _refresh_orders(connector, orders):
        for order in orders:
            status = await connector._request_order_status(order)
            if status.exchange_order_id:
                order.update_exchange_order_id(status.exchange_order_id)
            fills = await connector._all_trade_updates_for_order(order)
            for fill in fills:
                connector._order_tracker.process_trade_update(fill)
            await connector._order_tracker.process_order_update(status)

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
            response = await connector._api_get(path_url="/api/v5/trade/orders-pending", params=params,
                                                is_auth_required=True, limit_id=limit_id)
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
        if any(not task.done() for task in self.submission_tasks | set(self.transport_tasks)) or self.account_token:
            raise SubmissionBlocked("Stop all order-producing/transport tasks before releasing ownership.")
        connection, self.connection = self.connection, None
        if connection is not None:
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
        if strategy is not None:
            for name in ("_is_stop_triggered", "drawdown_exited_controllers"):
                if hasattr(strategy, name):
                    checkpoint[name] = getattr(strategy, name)
            if hasattr(strategy, "max_global_pnl"):
                checkpoint["max_global_pnl"] = str(strategy.max_global_pnl)
            if hasattr(strategy, "max_pnl_by_controller"):
                checkpoint["max_pnl_by_controller"] = {key: str(value) for key, value in strategy.max_pnl_by_controller.items()}

        def begin_shutdown(state):
            state.update(halt=True, shutdown_incomplete=True)
            if self.config_file_path is not None:
                checkpoints = dict(state.get("strategy_checkpoints") or {})
                checkpoints[self.config_file_path] = checkpoint
                state["strategy_checkpoints"] = checkpoints
        self._mutate_recovery(begin_shutdown)
        deadline = asyncio.get_running_loop().time() + timeout
        # A cancel acknowledgment alone is not terminal. Read back known orders
        # while connectors/recorder still run, before returning ownership.
        tracked = [(connector, list(connector.in_flight_orders.values())) for connector in self.connectors.values()]
        # V2 snapshots held positions during on_stop, before final cancellation
        # readback. A concurrent fill makes that snapshot unsafe to resume.
        held_snapshot = hasattr(strategy, "executor_orchestrator")
        execution_before_stop = {order.client_order_id: order.executed_amount_base
                                 for _, orders in tracked for order in orders}

        async def confirm_cancellations():
            for connector, orders in tracked:
                for order in orders:
                    # A create can be rejected by our final send gate while
                    # shutdown is already awaiting earlier cancels. Such a
                    # failed, never-transported order cannot exist remotely;
                    # asking OKX for it yields an empty not-found response.
                    # Sent/uncertain failures still require exchange readback.
                    if (order.is_done and order.client_order_id in self.unsent_rejections and not order.exchange_order_id
                            and order.client_order_id not in self.sent_intents):
                        continue
                    await self._refresh_orders(connector, [order])
                while any(not order.is_done for order in orders):
                    self.check_owner()
                    await asyncio.sleep(0.1)
                    await self._refresh_orders(connector, [order for order in orders if not order.is_done])
            core.trade_fill_db.assert_persistence_healthy()
            if held_snapshot and any(order.executed_amount_base != execution_before_stop[order.client_order_id]
                                     for _, orders in tracked for order in orders):
                self.require_recovery("A fill arrived during position checkpoint cleanup; operator reconciliation is required.")
                raise SubmissionBlocked("Position checkpoint changed during shutdown.")

        core.after_order_cancellation = confirm_cancellations
        core.KILL_TIMEOUT = min(core.KILL_TIMEOUT, max(0.1, timeout / 3))
        shutdown = asyncio.create_task(core.shutdown(keep_positions=True, shutdown_timeout=timeout / 3))
        done, pending = await asyncio.wait({shutdown}, timeout=max(0, deadline - asyncio.get_running_loop().time()))
        if pending:
            shutdown.cancel()
            return False
        if not shutdown.result():
            return False
        # Stop queued/in-flight create tasks as well as the clock/controllers. Their
        # cancellation handler resolves proven-unsent intents, keeping sent ones pending.
        requests = {task for task in self.submission_tasks | set(self.transport_tasks) if not task.done()}
        for task in requests:
            task.cancel()
        if requests:
            _, pending = await asyncio.wait(requests, timeout=max(0, deadline - asyncio.get_running_loop().time()))
            if pending:
                return False
        with self.engine.connect() as connection:
            unresolved = connection.execute(select(Order.id).where(Order.creation_pending.is_(True)).limit(1)).first()
        recovery = self.check_owner()["recovery_state"] or {}
        if unresolved or recovery.get("pending_mutations"):
            return False

        def finish(state):
            state.pop("shutdown_incomplete", None)
            state["runtime_active"] = False
            if not was_halted and not state.get("reason"):
                state.pop("halt", None)
        self._mutate_recovery(finish)
        return True
