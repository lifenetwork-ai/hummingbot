"""Operator-approved file lifecycle updates; cold readback is never activation."""
import asyncio
import time
from datetime import datetime
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.storage.capital import ApprovedSpotCashflow
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    OwnerMode,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    UnsupportedBackend,
    fingerprint,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import (
    ApproveSpotCashflow,
    ControlReceipt,
    OwnerTransition,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut, PrepareSpot
from hummingbot.strategy_v2.life_liquidity.storage.import_commands import (
    ImportApprovedCashflows,
    ImportPreparationOrigin,
)
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


class LegacyControlAdapter:
    def _read(self):
        snapshot = super()._read()
        records = snapshot.resources.get('control_operations', {})
        if self._operator_keys is None:
            if records or 'control_seed' in snapshot.resources:
                raise StorageUnavailable('CONTROL_KEYS_UNAVAILABLE')
            return snapshot
        try:
            ownership = self._operator_keys.verify_seed(snapshot.resources['control_seed'], self.binding)
            approved, inventory_mode, observation_grants = {}, None, {}
            for key, row in sorted(records.items(), key=lambda item: item[1]['revision']):
                self._operator_keys.audit(row, self.binding)
                entry = snapshot.resources['control_operations'][key]
                if row['payload']['operation_id'] != key or row['revision'] != row['cut']['checkpoint']['revision'] + 1:
                    raise StorageUnavailable('CONTROL_HISTORY_UNVERIFIED')
                ledger = self._state_operation(snapshot, 'control:' + key)
                if (ledger['revision'] != row['revision'] or ledger['fingerprint'] != fingerprint({
                        'approval': row['approval'], 'payload': row['payload']})):
                    raise StorageUnavailable('CONTROL_HISTORY_UNVERIFIED')
                role = entry['approval']['role']
                if role in ('ACTIVATE', 'DRAIN', 'RECOVER', 'REVOKE'):
                    mode = OwnerMode(row['payload']['mode'])
                    expected_role = {OwnerMode.ACTIVE: 'ACTIVATE', OwnerMode.DRAINING: 'DRAIN',
                                     OwnerMode.RECOVERY_ONLY: 'RECOVER', OwnerMode.UNARMED: 'REVOKE'}[mode]
                    if role != expected_role:
                        raise StorageUnavailable('CONTROL_HISTORY_UNVERIFIED')
                    ownership = {'mode': mode.value, 'token': row['payload']['token']}
                elif role == 'OBSERVE_SESSION':
                    payload = row['payload']
                    if payload['kind'] != 'OBSERVE_SESSION_SUCCESSOR' or payload['owner'] != ownership['token']:
                        raise StorageUnavailable('CONTROL_SESSION_OBSERVATION_UNVERIFIED')
                    observation_grants[key] = thaw(payload)
                elif role == 'APPROVE_CASHFLOW':
                    bill = row['payload']['bill']
                    if bill['bill_id'] in approved and approved[bill['bill_id']] != bill:
                        raise StorageUnavailable('CONTROL_APPROVAL_CONFLICT')
                    approved[bill['bill_id']] = bill
                elif role == 'INVENTORY':
                    payload = row['payload']
                    if (payload['kind'] != 'INVENTORY_MODE' or payload['mode'] not in ('MM', 'INVENTORY')
                            or payload['campaign_id'] != snapshot.resources['inventory_service_policy']['campaign_id']
                            or payload['policy_digest'] != fingerprint(thaw(snapshot.resources['inventory_service_policy']))):
                        raise StorageUnavailable('CONTROL_INVENTORY_MODE_UNVERIFIED')
                    inventory_mode = {'mode': payload['mode'], 'control_operation': key}
                elif role == 'IMPORT':
                    payload = row['payload']
                    if payload['kind'] == 'IMPORT_PREPARATION_ORIGIN':
                        restored = snapshot.resources['commands'][payload['prepare_operation_id']]
                        if (restored.get('original_cut') != payload['original_cut']
                                or restored.get('original_owner') != payload['original_owner']):
                            raise StorageUnavailable('CONTROL_IMPORT_PROJECTION_UNVERIFIED')
                    elif payload['kind'] == 'IMPORT_APPROVED_CASHFLOWS':
                        for imported in payload['cashflows']:
                            if snapshot.resources['cashflows'].get(imported['bill_id']) != imported:
                                raise StorageUnavailable('CONTROL_IMPORT_PROJECTION_UNVERIFIED')
                    else:
                        raise StorageUnavailable('CONTROL_IMPORT_SCHEMA_UNSUPPORTED')
                else:
                    raise StorageUnavailable('CONTROL_COMMAND_SCHEMA_UNSUPPORTED')
            actual = {'mode': snapshot.ownership.mode.value,
                      'token': snapshot.ownership.token.json() if snapshot.ownership.token is not None else None}
            if 'inventory_service_policy' in snapshot.resources:
                expected_mode = inventory_mode or {'mode': 'MM', 'control_operation': None}
                if snapshot.resources.get('inventory_mode') != expected_mode:
                    raise StorageUnavailable('CONTROL_INVENTORY_MODE_UNVERIFIED')
            elif 'inventory_mode' in snapshot.resources:
                raise StorageUnavailable('CONTROL_INVENTORY_MODE_UNVERIFIED')
            if (actual != ownership or approved != snapshot.resources.get('additional_cashflow_approvals', {})
                    or observation_grants != snapshot.resources.get('session_observation_grants', {})):
                raise StorageUnavailable('CONTROL_PROJECTION_UNVERIFIED')
        except (KeyError, ValueError, TypeError, StorageError):
            raise StorageUnavailable('CONTROL_HISTORY_UNVERIFIED') from None
        return snapshot

    def _state_operation(self, _snapshot, key):
        # File-only implementation detail; repository callers receive no caches
        # or paths. The owning worker serializes this fresh verified read.
        with self._state.inspect() as state:
            return dict(state['operations'][key])

    def _control_commit(self, operation_id, cut, approval, payload, ownership, facts, changes=None):
        self._check_file()
        with self._state.locked() as state:
            self._validate(state)
            now = utc(self._clock())
            key = 'control:' + operation_id
            previous = state['operations'].get(key)
            command_hash = fingerprint({'approval': approval.body(), 'payload': payload})
            if previous is not None:
                if previous['fingerprint'] != command_hash:
                    raise StorageConflict('CONTROL_OPERATION_CONFLICT')
                return True
            if self._cut(state) != cut.checkpoint:
                raise StorageConflict('STORAGE_CUT_STALE')
            if not approval.issued_at <= now < approval.expires_at:
                raise StorageError('OPERATOR_APPROVAL_EXPIRED_AT_COMMIT')
            if self._hashes(self._raw()) != dict(cut.components):
                raise StorageConflict('CONTROL_NATIVE_CUT_STALE')
            observation_only = (approval.role == 'OBSERVE_SESSION'
                                and payload.get('kind') == 'OBSERVE_SESSION_SUCCESSOR'
                                and ownership == state['ownership'])
            if observation_only and now >= datetime.fromisoformat(payload['expires_at']):
                raise StorageError('SESSION_OBSERVATION_EXPIRED_AT_COMMIT')
            if ownership['mode'] == 'ACTIVE' and not observation_only and not (
                    datetime.fromisoformat(ownership['token']['valid_from']) <= now
                    < datetime.fromisoformat(ownership['token']['expires_at'])):
                raise StorageError('CONTROL_OWNER_EXPIRED_AT_COMMIT')
            if state['checked_at'] is not None and now < datetime.fromisoformat(state['checked_at']):
                raise StorageError('CONTROL_CLOCK_ROLLBACK')
            records = state['resources'].setdefault('control_operations', {})
            revision = state['revision'] + 1
            records[operation_id] = {'payload': payload, 'approval': approval.body(), 'signature': approval.signature,
                                     'cut': cut.json(), 'facts': facts.json(), 'state': 'APPLIED', 'revision': revision}
            state['versions']['control_operations'] = state['versions'].get('control_operations', 0) + 1
            state['ownership'] = ownership
            for name, value in (changes or {}).items():
                state['resources'][name] = value
                state['versions'][name] = state['versions'].get(name, 0) + 1
            state['revision'] = state['event_version'] = revision
            state['checked_at'] = now.isoformat()
            state['operations'][key] = {'fingerprint': command_hash, 'revision': revision}
            self._capacity(state)
            try:
                self._state.commit(state)
            except BaseException:
                self._uncertain = True
                raise CommitUnknown('CONTROL_PUBLICATION_UNKNOWN') from None
            return False

    def _transition_owner(self, command, approval, facts, cut):
        now = utc(self._clock())
        if self._operator_keys is None:
            raise UnsupportedBackend('OPERATOR_KEYS_REQUIRED')
        meta = self._read()
        previous = meta.resources.get('control_operations', {}).get(command.operation_id)
        if previous is not None:
            if thaw(previous['payload']) != command.json() or previous['approval'] != approval.body():
                raise StorageConflict('CONTROL_OPERATION_CONFLICT')
            # Historical readback, even if its approval expired. Never toggle
            # process admission from a persisted activation receipt.
            return ControlReceipt(self.binding, command.operation_id, command.mode, command.token, True)
        self._operator_keys.verify(approval, self.binding, command.role, command.json(), cut, facts, now)
        view = self._snapshot()
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        current = meta.ownership.token
        if command.mode is OwnerMode.ACTIVE:
            if self._recovery_policy is None:
                raise UnsupportedBackend('SPOT_FINANCIAL_RECOVERY_POLICY_REQUIRED')
            token = command.token
            if (current is not None and token.generation == current.generation
                    or not token.valid_from <= now < token.expires_at or token.expires_at > view.session.expires_at
                    or str(token.session_id) != view.session.session_id or token.epoch != view.session.epoch
                    or view.session.state != 'ACTIVE' or view.session.rollback_latched or now < view.session.last_seen_at
                    or not view.session.started_at <= now < view.session.expires_at
                    or self._incomplete(meta.resources) or self._reporting_pending(meta.resources)
                    or dict(view.cut.components) != dict(meta.resources['journal_hashes'])
                    or any(row.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND') for row in self._wal.all_records())
                    or facts.accepted_wire_ids or facts.runner_wire_ids
                    or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                    or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0
                    or not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self._recovery_policy.max_fact_age_ms):
                raise StorageError('ACTIVATION_FACTS_UNQUALIFIED')
            self._verify_recovery_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                             meta.resources.get('cashflows', {}), facts)
            self._verify_fill_projection(meta.resources['commands'], meta.resources.get('fills', {}))
            self._verify_capital_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                            meta.resources.get('cashflows', {}))
        elif command.mode is not OwnerMode.UNARMED and command.token != current:
            if command.mode is not OwnerMode.RECOVERY_ONLY or current is not None:
                raise StorageError('CONTROL_OBSERVATION_OWNER_MISMATCH')
            # REVOKE removes admission, not the provenance needed to account
            # outstanding attempts. Explicit signed RECOVER restores only the
            # latest signature-audited writer and never an arbitrary token.
            historical = self._operator_keys.verify_seed(meta.resources['control_seed'], self.binding)['token']
            for _, row in sorted(meta.resources.get('control_operations', {}).items(), key=lambda item: item[1]['revision']):
                if row['approval']['role'] in ('ACTIVATE', 'DRAIN', 'RECOVER') and row['payload']['token'] is not None:
                    historical = row['payload']['token']
            if historical is None or command.token.json() != historical:
                raise StorageError('CONTROL_OBSERVATION_OWNER_MISMATCH')
        replayed = self._control_commit(command.operation_id, cut, approval, command.json(),
                                        {'mode': command.mode.value,
                                         'token': command.token.json() if command.token is not None else None}, facts)
        if not replayed:
            self._cold_unarmed = command.mode is not OwnerMode.ACTIVE
            self._admission_generation = command.token.generation if command.token else None
            if command.mode is OwnerMode.ACTIVE:
                # A successor tick changes logical session facts only. Update
                # process admission deadlines solely after a new signed grant.
                self._session_identity = (view.session.session_id, view.session.epoch,
                                          view.session.expires_at.isoformat())
                self._monotonic_deadline = time.monotonic() + max(0, (view.session.expires_at - now).total_seconds())
            if command.mode in (OwnerMode.ACTIVE, OwnerMode.RECOVERY_ONLY):
                self._observation_generation = command.token.generation
            elif command.mode is OwnerMode.UNARMED:
                self._observation_generation = None
        return ControlReceipt(self.binding, command.operation_id, command.mode, command.token, replayed)

    def _approved_cashflows(self):
        approved = {row.bill_id: row for row in self._accounting_policy.approved_cashflows}
        for key, raw in self._read().resources.get('additional_cashflow_approvals', {}).items():
            bill = ApprovedSpotCashflow(raw['bill_id'], raw['currency'], Decimal(raw['signed_amount']), raw['transfer_at_ms'])
            if key != bill.bill_id or key in approved and approved[key] != bill:
                raise StorageUnavailable('CONTROL_APPROVAL_CONFLICT')
            approved[key] = bill
        return approved

    def _approve_cashflow(self, command, approval, facts, cut):
        if self._operator_keys is None or self._accounting_policy is None:
            raise UnsupportedBackend('OPERATOR_APPROVAL_POLICY_REQUIRED')
        meta = self._read()
        previous = meta.resources.get('control_operations', {}).get(command.operation_id)
        if previous is not None:
            if thaw(previous['payload']) != command.json() or previous['approval'] != approval.body():
                raise StorageConflict('CONTROL_OPERATION_CONFLICT')
            return ControlReceipt(self.binding, command.operation_id, meta.ownership.mode, meta.ownership.token, True)
        self._operator_keys.verify(approval, self.binding, 'APPROVE_CASHFLOW', command.json(), cut, facts, utc(self._clock()))
        view = self._snapshot()
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        bill = command.bill
        if (int(bill.bill_id) <= int(self._accounting_policy.anchor_bill_id)
                or bill.transfer_at_ms < int(self._accounting_policy.opened_at.timestamp() * 1000)):
            raise StorageError('CONTROL_BILL_ANCHOR_INVALID')
        existing = self._approved_cashflows().get(bill.bill_id)
        if existing is not None and existing != bill:
            raise StorageConflict('CONTROL_BILL_APPROVAL_CONFLICT')
        updated = thaw(meta.resources.get('additional_cashflow_approvals', {}))
        if existing is None:
            updated[bill.bill_id] = bill.json()
        # Persist every signed approval in the projection, including exact
        # repetition of an initially configured approval.
        else:
            updated[bill.bill_id] = existing.json()
        replayed = self._control_commit(command.operation_id, cut, approval, command.json(),
                                        {'mode': meta.ownership.mode.value,
                                         'token': meta.ownership.token.json() if meta.ownership.token else None}, facts,
                                        {'additional_cashflow_approvals': updated})
        return ControlReceipt(self.binding, command.operation_id, meta.ownership.mode, meta.ownership.token, replayed)

    async def approve_cashflow(self, command, *, approval, facts, cut):
        if (not isinstance(command, ApproveSpotCashflow) or not isinstance(facts, SpotFinancialRecoveryFacts)
                or not isinstance(cut, FinancialCut)):
            raise StorageError('CONTROL_COMMAND_INVALID')
        self._assert_binding(facts.binding)
        self._assert_binding(cut.checkpoint.binding)
        try:
            return await self._call(self._approve_cashflow, command, approval, facts, cut)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    def _import_legacy(self, command, approval, facts, cut):
        if self._operator_keys is None or self._recovery_policy is None:
            raise UnsupportedBackend('QUALIFIED_IMPORT_POLICY_REQUIRED')
        meta = self._read()
        previous = meta.resources.get('control_operations', {}).get(command.operation_id)
        if previous is not None:
            if thaw(previous['payload']) != command.json() or previous['approval'] != approval.body():
                raise StorageConflict('CONTROL_OPERATION_CONFLICT')
            return ControlReceipt(self.binding, command.operation_id, meta.ownership.mode, meta.ownership.token, True)
        now = utc(self._clock())
        self._operator_keys.verify(approval, self.binding, 'IMPORT', command.json(), cut, facts, now)
        view = self._snapshot()
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        if (not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self._recovery_policy.max_fact_age_ms
                or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0):
            raise StorageError('IMPORT_FACTS_UNQUALIFIED')
        changes = {}
        if isinstance(command, ImportPreparationOrigin):
            self._assert_binding(command.original_cut.checkpoint.binding)
            commands = thaw(meta.resources['commands'])
            row = commands.get(command.prepare_operation_id)
            if (row is None or row['state'] not in ('PREPARING', 'ARMING')
                    or command.original_owner != meta.ownership.token
                    or row['fingerprint'] != fingerprint({'command': row['command'],
                                                         'cut': command.original_cut.json(), 'owner': command.original_owner.json()})
                    or row.get('original_cut') is not None and row['original_cut'] != command.original_cut.json()
                    or row.get('original_owner') is not None and row['original_owner'] != command.original_owner.json()):
                raise StorageError('IMPORT_ORIGINAL_REQUEST_UNPROVEN')
            # Missing arm publication provenance cannot be guessed from a
            # prepare request. Such a format remains explicitly unsupported.
            if row['state'] == 'ARMING' and 'arm_fingerprint' not in row:
                raise UnsupportedBackend('IMPORT_ARM_PROVENANCE_REQUIRED')
            row['original_cut'], row['original_owner'] = command.original_cut.json(), command.original_owner.json()
            self._prepare_recovery_evidence(PrepareSpot.from_json(row['command']), row, view,
                                            type(meta)(meta.cut, {**meta.resources, 'commands': commands}, meta.ownership),
                                            facts, row['state'] == 'ARMING')
            changes['commands'] = commands
        else:
            if command.writer != meta.ownership.token or self._incomplete(meta.resources):
                raise StorageError('IMPORT_WRITER_OR_PENDING_OPERATION_INVALID')
            flows = thaw(meta.resources.get('cashflows', {}))
            operations = thaw(meta.resources.get('cashflow_operations', {}))
            for item in command.cashflows:
                self._validate_cashflow_value(item)
                native = self._ledger.cashflow_events.get(item.bill_id)
                if (native != (item.currency, item.signed_amount) or item.observed_at > facts.observed_at
                        or item.bill_id in flows and flows[item.bill_id] != item.json()
                        or item.operation_id in operations):
                    raise StorageError('IMPORT_NATIVE_CASHFLOW_UNPROVEN')
                flows[item.bill_id] = item.json()
                operations[item.operation_id] = {'command': item.json(), 'state': 'APPLIED',
                                                 'fingerprint': fingerprint({'command': item.json(), 'owner': command.writer.json()})}
            self._verify_recovery_projection(meta.resources['commands'], meta.resources.get('fills', {}), flows, facts)
            self._verify_fill_projection(meta.resources['commands'], meta.resources.get('fills', {}))
            self._verify_capital_projection(meta.resources['commands'], meta.resources.get('fills', {}), flows)
            changes.update(cashflows=flows, cashflow_operations=operations, journal_hashes=dict(cut.components))
        replayed = self._control_commit(command.operation_id, cut, approval, command.json(),
                                        {'mode': meta.ownership.mode.value,
                                         'token': meta.ownership.token.json() if meta.ownership.token else None}, facts, changes)
        # Import is observation/repair, never admission or a generation grant.
        return ControlReceipt(self.binding, command.operation_id, meta.ownership.mode, meta.ownership.token, replayed)

    async def import_legacy(self, command, *, approval, facts, cut):
        if (not isinstance(command, (ImportPreparationOrigin, ImportApprovedCashflows))
                or not isinstance(facts, SpotFinancialRecoveryFacts) or not isinstance(cut, FinancialCut)):
            raise StorageError('IMPORT_COMMAND_INVALID')
        self._assert_binding(facts.binding)
        self._assert_binding(cut.checkpoint.binding)
        try:
            return await self._call(self._import_legacy, command, approval, facts, cut)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    async def transition_owner(self, command, *, approval, facts, cut):
        if (not isinstance(command, OwnerTransition) or not isinstance(facts, SpotFinancialRecoveryFacts)
                or not isinstance(cut, FinancialCut)):
            raise StorageError('CONTROL_COMMAND_INVALID')
        self._assert_binding(facts.binding)
        self._assert_binding(cut.checkpoint.binding)
        # A genuine signed revocation closes local admission before any queue
        # wait. Failed durable publication cannot reopen that local barrier.
        if command.mode is not OwnerMode.ACTIVE and self._operator_keys is not None:
            self._operator_keys.verify(approval, self.binding, command.role, command.json(), cut, facts, utc(self._clock()))
            self._cold_unarmed = True
        try:
            return await self._call(self._transition_owner, command, approval, facts, cut)
        except asyncio.CancelledError:
            self._uncertain = True
            self._cold_unarmed = True
            raise
