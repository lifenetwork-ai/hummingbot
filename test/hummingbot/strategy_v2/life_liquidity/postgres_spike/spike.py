"""Synthetic PostgreSQL experiment, deliberately absent from production imports."""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_CEILING, Decimal

from hummingbot.core.data_type.common import OrderType
from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest
from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection


@dataclass(frozen=True)
class Command:
    intent: str
    wire: str
    expires_at: datetime
    side: str = "BUY"
    amount: Decimal = Decimal("1")
    price: Decimal = Decimal("1")
    account: str = "synthetic-life"
    owner: str = "process-a"
    lineage: str = "synthetic-lineage"
    epoch: int = 1
    config_version: int = 1
    risk_epoch: int = 1
    session: str = "synthetic-session"


class PgAuthority:
    def __init__(self, pool):
        self.pool = pool
        self.queries = 0
        self.rows = 0

    @asynccontextmanager
    async def connection(self):
        async with self.pool.acquire(timeout=.3) as connection:
            yield connection

    async def fetchrow(self, connection, query, *args):
        self.queries += 1
        row = await connection.fetchrow(query, *args)
        self.rows += int(row is not None)
        return row

    async def execute(self, connection, query, *args):
        self.queries += 1
        return await connection.execute(query, *args)

    async def account(self, connection, account):
        row = await self.fetchrow(connection, "SELECT * FROM account_state WHERE account=$1 FOR UPDATE", account)
        if row is None:
            raise PermissionError("ACCOUNT_MISSING")
        return row

    @staticmethod
    def validate(command):
        if (type(command) is not Command or command.side not in ("BUY", "SELL")
                or command.expires_at.tzinfo is None
                or any(type(x) is not Decimal or not x.is_finite() or x <= 0
                       or x >= Decimal("1e20") or x.as_tuple().exponent < -8
                       for x in (command.amount, command.price))):
            raise ValueError("COMMAND_INVALID")

    @staticmethod
    def matches(row, command):
        keys = ("account", "intent", "wire", "side", "amount", "price", "owner", "lineage", "epoch",
                "config_version", "risk_epoch", "session", "expires_at")
        return row is not None and all(row[key] == getattr(command, key) for key in keys)

    async def eligibility(self, connection, row, command):
        # DB time is read AFTER any account-lock wait, rather than transaction start time.
        now = (await self.fetchrow(connection, "SELECT clock_timestamp() AS now"))["now"]
        if (row["environment"] != "OFFLINE_SYNTHETIC_ONLY" or not row["armed"]
                or any(row[key] != getattr(command, key) for key in
                       ("owner", "lineage", "epoch", "config_version", "risk_epoch", "session"))
                or now >= min(command.expires_at, row["expires_at"])
                or not 0 <= (now - row["observed_at"]).total_seconds() <= 30):
            raise PermissionError("AUTHORITY_REVOKED_OR_STALE")

    async def attempt(self, connection, command):
        return await self.fetchrow(connection, "SELECT * FROM attempts WHERE account=$1 AND intent=$2",
                                   command.account, command.intent)

    async def reserve(self, command):
        self.validate(command)
        async with self.connection() as connection, connection.transaction():
            row = await self.account(connection, command.account)
            await self.eligibility(connection, row, command)
            old = await self.attempt(connection, command)
            if old is not None:
                if not self.matches(old, command):
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return False  # Duplicate reservation is never resend permission.
            cash = ((command.amount * command.price * Decimal("1.001")).quantize(
                Decimal("0.00000001"), rounding=ROUND_CEILING) if command.side == "BUY" else Decimal("0"))
            base = command.amount if command.side == "SELL" else Decimal("0")
            budget = Decimal("0.01")  # Explicit synthetic service spending hold.
            if (row["cash"] - row["reserved_cash"] < cash or row["base"] - row["reserved_base"] < base
                    or row["budget_spent"] + row["budget_reserved"] + budget > row["budget_limit"]
                    or row["active_count"] >= 32):
                raise PermissionError("INSUFFICIENT_CAPITAL_OR_BUDGET")
            await self.execute(connection, """INSERT INTO attempts
                (account,intent,wire,side,amount,price,hold_cash,hold_base,hold_budget,state,
                 owner,lineage,epoch,config_version,risk_epoch,session,expires_at)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'PREPARED',$10,$11,$12,$13,$14,$15,$16)""",
                               command.account, command.intent, command.wire, command.side, command.amount,
                               command.price, cash, base, budget, command.owner, command.lineage, command.epoch,
                               command.config_version, command.risk_epoch, command.session, command.expires_at)
            await self.execute(connection, """UPDATE account_state SET reserved_cash=reserved_cash+$2,
                reserved_base=reserved_base+$3,budget_reserved=budget_reserved+$4,
                active_count=active_count+1,version=version+1 WHERE account=$1""",
                               command.account, cash, base, budget)
            await self.execute(connection, "INSERT INTO events(account,event_id,intent,kind) VALUES ($1,$2,$3,'RESERVE')",
                               command.account, "reserve:" + command.intent, command.intent)
            await self.on_reserved(connection, command)
        return True

    async def on_reserved(self, connection, command):
        """Test-only extension point for a projection operation in the same UoW."""

    async def authorize(self, command, *, arm=False):
        self.validate(command)
        async with self.connection() as connection, connection.transaction():
            row = await self.account(connection, command.account)
            await self.eligibility(connection, row, command)
            attempt = await self.attempt(connection, command)
            if not self.matches(attempt, command) or attempt["state"] != ("PREPARED" if arm else "SEND_UNKNOWN"):
                raise PermissionError("ATTEMPT_NOT_SENDABLE")
            if (row["reserved_cash"] < attempt["hold_cash"] or row["reserved_base"] < attempt["hold_base"]
                    or row["budget_reserved"] < attempt["hold_budget"]):
                raise PermissionError("HOLD_MISSING")
            if arm:
                await self.execute(connection, "UPDATE attempts SET state='SEND_UNKNOWN' WHERE account=$1 AND intent=$2",
                                   command.account, command.intent)
                await self.execute(connection, "INSERT INTO events(account,event_id,intent,kind) VALUES ($1,$2,$3,'ARM')",
                                   command.account, "arm:" + command.intent, command.intent)
        return True

    async def poll(self):
        async with self.connection() as connection:
            self.queries += 1
            rows = await connection.fetch("""SELECT intent,wire,state,exchange_id FROM attempts
                WHERE account='synthetic-life' AND state IN ('PREPARED','SEND_UNKNOWN','ACKED') ORDER BY intent LIMIT 33""")
            self.rows += len(rows)
            if len(rows) > 32:
                raise PermissionError("ACTIVE_CAPACITY")
            return tuple(dict(row) for row in rows)

    async def acknowledge(self, command, exchange_id):
        if not exchange_id:
            raise ValueError("EXCHANGE_ID_REQUIRED")
        async with self.connection() as connection, connection.transaction():
            await self.account(connection, command.account)
            row = await self.attempt(connection, command)
            if not self.matches(row, command) or row["state"] not in ("SEND_UNKNOWN", "ACKED"):
                raise PermissionError("ACK_CONFLICT")
            if row["exchange_id"] is not None:
                if row["exchange_id"] != exchange_id:
                    raise ValueError("ACK_CONFLICT")
                return False
            await self.execute(connection, """UPDATE attempts SET state='ACKED',exchange_id=$3
                WHERE account=$1 AND intent=$2""", command.account, command.intent, exchange_id)
            await self.execute(connection, "INSERT INTO events(account,event_id,intent,kind) VALUES ($1,$2,$3,'ACK')",
                               command.account, "ack:" + command.intent, command.intent)
        return True

    async def settle_zero_fill(self, command, exchange_id):
        # Only the fake exchange's qualified terminal-zero-fill replay calls this.
        # It is not a general OKX reconciliation/release implementation.
        async with self.connection() as connection, connection.transaction():
            await self.account(connection, command.account)
            row = await self.attempt(connection, command)
            if (not self.matches(row, command) or row["state"] not in ("ACKED", "TERMINAL")
                    or row["exchange_id"] != exchange_id):
                raise PermissionError("TERMINAL_PROOF_MISMATCH")
            if row["state"] == "TERMINAL":
                return False
            await self.execute(connection, """UPDATE account_state SET reserved_cash=reserved_cash-$2,
                reserved_base=reserved_base-$3,budget_reserved=budget_reserved-$4,
                active_count=active_count-1,version=version+1 WHERE account=$1""",
                               command.account, row["hold_cash"], row["hold_base"], row["hold_budget"])
            await self.execute(connection, "UPDATE attempts SET state='TERMINAL' WHERE account=$1 AND intent=$2",
                               command.account, command.intent)
            await self.execute(connection, "INSERT INTO events(account,event_id,intent,kind) VALUES ($1,$2,$3,'TERMINAL')",
                               command.account, "terminal:" + command.intent, command.intent)
        return True


class SpikeDispatcher:
    """Bounded synthetic adapter for the existing synchronous OrderExecutor hook.

    A returned wire ID is an enqueue identity, never a trading permit or ACK.
    The fake transport uses real RESTConnection with only an in-memory session.
    """

    def __init__(self, store, command, session):
        self.store, self.command, self.session = store, command, session
        self.pending = None
        self.stopped = False
        self.throttle = asyncio.Event()
        self.throttle.set()
        self.armed = asyncio.Event()

    @staticmethod
    def logger():
        return logging.getLogger(__name__)

    def allow_create_executor_actions(self):
        # Cheap scheduling gate. Actual authority is awaited in _send().
        return not self.stopped and self.pending is None

    def submit_executor_spot_order(self, config, *, amount, price, order_type):
        c = self.command
        if (not self.allow_create_executor_actions() or config.id != c.intent or config.controller_id != "life"
                or config.connector_name != "okx" or config.trading_pair != "LIFE-USDT"
                or config.side.name != c.side or amount != c.amount or price != c.price
                or order_type != OrderType.LIMIT_MAKER):
            raise PermissionError("DISPATCH_DENIED_OR_BUSY")
        self.pending = asyncio.create_task(self._send())
        return c.wire

    def final_local_check(self):
        if self.stopped or datetime.now(timezone.utc) >= self.command.expires_at:
            raise PermissionError("STOPPED_OR_EXPIRED")

    async def _send(self):
        c = self.command
        self.final_local_check()
        if not await self.store.reserve(c):
            raise PermissionError("RECONCILE_BEFORE_RETRY")
        self.final_local_check()
        await self.store.authorize(c, arm=True)
        self.armed.set()
        await asyncio.wait_for(self.throttle.wait(), 1)
        self.final_local_check()
        await self.store.authorize(c)  # Fresh primary check after throttle wait.
        self.final_local_check()
        wire = dict(clOrdId=c.wire, instId="LIFE-USDT", tdMode="cash", ordType="post_only",
                    side=c.side.lower(), px=str(c.price), sz=str(c.amount))
        connection = RESTConnection(self.session)
        response = await connection.call(RESTRequest(method=RESTMethod.POST,
                                                     url="https://synthetic.invalid/api/v5/trade/order",
                                                     data=json.dumps(wire)), pre_send_check=self.final_local_check)
        ack = await response.json()
        await self.store.acknowledge(c, ack["ordId"])
        return ack

    async def stop(self):
        self.stopped = True
        if self.pending is not None:
            self.pending.cancel()
            await asyncio.gather(self.pending, return_exceptions=True)
        # A stop/timeout never refunds an armed/unknown hold.

    async def cancel_owned(self):
        c = self.command
        async with self.store.connection() as connection:
            row = await self.store.attempt(connection, c)
            if row is None or not self.store.matches(row, c) or row["state"] != "ACKED" or not row["exchange_id"]:
                raise PermissionError("CANCEL_IDENTITY_UNQUALIFIED")
        return await self.session.cancel(c.wire, row["exchange_id"])
