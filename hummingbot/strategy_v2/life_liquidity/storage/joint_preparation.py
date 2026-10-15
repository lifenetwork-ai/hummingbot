"""Complete original prepared receipts inside a qualified joint money batch.

Only already complete native holds are accepted. No missing hold, WAL arm or
sender permit is reconstructed by this adapter.
"""
from datetime import datetime

from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, fingerprint, thaw, utc
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut, PrepareSpot


class LegacyJointPreparationAdapter:
    def _restore_original_preparation_holds(self, prepared, facts, owner, authority):
        self.assert_worker_thread()
        if self._shared_spot is None or self._shared_spot.joint is not authority or authority.store is not self:
            raise StorageError('JOINT_ORIGINAL_HOLD_AUTHORITY_REQUIRED')
        authority._guard()
        meta = self._read()
        self._prepared_request_origin(prepared, meta.resources['commands'].get(prepared.operation_id), owner)
        view, meta, record, original, completed = self._prepare_recovery_guard(
            prepared.operation_id, prepared, facts, self._snapshot().cut, owner, False)
        if completed:
            return
        # A hold is a liability, never income or a release. Reconstruct only
        # admitted original zero-fill holds over verified prior native money.
        # Final account comparison belongs to the subsequent complete joint
        # proof; PREPARING remains durable throughout this conservative repair.
        wal, principal, subsidy = self._prepare_recovery_evidence(original, record, view, meta, facts, False, hold_only=True)
        commands = thaw(meta.resources['commands'])
        commands[prepared.operation_id]['hold_recovery_facts'] = facts.json()
        try:
            self._publish_prepare_recovery('hold-restore:' + fingerprint({'operation': prepared.operation_id, 'cut': view.cut.json()}),
                                           owner, commands, dict(view.cut.components))
            self._shared_spot.recover_hold(original, facts, owner)
            if wal is None:
                self._wal.begin(original.intent_id, client_order_id=original.client_order_id, session_id=original.session_id,
                                epoch=original.epoch, reservation_id=original.intent_id, slot_market=original.market,
                                slot_side=original.side, slot_level=original.quote_level)
            if principal is None:
                if not self._ledger.reserve(self._intent(original), reference_price=original.reference_price_usdt).allowed:
                    raise StorageError('JOINT_ORIGINAL_PRINCIPAL_INCOMPLETE')
            if not subsidy:
                if not self._subsidy.reserve(original.intent_id, original.subsidy_quote_usdt, session_id=original.session_id,
                                             at_utc=datetime.fromisoformat(record['prepared_at'])):
                    raise StorageError('JOINT_ORIGINAL_SUBSIDY_INCOMPLETE')
            self._assert_original_prepared_receipt(original, record, owner, facts.observed_at)
            self._publish_prepare_recovery('holds-restored:' + original.operation_id, owner, commands,
                                           self._hashes(self._raw()))
        except BaseException:
            self._uncertain = True
            raise

    def _restore_original_arm_bookkeeping(self, operation_id, facts, owner, authority):
        self.assert_worker_thread()
        if self._shared_spot is None or self._shared_spot.joint is not authority or authority.store is not self:
            raise StorageError('JOINT_ORIGINAL_HOLD_AUTHORITY_REQUIRED')
        authority._guard()
        record = self._read().resources['commands'].get(operation_id)
        if record is None:
            raise StorageError('JOINT_ORIGINAL_ARM_REQUIRED')
        prepared = PrepareSpot.from_json(record['command'])
        self._prepared_request_origin(prepared, record, owner)
        view, meta, record, prepared, completed = self._prepare_recovery_guard(
            operation_id, None, facts, self._snapshot().cut, owner, True)
        if completed:
            return
        if FinancialCut.from_json(record['arm_cut']).checkpoint.binding != self.binding:
            raise StorageError('JOINT_ORIGINAL_ARM_NAMESPACE_MISMATCH')
        wal, _, _ = self._prepare_recovery_evidence(prepared, record, view, meta, facts, True, hold_only=True)
        commands = thaw(meta.resources['commands'])
        commands[operation_id]['arm_hold_recovery_facts'] = facts.json()
        try:
            self._publish_prepare_recovery('arm-hold-restore:' + fingerprint({'operation': operation_id, 'cut': view.cut.json()}),
                                           owner, commands, dict(view.cut.components))
            if wal.state == 'PREPARED':
                self._assert_holds(prepared, record)
                self._wal.arm_send(prepared.intent_id, client_order_id=prepared.client_order_id,
                                   session_id=prepared.session_id, epoch=prepared.epoch, reservation_id=prepared.intent_id)
            self._prepare_recovery_evidence(prepared, record, self._snapshot(), meta, facts, True, hold_only=True)
            commands[operation_id]['state'] = 'SEND_UNKNOWN'
            self._publish_prepare_recovery('armed:' + operation_id, owner, commands, self._hashes(self._raw()))
        except BaseException:
            self._uncertain = True
            raise

    def _prepared_request_origin(self, prepared, record, owner):
        if (record is None or record['command'] != prepared.json() or record.get('original_owner') != owner.json()
                or 'original_cut' not in record or record['fingerprint'] != fingerprint({'command': prepared.json(),
                                                                                         'cut': record['original_cut'], 'owner': owner.json()})):
            raise StorageError('JOINT_PREPARED_RECEIPT_ORIGIN_INVALID')
        cut = FinancialCut.from_json(record['original_cut'])
        at = datetime.fromisoformat(record['prepared_at'])
        utc(at)
        if (cut.checkpoint.binding != self.binding or (prepared.session_id, prepared.epoch, prepared.config_version) != (
                str(owner.session_id), owner.epoch, self._config_version) or not owner.valid_from <= at < owner.expires_at):
            raise StorageError('JOINT_PREPARED_RECEIPT_ORIGIN_INVALID')
        return at

    def _assert_original_prepared_receipt(self, prepared, record, owner, observed_at, *, states=('PREPARING',)):
        at = self._prepared_request_origin(prepared, record, owner)
        if record['state'] not in states or record.get('arm_fingerprint') is not None or at > observed_at:
            raise StorageError('JOINT_PREPARED_RECEIPT_ORIGIN_INVALID')
        if not self._wal.has_intent(prepared.intent_id):
            raise StorageError('JOINT_PREPARED_RECEIPT_NATIVE_INCOMPLETE')
        self._assert_holds(prepared, record)
        attempt = self._wal.get(prepared.intent_id)
        reservation = self._ledger.reservation_state(prepared.intent_id)
        if (attempt.exchange_order_id is not None or attempt.exchange_terminal_observed
                or attempt.cancel_attempts or attempt.last_cancel_attempt_at is not None
                or reservation.state != 'OPEN' or reservation.filled_base != 0):
            raise StorageError('JOINT_PREPARED_RECEIPT_NATIVE_CHANGED')

    async def prepared_receipt_ready(self, prepared, *, owner, observed_at):
        # A pure routing diagnostic for the file service, never a release or
        # send grant. The joint source and batch independently recheck it.
        if type(prepared) is not PrepareSpot or type(owner) is not OwnerToken:
            raise StorageError('JOINT_PREPARED_RECEIPT_COMMAND_INVALID')
        utc(observed_at)

        def inspect():
            resources = self._read().resources
            try:
                self._assert_original_prepared_receipt(prepared, resources['commands'].get(prepared.operation_id),
                                                       owner, observed_at)
            except StorageError:
                return False
            return True
        return await self._call(inspect)

    def _joint_prepared_receipt_projection(self, command, resources, owner):
        commands = thaw(resources['commands'])
        for prepared in command.spot_prepared_receipts:
            record = commands.get(prepared.operation_id)
            self._assert_original_prepared_receipt(prepared, record, owner, command.observed_at)
            if (any(row['intent_id'] == prepared.intent_id for row in resources.get('fills', {}).values())
                    or any(row.intent_id == prepared.intent_id for row in (*command.spot_fills, *command.spot_acks))):
                raise StorageError('JOINT_PREPARED_RECEIPT_SOURCE_CONFLICT')
            record['state'] = 'PREPARED'
            record['prepared_receipt_batch'] = command.operation_id
        return commands

    def _verify_joint_prepared_receipts(self, command, resources, owner):
        writer = OwnerToken.from_json(owner)
        for prepared in command.spot_prepared_receipts:
            record = resources['commands'].get(prepared.operation_id)
            self._prepared_request_origin(prepared, record, writer)
            if record['state'] == 'PREPARING' or record.get('prepared_receipt_batch') != command.operation_id:
                raise StorageError('JOINT_PREPARED_RECEIPT_PROJECTION_INVALID')

    def _joint_partial_prepared_receipt_cut(self, view, resources, command):
        if command is None or not command.spot_prepared_receipts:
            return False
        # Only publication of these complete original holds may explain the
        # changed cut. Unrelated partial monetary/cancel/ACK writes need their
        # own qualified batch contract; this path never adopts their hashes.
        if any(dict(view.cut.components)[key] != value for key, value in resources['journal_hashes'].items()
                if key in ('session', 'loss')):
            return False
        owner = self._read().ownership.token
        commands = self._joint_prepared_receipt_projection(command, resources, owner)
        known = {record['command']['intent_id'] for record in commands.values()}
        if (known != {row.intent_id for row in self._wal.all_records()}
                or known != self._ledger.reservation_ids or known != self._subsidy.verified_intent_ids()):
            raise StorageError('JOINT_PREPARED_RECEIPT_NATIVE_SCOPE_CHANGED')
        for record in commands.values():
            prepared = PrepareSpot.from_json(record['command'])
            attempt = self._wal.get(prepared.intent_id)
            if record['state'] not in ('PREPARED', 'SEND_UNKNOWN', 'ACKED', 'TERMINAL', 'ABORTED_BEFORE_SEND'):
                raise StorageError('JOINT_PREPARED_RECEIPT_OTHER_RECOVERY_PENDING')
            if attempt.state != record['state']:
                raise StorageError('JOINT_PREPARED_RECEIPT_OTHER_ATTEMPT_CHANGED')
            if record['state'] not in ('TERMINAL', 'ABORTED_BEFORE_SEND') and not self._matches_accounting_hold(prepared, record):
                raise StorageError('JOINT_PREPARED_RECEIPT_OTHER_HOLD_CHANGED')
        self._verify_fill_projection(commands, resources.get('fills', {}))
        self._verify_capital_projection(commands, resources.get('fills', {}), resources.get('cashflows', {}))
        self._verify_terminal_projection(resources)
        self._verify_lifecycle_projection(resources)
        return True
