"""Exact preparation/arming saga completion on the owning file worker only."""
import asyncio
from datetime import datetime
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    OwnerMode,
    OwnerToken,
    StorageConflict,
    StorageError,
    UnsupportedBackend,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut, PrepareReceipt, PrepareSpot
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


class LegacyPrepareRecoveryAdapter:
    def _prepare_recovery_guard(self, operation_id, command, facts, cut, owner, arm):
        if self._recovery_policy is None:
            raise UnsupportedBackend('SPOT_FINANCIAL_RECOVERY_POLICY_REQUIRED')
        view, meta = self._snapshot(), self._read()
        now = utc(self._clock())
        if (meta.ownership.token != owner or meta.ownership.mode not in (
                OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY) or now < owner.valid_from):
            raise StorageError('SPOT_PREPARE_RECOVERY_OWNER_INVALID')
        record = meta.resources['commands'].get(operation_id)
        if record is None:
            raise StorageError('SPOT_PREPARE_RECOVERY_NOT_STARTED')
        prepared = PrepareSpot.from_json(record['command'])
        if not arm and command != prepared:
            raise StorageConflict('SPOT_PREPARE_RECOVERY_COMMAND_CONFLICT')
        # Older partial records do not retain the original request cut/writer;
        # never guess them from the current snapshot or a recovery caller.
        if (record.get('original_owner') != owner.json() or 'original_cut' not in record
                or record['fingerprint'] != fingerprint({
                    'command': prepared.json(), 'cut': record['original_cut'], 'owner': owner.json()})):
            raise StorageError('SPOT_PREPARE_RECOVERY_ORIGIN_UNPROVEN')
        if arm and (record.get('arm_owner') != owner.json() or 'arm_cut' not in record
                    or record.get('arm_fingerprint') != fingerprint({
                        'operation': operation_id, 'cut': record['arm_cut'], 'owner': owner.json()})):
            raise StorageError('SPOT_ARM_RECOVERY_ORIGIN_UNPROVEN')
        completed = record['state'] == ('SEND_UNKNOWN' if arm else 'PREPARED')
        if completed:
            return view, meta, record, prepared, True
        if record['state'] != ('ARMING' if arm else 'PREPARING'):
            raise StorageError('SPOT_PREPARE_RECOVERY_PHASE_INVALID')
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        if (self._ancillary_recovery_pending(meta.resources)
                or any(key != operation_id and row['state'] in ('PREPARING', 'ARMING', 'ACKING', 'FILLING', 'TERMINATING')
                       for key, row in meta.resources['commands'].items())
                or any(row['state'] != 'APPLIED' for row in meta.resources.get('fill_operations', {}).values())
                or any(row['state'] != 'APPLIED' for row in meta.resources.get('cashflow_operations', {}).values())
                or any(row['state'] != 'ACKED' for row in meta.resources.get('acknowledgements', {}).values())
                or any(row['state'] != 'SETTLED' for row in meta.resources.get('settlement_operations', {}).values())
                or any(row['state'] != 'TERMINAL' for row in meta.resources.get('terminal_operations', {}).values())):
            raise StorageError('SPOT_FINANCIAL_OTHER_RECOVERY_PENDING')
        age = (now - facts.observed_at).total_seconds() * 1000
        if (not 0 <= age <= self._recovery_policy.max_fact_age_ms
                or facts.observed_at < datetime.fromisoformat(record['prepared_at'])
                or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0
                or now < view.session.last_seen_at
                or (prepared.session_id, prepared.epoch, prepared.config_version) != (
                    view.session.session_id, view.session.epoch, view.session.config_version)):
            raise StorageError('SPOT_PREPARE_RECOVERY_FACTS_UNQUALIFIED')
        return view, meta, record, prepared, False

    def _prepare_recovery_evidence(self, prepared, record, view, meta, facts, arm):
        commands = meta.resources['commands']
        known = {row['command']['intent_id'] for row in commands.values()}
        wal_rows = self._wal.all_records()
        if (not {row.intent_id for row in wal_rows}.issubset(known)
                or not self._ledger.reservation_ids.issubset(known)
                or not self._subsidy.verified_intent_ids().issubset(known)):
            raise StorageError('SPOT_PREPARE_RECOVERY_NATIVE_SCOPE_MISMATCH')
        fills, cashflows = meta.resources.get('fills', {}), meta.resources.get('cashflows', {})
        if any(row['intent_id'] == prepared.intent_id for row in fills.values()):
            raise StorageError('SPOT_PREPARE_RECOVERY_ALREADY_FILLED')
        others = {key: row for key, row in commands.items() if key != prepared.operation_id}
        self._verify_recovery_projection(others, fills, cashflows, facts)
        self._verify_fill_projection(commands, fills)
        self._verify_capital_projection(commands, fills, cashflows)
        self._verify_terminal_projection(meta.resources)
        for value in others.values():
            original = PrepareSpot.from_json(value['command'])
            other = self._wal.get(original.intent_id)
            if (other.state != value['state'] or other.state == 'SEND_UNKNOWN'
                    or value['state'] != 'TERMINAL' and not self._matches_accounting_hold(original, value)):
                raise StorageError('SPOT_PREPARE_OTHER_NATIVE_STATE_UNPROVEN')
        wal = self._wal.get(prepared.intent_id) if self._wal.has_intent(prepared.intent_id) else None
        allowed = ('PREPARED', 'SEND_UNKNOWN') if arm else ('PREPARED',)
        if wal is not None and (wal.state not in allowed or wal.cancel_requested or wal.cancel_attempts
                                or wal.exchange_order_id is not None or wal.exchange_terminal_observed
                                or (wal.client_order_id, wal.reservation_id, wal.session_id, wal.epoch,
                                    wal.slot_market, wal.slot_side, wal.slot_level) != (
                                    prepared.client_order_id, prepared.intent_id, prepared.session_id, prepared.epoch,
                                    prepared.market, prepared.side, prepared.quote_level)):
            raise StorageError('SPOT_PREPARE_RECOVERY_WAL_MISMATCH')
        if (arm and wal is None or (wal is None or wal.state == 'PREPARED')
                and prepared.client_order_id in facts.accepted_wire_ids):
            raise StorageError('SPOT_PREPARE_RECOVERY_SEND_SCOPE_UNPROVEN')
        native = self._ledger.reservation_state(prepared.intent_id)
        if native is not None and not self._ledger.matches_open_intent(self._intent(prepared)):
            raise StorageError('SPOT_PREPARE_RECOVERY_HOLD_MISMATCH')
        at = datetime.fromisoformat(record['prepared_at'])
        subsidy_exists = prepared.intent_id in self._subsidy.verified_intent_ids()
        if subsidy_exists and not self._subsidy.matches_reservation(
                prepared.intent_id, prepared.subsidy_quote_usdt, session_id=prepared.session_id, at_utc=at):
            raise StorageError('SPOT_PREPARE_RECOVERY_SUBSIDY_MISMATCH')
        if arm and (native is None or not subsidy_exists):
            raise StorageError('SPOT_ARM_RECOVERY_HOLD_INCOMPLETE')
        if native is None:
            principal_base = prepared.quantity_base if prepared.side == 'SELL' else Decimal('0')
            principal_quote = prepared.quantity_base * prepared.limit_price_usdt if prepared.side == 'BUY' else Decimal('0')
            # Fee claims already exist in the pending coordinator snapshot.
            if (principal_base > view.balance.available_base_life or principal_quote > view.balance.available_quote_usdt
                    or view.balance.available_base_life - principal_base < self._risk_limits.min_inventory_base
                    or not self._ledger.preview().check_and_hold(
                        self._intent(prepared), reference_price=prepared.reference_price_usdt).allowed):
                raise StorageError('SPOT_PREPARE_RECOVERY_CAPITAL_DENIED')
        if not subsidy_exists and prepared.subsidy_quote_usdt > self._subsidy.verified_status(
                session_id=prepared.session_id, at_utc=at).available_quote:
            raise StorageError('SPOT_PREPARE_RECOVERY_SUBSIDY_DENIED')
        return wal, native, subsidy_exists

    def _publish_prepare_recovery(self, key, owner, commands, hashes):
        meta = self._read()
        return self._commit(meta.cut, owner, key, (
            ('commands', encode(commands)), ('journal_hashes', encode(hashes))), observation=True)

    def _resume_preparation(self, operation_id, command, facts, cut, owner, arm):
        view, meta, record, prepared, replayed = self._prepare_recovery_guard(
            operation_id, command, facts, cut, owner, arm)
        if replayed:
            return PrepareReceipt(self.binding, operation_id, prepared.intent_id, record['state'], True, self.atomic_scope)
        wal, native, subsidy_exists = self._prepare_recovery_evidence(prepared, record, view, meta, facts, arm)
        commands = thaw(meta.resources['commands'])
        commands[operation_id]['recovery_facts'] = facts.json()
        stage = ('arm-resume:' if arm else 'prepare-resume:') + fingerprint({
            'operation': operation_id, 'facts': facts.json(), 'cut': cut.json()})
        try:
            self._publish_prepare_recovery(stage, owner, commands, dict(view.cut.components))
            if not arm:
                if wal is None:
                    self._wal.begin(prepared.intent_id, client_order_id=prepared.client_order_id,
                                    session_id=prepared.session_id, epoch=prepared.epoch, reservation_id=prepared.intent_id,
                                    slot_market=prepared.market, slot_side=prepared.side, slot_level=prepared.quote_level)
                if native is None:
                    self._ledger.reserve(self._intent(prepared), reference_price=prepared.reference_price_usdt)
                if not subsidy_exists:
                    self._subsidy.reserve(prepared.intent_id, prepared.subsidy_quote_usdt,
                                          session_id=prepared.session_id, at_utc=datetime.fromisoformat(record['prepared_at']))
                self._assert_holds(prepared, record)
            else:
                if wal.state == 'PREPARED':
                    self._assert_holds(prepared, record)
                    self._wal.arm_send(prepared.intent_id, client_order_id=prepared.client_order_id,
                                       session_id=prepared.session_id, epoch=prepared.epoch, reservation_id=prepared.intent_id)
                if self._wal.get(prepared.intent_id).state != 'SEND_UNKNOWN':
                    raise StorageError('SPOT_ARM_PUBLICATION_INCOMPLETE')
            verified = self._snapshot()
            self._prepare_recovery_evidence(prepared, record, verified, meta, facts, arm)
            commands[operation_id]['state'] = 'SEND_UNKNOWN' if arm else 'PREPARED'
            key = ('armed:' if arm else 'prepared:') + operation_id
            self._publish_prepare_recovery(key, owner, commands, dict(verified.cut.components))
            return PrepareReceipt(self.binding, operation_id, prepared.intent_id, commands[operation_id]['state'],
                                  False, self.atomic_scope)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_PREPARE_RESUME_UNKNOWN') from None

    async def _resume_preparation_command(self, operation_id, command, facts, cut, owner, arm):
        identifier(operation_id)
        if (not arm and not isinstance(command, PrepareSpot) or not isinstance(facts, SpotFinancialRecoveryFacts)
                or not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken)):
            raise StorageError('SPOT_PREPARE_RECOVERY_COMMAND_INVALID')
        self._assert_binding(cut.checkpoint.binding)
        self._assert_binding(facts.binding)
        try:
            return await self._call(self._resume_preparation, operation_id, command, facts, cut, owner, arm)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    async def resume_prepare(self, command, *, facts, cut, owner):
        if not isinstance(command, PrepareSpot):
            raise StorageError('SPOT_PREPARE_RECOVERY_COMMAND_INVALID')
        return await self._resume_preparation_command(command.operation_id, command, facts, cut, owner, False)

    async def resume_arm(self, operation_id, *, facts, cut, owner):
        return await self._resume_preparation_command(operation_id, None, facts, cut, owner, True)
