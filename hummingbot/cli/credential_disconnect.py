"""Sequential Disconnect coordination over existing owner, receipt and trading APIs."""
import asyncio
import logging
from decimal import Decimal

from sqlalchemy.exc import SQLAlchemyError

from hummingbot.connector.credential_adapters import CredentialAdapterError
from hummingbot.core.control_errors import ControlConflict


class DisconnectCoordinator:
    TRANSIENT = {"kms_unavailable", "exchange_unavailable", "database_unavailable", "owner_unavailable",
                 "cleanup_ledger_pending", "cleanup_orders_pending", "cleanup_runtime_unavailable",
                 "cleanup_metadata_unavailable", "cleanup_price_unavailable"}

    def __init__(self, store, owner, runtime):
        self.store, self.owner, self.runtime = store, owner, runtime

    async def call(self, function, *args, **kwargs):
        # One coordinator per owner/operation. The service starts at most one;
        # no HTTP request creates an independent cleanup task or engine.
        return await asyncio.to_thread(function, *args, **kwargs)

    async def snapshot(self, identity, *, required=False):
        snapshot = await self.call(self.store.disconnect_snapshot, identity, self.owner.owner_uuid, self.owner.generation)
        if required and snapshot is None:
            raise ControlConflict("Cleanup ownership or operation changed", code="owner_unavailable")
        return snapshot

    async def progress(self, identity, stage, **kwargs):
        if not await self.call(self.store.progress_disconnect, identity, self.owner.owner_uuid, self.owner.generation, stage=stage, **kwargs):
            raise ControlConflict("Cleanup ownership changed", code="owner_unavailable")

    @staticmethod
    def expected(positions):
        result = {}
        for row in positions:
            amount = Decimal(row["amount"])
            if row["side"] not in ("BUY", "SELL") or not amount.is_finite() or amount <= 0:
                raise CredentialAdapterError("cleanup_position_invalid", status=409)
            key = row["trading_pair"], "LONG" if row["side"] == "BUY" else "SHORT"
            result[key] = result.get(key, Decimal(0)) + amount
        return result

    def proof(self, snapshot, **values):
        return dict(observed_at=float(snapshot["state"]["database_now"]), owner_generation=self.owner.generation,
                    remaining_order_count=len(snapshot["orders"]), remaining_position_count=len(snapshot["positions"]), **values)

    async def run(self, identity):
        try:
            snapshot = await self.snapshot(identity)
            if snapshot is None:
                return False
            operation = snapshot["operation"]
            await self.progress(identity, "checking_account")
            await self.runtime.prepare(operation)
            await self.progress(identity, "cancelling_orders")
            await self.runtime.cancel_orders()
            snapshot = await self.snapshot(identity, required=True)
            actual = await self.runtime.positions()
            if actual != self.expected(snapshot["positions"]):
                raise CredentialAdapterError("cleanup_position_mismatch", status=409)
            proof = self.proof(snapshot, orders_terminal_verified=True, account_verified=True, position_checkpoints_verified=True)
            await self.progress(identity, "closing_positions", evidence=proof)
            # One row at a time keeps controller attribution and long/short
            # checkpoints separate; terminal fills reduce each row atomically.
            for position in snapshot["positions"]:
                await self.runtime.close_position(position, operation)
                current = await self.snapshot(identity, required=True)
                if await self.runtime.positions() != self.expected(current["positions"]):
                    raise CredentialAdapterError("cleanup_ledger_pending", status=503)
            await self.progress(identity, "verifying")
            await self.runtime.cancel_orders()
            snapshot = await self.snapshot(identity, required=True)
            if snapshot["positions"] or snapshot["orders"] or await self.runtime.positions():
                raise CredentialAdapterError("cleanup_ledger_pending", status=503)
            await self.runtime.shutdown()
            await self.runtime.detach(operation["connector"])
            snapshot = await self.snapshot(identity, required=True)
            proof = self.proof(snapshot, orders_terminal_verified=True, positions_flat_verified=True,
                               ledger_confirmed=True, consumers_detached=True)
            await self.progress(identity, "detaching", evidence=proof)
            if not await self.call(self.store.finish_disconnect, identity, self.owner.owner_uuid, self.owner.generation, evidence=proof):
                raise CredentialAdapterError("cleanup_verification_changed", status=409)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as error:
            code = "database_unavailable" if isinstance(error, SQLAlchemyError) else getattr(error, "code", "cleanup_failed")
            transient = code in self.TRANSIENT
            logging.getLogger(__name__).log(logging.ERROR if code == "cleanup_failed" else logging.WARNING,
                                            "Disconnect incomplete code=%s operation_id=%s error_type=%s", code, identity, type(error).__name__,
                                            extra={"event": "credential_cleanup_incomplete", "reason_code": code,
                                                   "operation_id": identity, "error_type": type(error).__name__})
            try:
                snapshot = await self.snapshot(identity)
                if snapshot is not None:
                    await self.progress(identity, "waiting_for_dependency" if transient else "action_required", reason_code=code,
                                        operator_action_required=not transient,
                                        next_retry_at=float(snapshot["state"]["database_now"]) + 5 if transient else None)
            except Exception:
                logging.getLogger(__name__).error("Disconnect progress persistence unavailable operation_id=%s", identity,
                                                  extra={"event": "credential_cleanup_publication_failed", "operation_id": identity})
            return False
        finally:
            await self.runtime.shutdown()
