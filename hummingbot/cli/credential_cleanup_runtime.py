"""Cleanup adapter over TradingCore/ConnectorManager/tracker/recorder; no strategy loop."""
import asyncio
import time
from decimal import Decimal

from hummingbot.connector.credential_adapters import CredentialAdapterError
from hummingbot.core.cleanup_authority import CleanupOrderScope, cleanup_order_scope
from hummingbot.core.data_type.common import OrderType, PositionAction, PositionMode
from hummingbot.core.data_type.in_flight_order import OrderState

CLEANUP_CONFIG = "credentials_cleanup"


class CleanupExchangeRuntime:
    def __init__(self, host, store):
        self.host, self.store, self.owner = host, store, host.owner
        self.core = host.hb.trading_core
        self.initialized = False
        self.orders = {}
        self.operation = None

    async def prepare(self, operation):
        self.operation = operation
        if not await self.host.stop_core():
            raise CredentialAdapterError("cleanup_runtime_stop_incomplete", status=409)
        await self.host.detach_credential_readers()
        snapshot = await asyncio.to_thread(self.store.disconnect_snapshot, operation["id"], self.owner.owner_uuid, self.owner.generation)
        if snapshot is None:
            raise CredentialAdapterError("owner_unavailable", status=503)
        recovery = snapshot["state"]["recovery_state"] or {}
        if ((recovery.get("runtime_active") or recovery.get("shutdown_incomplete")) and not operation.get("checkpoint_frozen")):
            raise CredentialAdapterError("cleanup_checkpoint_unavailable", status=409)

        def freeze(state):
            active = state.get("credential_operation") or {}
            if active.get("id") != operation["id"]:
                raise CredentialAdapterError("owner_unavailable", status=503)
            state["credential_operation"] = {**active, "checkpoint_frozen": True}
        self.owner._mutate_recovery(freeze, expected_version=snapshot["state"]["control_version"])
        versions = await self.host.managed_credentials.reload_cleanup(operation)
        pairs = {name: set() for name in versions}
        for row in snapshot["positions"]:
            if row["connector_name"] not in pairs:
                raise CredentialAdapterError("cleanup_perpetual_credentials_required", status=409)
            pairs[row["connector_name"]].add(row["trading_pair"])
        for row in snapshot["orders"]:
            if row["market"] not in pairs:
                raise CredentialAdapterError("cleanup_credentials_missing", status=409)
            pairs[row["market"]].add(row["symbol"])
        self.owner.allow_new_orders = False
        self.owner.config_file_path = CLEANUP_CONFIG
        self.owner.cleanup_operation_id = operation["id"]
        self.owner.cleanup_versions = versions.copy()
        self.owner.listing_bootstrap = self.owner.spot_outflow = None
        self.owner.volatility_checkpoints = None
        self.owner.mm_quote_check = None
        self.core.strategy_name = "credential_cleanup"
        self.core.strategy_file_name = CLEANUP_CONFIG
        await self.core.initialize_markets([(name, sorted(values)) for name, values in pairs.items()])
        self.core.markets_recorder.cleanup_operation_id = operation["id"]
        self.owner.connectors = self.core.connector_manager.connectors
        if not await self.core.start_clock():
            raise CredentialAdapterError("cleanup_runtime_unavailable", status=503)
        self.initialized = True
        deadline = time.monotonic() + 20
        while not all(connector.trading_pair_symbol_map_ready() and all(pair in connector.trading_rules for pair in pairs[name])
                      for name, connector in self.core.connector_manager.connectors.items()):
            if time.monotonic() >= deadline:
                raise CredentialAdapterError("cleanup_metadata_unavailable", status=503)
            self.owner.check_owner()
            await asyncio.sleep(.1)
        perpetual = self.core.connector_manager.connectors.get("okx_perpetual")
        if perpetual is not None:
            mode = await asyncio.wait_for(perpetual._fetch_account_position_mode(), 10)
            if mode != PositionMode.HEDGE:
                raise CredentialAdapterError("unsupported_cleanup_mode")
            perpetual._perpetual_trading.set_position_mode(mode)  # Local sync after read; no exchange setting mutation.
        # Restore after metadata/clock setup, preventing start_clock's generic
        # dangling-order cancellation from running before symbol discovery.
        known = {row["id"]: row for row in snapshot["orders"]}
        recovered = set()
        for state in snapshot["tracking_states"]:
            connector = self.core.connector_manager.connectors.get(state["market"])
            if connector is None:
                continue
            states = {identity: data for identity, data in state["saved_state"].items()
                      if identity in known and known[identity]["market"] == state["market"]}
            restored = self.restore_orders(connector, states, known)
            for identity, order in restored.items():
                if order is not None:
                    self.orders[identity] = connector, order
                    recovered.add(identity)
        if set(known) != recovered:
            raise CredentialAdapterError("cleanup_order_tracking_missing", status=409)

    @staticmethod
    def restore_orders(connector, states, known):
        restored = {}
        for identity, data in states.items():
            row = known[identity]
            if data.get("trading_pair") != row["symbol"] or Decimal(data["amount"]) != row["amount"]:
                raise CredentialAdapterError("cleanup_order_tracking_missing", status=409)
            # A failure snapshot after a lost ACK is not evidence of remote
            # absence. Restore it for GET/cancel reconciliation, never placement.
            restored[identity] = ({**data, "last_state": str(OrderState.PENDING_CREATE.value)}
                                  if row["creation_pending"] and data.get("last_state") == str(OrderState.FAILED.value) else data)
        connector.restore_tracking_states(restored)
        tracker = getattr(connector, "_order_tracker", None)
        available = tracker.all_fillable_orders if tracker is not None else connector.in_flight_orders
        return {identity: available.get(identity) for identity in states}

    async def cancel_orders(self):
        unknown = False
        for connector in self.core.connector_manager.connectors.values():
            remote = await self.owner._open_orders(connector)
            for item in remote:
                instrument = item.get("instId", "")
                market = "okx_perpetual" if instrument.endswith("-SWAP") else "okx"
                identity = item.get("clOrdId") or ""
                if market == connector.name and identity not in self.orders and identity.startswith(connector.client_order_id_prefix):
                    unknown = True
            await connector.cancel_all(5)
        deadline = time.monotonic() + 15
        while True:
            for connector, order in list(self.orders.values()):
                await asyncio.wait_for(self.owner._refresh_orders(connector, [order]), 10)
            if all(order.is_done for _, order in self.orders.values()):
                break
            if time.monotonic() >= deadline:
                raise CredentialAdapterError("cleanup_orders_pending", status=503)
            await asyncio.sleep(.2)
        for connector in self.core.connector_manager.connectors.values():
            for item in await self.owner._open_orders(connector):
                instrument, identity = item.get("instId", ""), item.get("clOrdId") or ""
                market = "okx_perpetual" if instrument.endswith("-SWAP") else "okx"
                if market == connector.name and (identity in self.orders or identity.startswith(connector.client_order_id_prefix)):
                    unknown = True
        self.core.trade_fill_db.assert_persistence_healthy()
        if unknown:
            raise CredentialAdapterError("cleanup_unknown_orders", status=409)

    async def positions(self):
        connectors = self.core.connector_manager.connectors
        connector = connectors.get("okx_perpetual") or connectors.get("okx")
        if connector is None:
            raise CredentialAdapterError("cleanup_credentials_missing", status=409)
        adapter = self.host.managed_credentials.source.adapters.get("okx", connector.name)
        return await adapter.cleanup_positions(connector)

    async def close_position(self, position, operation):
        connector = self.core.connector_manager.connectors.get("okx_perpetual")
        if connector is None:
            raise CredentialAdapterError("cleanup_perpetual_credentials_required", status=409)
        pair, amount = position["trading_pair"], Decimal(position["amount"])
        rule = connector.trading_rules.get(pair)
        quantized = connector.quantize_order_amount(trading_pair=pair, amount=amount)
        if not amount.is_finite() or amount <= 0 or rule is None or quantized != amount or amount < rule.min_order_size:
            raise CredentialAdapterError("cleanup_uncloseable_residual", status=409)
        try:
            # Reuse the native connector's registered ticker endpoint/bucket.
            # A made-up path has no rate limit and fails before transport.
            ticker = await asyncio.wait_for(connector._api_get(path_url="/api/v5/market/tickers", params={"instType": "SWAP"}), 10)
            rows = [row for row in ticker["data"] if row.get("instId") == pair + "-SWAP"]
            if str(ticker.get("code")) != "0" or len(rows) != 1:
                raise ValueError
            price = Decimal(rows[0]["last"])
            if not price.is_finite() or price <= 0 or price * amount < rule.min_notional_size:
                raise ValueError
        except Exception:
            raise CredentialAdapterError("cleanup_price_unavailable", status=503) from None
        row = self.owner.check_owner()
        scope = CleanupOrderScope(operation["id"], row["control_version"], self.owner.generation, position["id"], pair,
                                  "LONG" if position["side"] == "BUY" else "SHORT", amount, operation["affected_versions"]["okx_perpetual"],
                                  int(row["database_now"] * 1000) + 5000)
        with cleanup_order_scope(scope):
            place = connector.sell if position["side"] == "BUY" else connector.buy
            identity = place(trading_pair=pair, amount=amount, order_type=OrderType.MARKET, price=price, position_action=PositionAction.CLOSE)
        deadline = time.monotonic() + 20
        order = None
        while order is None:
            tracker = getattr(connector, "_order_tracker", None)
            order = tracker.fetch_order(client_order_id=identity) if tracker is not None else connector.in_flight_orders.get(identity)
            if time.monotonic() >= deadline:
                raise CredentialAdapterError("cleanup_orders_pending", status=503)
            await asyncio.sleep(.01)
        self.orders[identity] = connector, order
        await asyncio.wait_for(self.owner._refresh_orders(connector, [order]), 10)
        while not order.is_done:
            await asyncio.wait_for(self.owner._refresh_orders(connector, [order]), 10)
            if time.monotonic() >= deadline:
                raise CredentialAdapterError("cleanup_orders_pending", status=503)
            await asyncio.sleep(.1)
        self.core.trade_fill_db.assert_persistence_healthy()
        if order.executed_amount_base != amount:
            raise CredentialAdapterError("cleanup_orders_pending", status=503)

    async def shutdown(self):
        if not await self.host.stop_core():
            raise CredentialAdapterError("cleanup_runtime_stop_incomplete", status=409)
        self.core.before_position_checkpoint = self.core.after_order_cancellation = None
        self.orders.clear()
        self.owner.connectors = {}
        self.owner.listing_bootstrap = self.owner.spot_outflow = None
        self.owner.volatility_checkpoints = None
        self.owner.cleanup_operation_id = None
        self.owner.cleanup_versions = {}
        self.owner.cleanup_intents.clear()
        self.initialized = False

    async def detach(self, connector):
        await self.host.detach_credential_readers()
        self.host.managed_credentials.detach_selected(connector)
