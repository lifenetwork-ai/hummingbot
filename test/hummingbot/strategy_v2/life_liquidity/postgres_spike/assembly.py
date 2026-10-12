"""Offline PostgreSQL assembly experiment; no production controller installs it."""
import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.postgres_spike.spike import Command, PgAuthority
from types import SimpleNamespace
from unittest.mock import patch

from bidict import bidict

from hummingbot.connector.exchange.okx import okx_constants as CONSTANTS
from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.connector.trading_rule import TradingRule
from hummingbot.core.api_throttler.async_throttler import AsyncThrottler
from hummingbot.core.data_type.common import OrderType, TradeType
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
from hummingbot.core.web_assistant.rest_assistant import RESTAssistant
from hummingbot.model.executors import Executors
from hummingbot.strategy.strategy_v2_base import StrategyV2Base
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.executors.order_executor.order_executor import OrderExecutor
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


@dataclass(frozen=True)
class Projection:
    payload: str

    @property
    def config(self):
        return SimpleNamespace(id=json.loads(self.payload)["id"])

    @property
    def executor_info(self):
        return ExecutorInfo.model_validate_json(self.payload)


class AssemblyAuthority(PgAuthority):
    def __init__(self, pool, app):
        super().__init__(pool)
        self.app = app

    async def on_reserved(self, connection, command):
        child = self.app.child(command.intent)
        snapshot = child.executor_info.model_dump_json()
        row = json.loads(snapshot)
        row["custom_info"].update(pg_account=command.account, pg_lineage=command.lineage, pg_owner=command.owner,
                                  pg_epoch=command.epoch, pg_session=command.session,
                                  pg_config_version=command.config_version, pg_risk_epoch=command.risk_epoch)
        blob = encoded(row)
        await self.execute(connection, """INSERT INTO recorder_operations(account,intent,wire,payload,payload_sha,state)
            VALUES ($1,$2,$3,$4::jsonb,$5,'READY')""", command.account, command.intent, command.wire,
                           blob, hashlib.sha256(blob.encode()).hexdigest())

    async def operation(self, command):
        async with self.connection() as connection:
            row = await self.fetchrow(connection, "SELECT * FROM recorder_operations WHERE account=$1 AND intent=$2",
                                      command.account, command.intent)
        if row is None or row["wire"] != command.wire:
            raise PermissionError("RECORDER_OPERATION_MISSING")
        payload = json.loads(row["payload"])
        if hashlib.sha256(encoded(payload).encode()).hexdigest() != row["payload_sha"]:
            raise PermissionError("RECORDER_OPERATION_CHANGED")
        return row, payload

    async def project(self, command):
        row, payload = await self.operation(command)
        self.app.recorder_entered.set()
        await self.app.recorder_release.wait()
        await asyncio.to_thread(self.app.recorder.store_or_update_executor, Projection(encoded(payload)))
        self.app.controller.fence(command)
        await self.verify_projection(command, require_applied=False)
        async with self.connection() as connection, connection.transaction():
            await self.account(connection, command.account)
            await self.execute(connection, """UPDATE recorder_operations SET state='APPLIED'
                WHERE account=$1 AND intent=$2 AND payload_sha=$3""", command.account, command.intent, row["payload_sha"])

    async def verify_projection(self, command, *, require_applied=True):
        operation, expected = await self.operation(command)
        if require_applied and operation["state"] != "APPLIED":
            raise PermissionError("RECORDER_PROJECTION_INCOMPLETE")

        def read():
            rows = self.app.recorder.get_executors_by_ids([command.intent])
            return [json.loads(row.to_executor_info().model_dump_json()) for row in rows]

        rows = await asyncio.to_thread(read)
        if len(rows) != 1:
            raise PermissionError("RECORDER_PROJECTION_MISSING")
        actual = rows[0]
        keys = ("recovery_order_ids", "recovery_account_uid", "pg_account", "pg_lineage", "pg_owner", "pg_epoch",
                "pg_session", "pg_config_version", "pg_risk_epoch")
        if (actual["id"] != command.intent or actual["controller_id"] != "life"
                or actual["config"] != expected["config"]
                or any(actual["custom_info"].get(key) != expected["custom_info"].get(key) for key in keys)
                or actual["custom_info"].get("recovery_order_ids") != [command.wire]
                or actual["custom_info"].get("recovery_account_uid") != "12345"):
            raise PermissionError("RECORDER_PROVENANCE_MISMATCH")
        return True

    async def settle_zero_fill(self, command, exchange_id):
        await self.verify_projection(command)
        return await super().settle_zero_fill(command, exchange_id)


class SyntheticHttp:
    """Only in-memory OKX envelopes; records actual connector wire/auth inputs."""

    def __init__(self):
        self.sent, self.cancels = [], []
        self.lose_ack = False

    async def request(self, *, method, url, params, data, headers):
        assert url.startswith("https://www.okx.com/")
        assert all(headers.get(key) for key in ("OK-ACCESS-KEY", "OK-ACCESS-SIGN", "OK-ACCESS-TIMESTAMP"))
        wire = json.loads(data)
        if url.endswith(CONSTANTS.OKX_PLACE_ORDER_PATH):
            assert method == "POST" and wire["ordType"] == "post_only" and wire["instId"] == "LIFE-USDT"
            self.sent.append(wire)
            if self.lose_ack:
                raise OSError("synthetic accepted wire with lost ACK")
            body = {"code": "0", "data": [{"sCode": "0", "ordId": "exchange-" + wire["clOrdId"],
                                           "clOrdId": wire["clOrdId"]}]}
        elif url.endswith(CONSTANTS.OKX_ORDER_CANCEL_PATH):
            self.cancels.append((wire, time.perf_counter()))
            body = {"code": "0", "data": [{"sCode": "0", "ordId": wire.get("ordId", ""),
                                           "clOrdId": wire.get("clOrdId", "")}]}
        else:
            raise AssertionError("unexpected offline request route")

        async def response_json():
            return body

        return SimpleNamespace(status=200, content_type="application/json", json=response_json)


class AssemblyController:
    def __init__(self, app):
        self.app = app
        self.config = SimpleNamespace(id="life", recovery_account_uid="12345")
        self.commands, self.tasks = {}, {}
        self.stopped = True
        self.admitted = set()
        self.executors_update_event = asyncio.Event()

    @staticmethod
    def logger():
        return logging.getLogger(__name__)

    def allow_create_executor_actions(self):
        return not self.stopped and len(self.admitted) < 2 and sum(not task.done() for task in self.tasks.values()) < 2

    def authorize_runner_create_action(self, action):
        config = action.executor_config
        if not self.allow_create_executor_actions() or config.id in self.admitted or config.id in self.tasks:
            return False
        command = self.commands.get(config.id)
        if (command is None or config.controller_id != "life" or config.connector_name != "okx"
                or config.trading_pair != "LIFE-USDT" or config.execution_strategy != ExecutionStrategy.LIMIT_MAKER
                or config.side.name != command.side or config.amount != command.amount or config.price != command.price):
            return False
        self.fence(command)
        self.admitted.add(config.id)
        return True

    def suppress_create_for_stop_batch(self):
        return True

    def fence(self, command):
        if (self.stopped or self.commands.get(command.intent) != command
                or datetime.now(timezone.utc) >= command.expires_at):
            raise PermissionError("LOCAL_AUTHORITY_REVOKED")

    def submit_executor_spot_order(self, config, *, amount, price, order_type):
        command = self.commands[config.id]
        self.fence(command)
        if (self.stopped or config.id not in self.admitted or config.id in self.tasks
                or config.controller_id != "life" or config.connector_name != "okx"
                or config.trading_pair != "LIFE-USDT" or config.execution_strategy != ExecutionStrategy.LIMIT_MAKER
                or config.side.name != command.side or amount != command.amount or price != command.price
                or order_type != OrderType.LIMIT_MAKER):
            raise PermissionError("ASSEMBLY_DISPATCH_DENIED")
        task = asyncio.create_task(self.send(command))
        self.tasks[config.id] = task

        def finished(done):
            self.admitted.discard(config.id)
            if not done.cancelled():
                done.exception()

        task.add_done_callback(finished)
        return command.wire

    async def send(self, command):
        store, app = self.app.store, self.app
        if not await store.reserve(command):
            raise PermissionError("RECONCILE_BEFORE_RETRY")
        self.fence(command)
        await store.project(command)
        self.fence(command)
        await store.authorize(command, arm=True)
        ack = []

        def check_wire(wire):
            self.fence(command)
            expected = dict(clOrdId=command.wire, instId="LIFE-USDT", tdMode="cash", ordType="post_only",
                            side=command.side.lower(), px=str(command.price), sz=str(command.amount))
            if set(wire) != set(expected) or any(wire[key] != expected[key] for key in expected if key not in ("px", "sz")):
                raise PermissionError("WIRE_CHANGED")
            if Decimal(wire["px"]) != command.price or Decimal(wire["sz"]) != command.amount:
                raise PermissionError("WIRE_CHANGED")

        async def authorize(wire):
            app.db_gate_entered.set()
            await app.db_gate_release.wait()
            await store.verify_projection(command)
            await store.authorize(command)
            check_wire(wire)

        app.connector.submit_protected_order(order_id=command.wire, trading_pair="LIFE-USDT", amount=command.amount,
                                             trade_type=TradeType[command.side], order_type=OrderType.LIMIT_MAKER,
                                             price=command.price, pre_send_check=check_wire,
                                             async_pre_send_check=authorize, on_ack=lambda value: ack.append(value))
        await app.transport_tasks[-1]
        if len(ack) != 1:
            raise OSError("PROTECTED_ORDER_ACK_UNAVAILABLE")
        await store.acknowledge(command, ack[0])
        return ack[0]

    def stop(self):
        self.stopped = True
        for task in self.tasks.values():
            if not task.done():
                task.cancel()
        for task in self.app.transport_tasks:
            if not task.done():
                task.cancel()

    def on_runner_stop_action(self, action):
        self.stop()

    async def cancel_owned(self):
        for row in await self.app.store.poll():
            if row["state"] != "ACKED" or row["intent"] not in self.commands:
                continue
            command = self.commands[row["intent"]]
            await self.app.store.verify_projection(command)
            await self.app.connector.cancel_by_exchange_order_id("LIFE-USDT", row["exchange_id"])


class RunnerAssembly:
    def __init__(self, db, recorder, connector=None, *, cold_restore=False):
        self.db, self.recorder = db, recorder
        self.cold_restore = cold_restore
        self.http = SyntheticHttp()
        self.connector = connector or OkxExchange("offline-spike-key", "offline-spike-secret", "offline-passphrase",
                                                  trading_pairs=["LIFE-USDT"])
        self.connector.enable_protected_trading_pair("LIFE-USDT")
        self.connector._set_trading_pair_symbol_map(bidict({"LIFE-USDT": "LIFE-USDT"}))
        self.connector._trading_rules["LIFE-USDT"] = TradingRule("LIFE-USDT", min_order_size=Decimal("0.00000001"),
                                                                 min_price_increment=Decimal("0.00000001"), min_base_amount_increment=Decimal("0.00000001"))
        self.connector._account_balances = {"LIFE": Decimal("1000"), "USDT": Decimal("1000")}
        self.connector._account_available_balances = dict(self.connector._account_balances)
        self.api = RESTAssistant(RESTConnection(self.http), AsyncThrottler(CONSTANTS.RATE_LIMITS),
                                 auth=self.connector.authenticator)

        async def get_rest_assistant():
            return self.api

        self.connector._web_assistants_factory = SimpleNamespace(get_rest_assistant=get_rest_assistant)
        self.controller = AssemblyController(self)
        self.store = AssemblyAuthority(db.pool, self)
        self.db_gate_entered, self.db_gate_release = asyncio.Event(), asyncio.Event()
        self.recorder_entered, self.recorder_release = asyncio.Event(), asyncio.Event()
        self.db_gate_release.set()
        self.recorder_release.set()
        self.loops, self.transport_tasks, self.patchers = [], [], []
        self.runner = None

    def block_database_gate(self):
        self.db_gate_release.clear()

    def release_database_gate(self):
        self.db_gate_release.set()

    async def __aenter__(self):
        await self.db.admin.execute(Path(__file__).with_name("assembly_schema.sql").read_text())
        await self.db.admin.execute("GRANT SELECT,INSERT ON recorder_operations TO life_spike_runtime")
        await self.db.admin.execute("GRANT UPDATE (state) ON recorder_operations TO life_spike_runtime")
        self.controller.stopped = self.cold_restore

        def spawn(tasks):
            def create(coroutine, *args, **kwargs):
                task = asyncio.create_task(coroutine)
                tasks.append(task)
                return task
            return create

        self.patchers = [patch.object(MarketsRecorder, "_shared_instance", self.recorder),
                         patch("hummingbot.strategy_v2.runnable_base.safe_ensure_future", spawn(self.loops)),
                         patch("hummingbot.connector.exchange.okx.okx_exchange.safe_ensure_future", spawn(self.transport_tasks)),
                         patch.object(OrderExecutor, "get_order_price", lambda child: child.config.price)]
        for patcher in self.patchers:
            patcher.start()
        self.runner = StrategyV2Base({"okx": self.connector}, config=None)
        self.runner.controllers = {"life": self.controller}
        self.runner.executor_orchestrator._initialize_cached_performance()
        self.runner.actions_queue = asyncio.Queue(maxsize=2)
        self.runner.market_data_provider = SimpleNamespace(ready=True, stop=lambda: None)
        self.runner.ready_to_trade = True
        self.runner.update_controllers_configs = lambda: None
        self.runner.determine_executor_actions = lambda: []
        return self

    def child(self, intent):
        return next(child for child in self.runner.executor_orchestrator.active_executors["life"] if child.config.id == intent)

    async def enqueue(self, intent, *, route="queue", side="BUY"):
        if not self.controller.allow_create_executor_actions() or len(self.controller.commands) >= 32:
            raise PermissionError("ASSEMBLY_CAPACITY_OR_STOP")
        if intent in self.controller.commands:
            raise ValueError("IDENTITY_ALREADY_ISSUED")
        if self.runner.actions_queue.full():
            raise asyncio.QueueFull
        command = Command(intent, "wire-" + intent, datetime.now(timezone.utc) + timedelta(seconds=30), side=side)
        self.controller.commands[intent] = command
        config = OrderExecutorConfig(id=intent, controller_id="life", timestamp=1, connector_name="okx",
                                     trading_pair="LIFE-USDT", side=TradeType[side], amount=command.amount,
                                     price=command.price, execution_strategy=ExecutionStrategy.LIMIT_MAKER)
        action = CreateExecutorAction(controller_id="life", executor_config=config)
        if route == "queue":
            self.runner.actions_queue.put_nowait([action])
        else:
            self.runner.determine_executor_actions = lambda: [action]
            self.runner.tick(1)
            self.runner.determine_executor_actions = lambda: []
        return command

    async def complete(self, intent):
        async def wait():
            while intent not in self.controller.tasks:
                await asyncio.sleep(.001)
            return await self.controller.tasks[intent]
        return await asyncio.wait_for(wait(), 3)

    def corrupt_recorder(self, intent, fault):
        with self.recorder.sql_manager.get_new_session() as session:
            row = session.query(Executors).filter(Executors.id == intent).one()
            if fault == "recorder_missing":
                session.delete(row)
            else:
                row.custom_info = {**row.custom_info, "recovery_order_ids": ["foreign-wire"]}
            session.commit()

    async def __aexit__(self, *args):
        self.controller.stop()
        tasks = [*self.controller.tasks.values(), *self.transport_tasks, *self.loops]
        if self.runner is not None:
            tasks.append(self.runner.listen_to_executor_actions_task)
            for children in self.runner.executor_orchestrator.active_executors.values():
                for child in children:
                    child.stop()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for patcher in reversed(self.patchers):
            patcher.stop()
