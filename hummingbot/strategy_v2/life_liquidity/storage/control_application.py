"""Configured signed control/import entry points with independently collected cuts."""
import asyncio
import inspect
from dataclasses import replace
from uuid import uuid4

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerMode, StorageError, fingerprint, utc
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import ControlSnapshot, OwnerTransition


class FileControlApplication:
    def __init__(self, service):
        self.service, self.store = service, service.store
        self._snapshot = None
        self._lock = asyncio.Lock()
        self._admitted = 0
        self.observer_id = uuid4()

    async def snapshot(self):
        s = self.service
        if s._revoked or self._admitted >= s.config.max_pending:
            raise StorageError('SERVICE_CONTROL_REVOKED_OR_BACKPRESSURE')
        self._admitted += 1
        s.runtime.invalidate_actions()
        s._control_pending = True  # Revoke cached action/final-send eligibility before any await.
        try:
            async with self._lock, asyncio.timeout(s.config.recovery_policy.max_fact_age_ms / 1000):
                s._control_pending = True  # A preceding command may have cleared its own barrier while we queued.
                s.runtime.invalidate_actions()
                s.controller._runner_halt_ok = s.controller._halt_runner_orders()
                if not s.controller._runner_halt_ok:
                    raise StorageError('SERVICE_CONTROL_RUNNER_UNQUALIFIED')
                await s.drain_action_events()
                await s.drain_runner_events()
                await s.reporting.drain()
                if s.joint is None:
                    if s.spot._observer is None:
                        raise StorageError('SERVICE_CONTROL_SOURCE_REQUIRED')
                    self._snapshot = await s.spot._observer.control_snapshot()
                else:
                    if s.sources.joint_spot_recovery is None:
                        raise StorageError('SERVICE_CONTROL_SOURCE_REQUIRED')
                    await s.recover()
                    values = s.sources.joint_spot_recovery()
                    if inspect.isawaitable(values):
                        values = await values
                    _, facts = values
                    self._snapshot = await self.store._call(self._joint_snapshot, facts)
                return self._snapshot
        except BaseException:
            s.revoke()
            raise
        finally:
            self._admitted -= 1

    def _joint_snapshot(self, facts):
        s = self.service
        self.store.assert_worker_thread()
        s.joint._guard()
        recovery = s.joint._recovery
        receipt = recovery._fresh()
        view = self.store._snapshot()
        meta = self.store._read()
        if (facts is None or facts.binding != self.store.binding
                or int(facts.observed_at.timestamp() * 1000) != receipt['bundle']['observed_at_ms']
                or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0
                or (facts.account_base_life, facts.account_quote_usdt) != (
                    view.balance.total_base_life, view.balance.total_quote_usdt)
                or not 0 <= (utc(self.store._clock()) - facts.observed_at).total_seconds() * 1000 <= s.config.recovery_policy.max_fact_age_ms):
            raise StorageError('SERVICE_CONTROL_JOINT_FACTS_UNQUALIFIED')
        self.store._verify_recovery_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                               meta.resources.get('cashflows', {}), facts)
        quiescent = s.controller._runner_executor_scope_complete(quiescent=True, verify_financial_events=False)
        facts = replace(facts, runner_scope_complete=facts.runner_scope_complete and quiescent is True)
        return ControlSnapshot(self.store.binding, view.cut, facts)

    async def apply(self, command, *, approval, snapshot):
        s = self.service
        # Historical exact readback is handled by the signed repository and
        # never restores any volatile admission/rearm state.
        if self._admitted >= s.config.max_pending:
            raise StorageError('SERVICE_CONTROL_BACKPRESSURE')
        self._admitted += 1
        try:
            async with self._lock, asyncio.timeout(s.config.recovery_policy.max_fact_age_ms / 1000):
                replay = await self.store.lookup_operation('control:' + command.operation_id, self.store.binding)
                if replay is None:
                    if (snapshot != self._snapshot or snapshot is None or s._revoked or not s._control_pending
                            or not 0 <= (utc(self.store._clock()) - snapshot.facts.observed_at).total_seconds() * 1000
                            <= s.config.recovery_policy.max_fact_age_ms):
                        raise StorageError('SERVICE_CONTROL_SOURCE_UNQUALIFIED')
                    for connector in s.connectors.values():
                        if await connector.get_account_uid() != self.store.binding.account_uid:
                            raise StorageError('SERVICE_CONTROL_UID_MISMATCH')
                from hummingbot.strategy_v2.life_liquidity.storage.control_plane import ApproveSpotCashflow
                from hummingbot.strategy_v2.life_liquidity.storage.import_commands import (
                    ImportApprovedCashflows,
                    ImportPreparationOrigin,
                )
                from hummingbot.strategy_v2.life_liquidity.storage.inventory_application import InventoryModeCommand
                from hummingbot.strategy_v2.life_liquidity.storage.session_observation import (
                    AuthorizeSessionObservation,
                )
                if type(command) is AuthorizeSessionObservation:
                    if replay is None and command.observer_id != self.observer_id:
                        raise StorageError('SESSION_OBSERVATION_PROCESS_MISMATCH')
                    receipt = await self.store._call(self._authorize_observation, command, approval, snapshot)
                elif type(command) is InventoryModeCommand:
                    if s.inventory is None:
                        raise StorageError('SERVICE_INVENTORY_ROUTE_DISABLED')
                    receipt = await self.store._call(s.inventory.apply_mode, command, approval, snapshot)
                elif type(command) is OwnerTransition:
                    # Revoke locally before the signed durable owner change waits.
                    if replay is None:
                        if command.mode is OwnerMode.ACTIVE and (command.token.process_id != s.spot._process_id
                                                                 or s.risk is not None and s.risk.gate.state == 'HALTED'):
                            raise StorageError('SERVICE_ACTIVATION_PROCESS_OR_HALT_INVALID')
                        if command.mode is OwnerMode.ACTIVE:
                            if s.configuration is not None:
                                await self.store._call(s.configuration.verify_activation)
                            if s.spot._pending or s.swap is not None and (s.swap._pending or s.swap._proposals):
                                raise StorageError('SERVICE_ACTIVATION_INFLIGHT_REQUESTS')
                            if s.quotes is not None and await self.store._call(s.quotes.native.action_journal.active_records):
                                raise StorageError('SERVICE_ACTIVATION_PENDING_PROPOSALS')
                        s.runtime.revoke()
                    receipt = await self.store.transition_owner(command, approval=approval, facts=snapshot.facts, cut=snapshot.cut)
                    if not receipt.replayed and command.mode is OwnerMode.ACTIVE:
                        await self.store._call(self._activate, receipt, approval.operator_id)
                elif type(command) is ApproveSpotCashflow:
                    receipt = await self.store.approve_cashflow(command, approval=approval, facts=snapshot.facts, cut=snapshot.cut)
                elif type(command) in (ImportApprovedCashflows, ImportPreparationOrigin):
                    receipt = await self.store.import_legacy(command, approval=approval, facts=snapshot.facts, cut=snapshot.cut)
                else:
                    raise StorageError('SERVICE_CONTROL_COMMAND_UNSUPPORTED')
                if not receipt.replayed:
                    self._snapshot = None
                    s._control_pending = False
                return receipt
        except BaseException:
            # Invalid signature/cut must not leave new-risk admission open.
            s.runtime.revoke()
            raise
        finally:
            self._admitted -= 1

    def _authorize_observation(self, command, approval, snapshot):
        s = self.service
        self.store.assert_worker_thread()
        prior = self.store._read().resources.get('control_operations', {}).get(command.operation_id)
        if prior is None:
            if s._revoked or s.controller._order_safety_stopped:
                raise StorageError('SESSION_OBSERVATION_STOPPED')
            if s.quotes is not None and s.quotes.native.action_journal.active_records():
                raise StorageError('SESSION_OBSERVATION_PROPOSALS_UNRESOLVED')
            if s.joint is not None:
                s.joint._guard(observation=True)
                if s.joint._recovery.capital.claim_ids() or any(
                        r.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND') for r in s.joint._recovery._records()):
                    raise StorageError('SESSION_OBSERVATION_JOINT_SCOPE_UNQUALIFIED')
        return self.store._authorize_session_observation(command, approval, snapshot.facts, snapshot.cut)

    async def advance_successor(self, grant_id):
        """Observe expiry/reference under a new signed process-scoped grant.

        Each changed financial/session cut is recollected independently. No
        original writer expiry, risk rearm flag or sender grant is extended.
        """
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import identifier
        identifier(grant_id)
        for _ in range(3):  # expiry, optional NAV publication, successor
            snapshot = await self.snapshot()
            if self._admitted >= self.service.config.max_pending:
                raise StorageError('SERVICE_CONTROL_BACKPRESSURE')
            self._admitted += 1
            try:
                async with self._lock, asyncio.timeout(self.service.config.recovery_policy.max_fact_age_ms / 1000):
                    if snapshot is not self._snapshot or self.service._revoked:
                        raise StorageError('SESSION_OBSERVATION_SOURCE_CHANGED')
                    self.service._control_pending = True
                    self.service.runtime.invalidate_actions()
                    result = await self.store._call(self._advance_successor, grant_id, snapshot)
                    self._snapshot = None
                    self.service._control_pending = False
                    if result:
                        return True
            except BaseException:
                self.service.runtime.revoke()
                raise
            finally:
                self._admitted -= 1
        return False

    def _advance_successor(self, grant_id, snapshot):
        from hummingbot.strategy_v2.life_liquidity.storage.session import SessionTick
        s = self.service
        view, meta = self.store._snapshot(), self.store._read()
        self.store._session_observation_guard(view, meta, s.spot.owner, grant_id)
        row = meta.resources['session_observation_grants'][grant_id]
        if (row['observer_id'] != str(self.observer_id) or s._revoked or s.controller._order_safety_stopped
                or view.cut != snapshot.cut):
            raise StorageError('SESSION_OBSERVATION_SOURCE_CHANGED')
        if view.session.state != 'TRANSITIONING':
            max_age_ms = s.config.joint.max_age_ms if s.joint is not None else s.spot._reconciliation_age_ms
            tick = SessionTick('observe-expiry:' + uuid4().hex, view.session.session_id, view.session.epoch,
                               False, False, False, max_age_ms,
                               observation_grant_id=grant_id)
            self.store._tick_session(tick, snapshot.cut, s.spot.owner)
            s.spot._sync_session()
            return False
        return s.spot._complete_successor(snapshot, observation_grant_id=grant_id)

    async def settle_inventory(self, cycle_id, intent_ids):
        """Release only the independently reconciled, physically flat cycle cost.

        This is accounting under the original owner, not an activation command.
        Retries retain the original command; they never restore admission.
        """
        from hummingbot.strategy_v2.life_liquidity.storage.settlement import SettleSpotInventory
        s = self.service
        if s.config.settlement_policy is None or s.config.controller.strategy.economics.objective != 'liquidity_service':
            raise StorageError('SERVICE_SETTLEMENT_ROUTE_DISABLED')
        # Validate before collection and use stable identity for all retries.
        command = SettleSpotInventory('validate', cycle_id, intent_ids)
        command = replace(command, operation_id='settlement:' + fingerprint({
            'cycle': command.cycle_id, 'members': command.intent_ids}))
        snapshot = await self.snapshot()
        if self._admitted >= s.config.max_pending:
            raise StorageError('SERVICE_CONTROL_BACKPRESSURE')
        self._admitted += 1
        try:
            async with self._lock, asyncio.timeout(s.config.recovery_policy.max_fact_age_ms / 1000):
                if (snapshot is None or snapshot != self._snapshot or s._revoked or not s._control_pending
                        or not 0 <= (utc(self.store._clock()) - snapshot.facts.observed_at).total_seconds() * 1000
                        <= s.config.settlement_policy.max_fact_age_ms):
                    raise StorageError('SERVICE_SETTLEMENT_SOURCE_UNQUALIFIED')
                for connector in s.connectors.values():
                    if await connector.get_account_uid() != self.store.binding.account_uid:
                        raise StorageError('SERVICE_CONTROL_UID_MISMATCH')
                prior = await self.store.lookup_inventory_settlement(command.operation_id, self.store.binding)
                method = (self.store.resume_inventory_settlement if prior is not None and prior.state == 'RECOVERY_REQUIRED'
                          else self.store.settle_inventory)
                receipt = await method(command, facts=snapshot.facts, cut=snapshot.cut, owner=s.spot.owner)
                self._snapshot = None
                s._control_pending = False
                return not receipt.replayed
        except BaseException:
            s.runtime.revoke()
            raise
        finally:
            self._admitted -= 1

    def _activate(self, receipt, operator):
        s = self.service
        self.store.assert_worker_thread()
        if (receipt.token.process_id != s.spot._process_id or s._revoked or s.controller._order_safety_stopped
                or receipt.mode is not OwnerMode.ACTIVE or receipt.replayed):
            raise StorageError('SERVICE_ACTIVATION_PROCESS_MISMATCH')
        s.spot.owner = s.runtime.owner = receipt.token
        if s.joint is not None:
            # The native joint rearm path separately proves fresh complete
            # claims/orders/carry/loss before restoring sender eligibility.
            s.joint.owner = receipt.token
            s.swap.owner = receipt.token
            s.swap._revoked = False
            if s.hedge is not None:
                s.hedge.owner = receipt.token
            s.joint._recovery._signed_rearm(operator, authority=s.joint)
        if s.risk is not None:
            # Never clear a HALT; a signed new owner alone is insufficient.
            if s.risk.gate.state == 'HALTED':
                raise StorageError('SERVICE_SAFETY_HALT_REMAINS')
            s.risk.gate._manual_rearm_required = False
            s.risk.gate.invalidate('STARTUP_REVALIDATION')
        s.spot._sync_session()
        if s.configuration is not None:
            s.configuration.refresh()
        s.spot._revoked = s.runtime._revoked = False
