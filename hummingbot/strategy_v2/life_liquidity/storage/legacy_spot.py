"""Offline spot ports on existing journals; multi-file work has recovery barriers.

No exchange I/O, blind repair, grant issuance or controller wiring. Exact-order
terminal release/resume requires the separate complete reconciliation facts port.
One account lock and one bounded worker own all live legacy objects. A separate
bound coordinator records STARTED before journal writes and completion afterward.
"""
import asyncio
import hashlib
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.account_lock import AccountRiskPoolLock
from hummingbot.strategy_v2.life_liquidity.accounting import CapitalLedger
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.session import SessionStore
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
from hummingbot.strategy_v2.life_liquidity.storage.capital import (
    CashflowReceipt,
    CashflowSpot,
    NavReceipt,
    ObserveSpotNAV,
    SpotAccountingPolicy,
    SpotCapitalView,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    CommitUnknown,
    OwnerMode,
    OwnerToken,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    UnsupportedBackend,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore
from hummingbot.strategy_v2.life_liquidity.storage.financial import (
    AckReceipt,
    AckSpot,
    AttemptState,
    BudgetPolicy,
    FillReceipt,
    FillSpot,
    FinancialCut,
    PrepareReceipt,
    PrepareSpot,
    SpotAttempt,
    SpotBalance,
    SpotBudget,
    SpotFillObservation,
    SpotFillPolicy,
    SpotReservation,
    SpotSession,
    SpotSnapshot,
)
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalPolicy
from hummingbot.strategy_v2.life_liquidity.storage.terminal_adapter import LegacyTerminalAdapter

PARTIAL_PHASES = ('PREPARING', 'ARMING', 'ACKING', 'FILLING', 'TERMINATING')


@dataclass(frozen=True)
class LegacySpotFiles:
    binding: Path
    session: Path
    wal: Path
    reservations: Path
    subsidy: Path
    loss: Path

    def __post_init__(self):
        for field in self.__dataclass_fields__:
            object.__setattr__(self, field, Path(getattr(self, field)).absolute())
        if len({value.resolve() for value in self.__dict__.values()}) != 6:
            raise StorageError('SPOT_JOURNAL_PATH_COLLISION')

    def journals(self):
        return {key: value for key, value in self.__dict__.items() if key != 'binding'}


class LegacySpotStore(LegacyTerminalAdapter, FileCheckpointStore):
    atomic_scope = AtomicScope.RECOVERABLE_SAGA

    def __init__(self, files, binding, limits, risk_limits, subsidy_policy, loss_policy,
                 config_version, account_lock_root, clock, fill_policy=None, accounting_policy=None, terminal_policy=None):
        if (not isinstance(files, LegacySpotFiles) or not isinstance(risk_limits, RiskLimits)
                or not isinstance(subsidy_policy, BudgetPolicy) or not isinstance(loss_policy, BudgetPolicy)
                or type(config_version) is not int or config_version < 1):
            raise StorageError('SPOT_STORAGE_CONFIG_INVALID')
        super().__init__(files.binding, binding, limits, clock)
        self._files, self._risk_limits = files, risk_limits
        self._subsidy_policy, self._loss_policy, self._config_version = subsidy_policy, loss_policy, config_version
        self._account_lock_root = Path(account_lock_root)
        if not self._account_lock_root.is_absolute():
            raise StorageError('SPOT_ACCOUNT_LOCK_ROOT_INVALID')
        self._account_lock = None
        if fill_policy is not None and not isinstance(fill_policy, SpotFillPolicy):
            raise StorageError('SPOT_FILL_POLICY_INVALID')
        self._fill_policy = fill_policy
        if accounting_policy is not None and (not isinstance(accounting_policy, SpotAccountingPolicy) or fill_policy is None):
            raise StorageError('SPOT_ACCOUNTING_POLICY_INVALID')
        self._accounting_policy = accounting_policy
        if terminal_policy is not None and (not isinstance(terminal_policy, SpotTerminalPolicy) or accounting_policy is None):
            raise StorageError('SPOT_TERMINAL_POLICY_INVALID')
        self._terminal_policy = terminal_policy
        self._policy['legacy_spot'] = {
            'paths': {key: str(value) for key, value in files.journals().items()},
            'account_lock_root': str(Path(account_lock_root)),
            'risk': {key: str(value) for key, value in risk_limits.__dict__.items()},
            'subsidy': subsidy_policy.json(), 'execution_loss': loss_policy.json(), 'config_version': config_version}
        if fill_policy is not None:
            self._policy['legacy_spot']['fill_policy'] = {'max_reference_skew_ms': fill_policy.max_reference_skew_ms}
        if accounting_policy is not None:
            self._policy['legacy_spot']['accounting_policy'] = accounting_policy.json()
        if terminal_policy is not None:
            self._policy['legacy_spot']['terminal_policy'] = {'max_fact_age_ms': terminal_policy.max_fact_age_ms}
        self._cold_unarmed = True
        self._cleanup_future = None

    def unit_of_work(self, *args, **kwargs):
        raise UnsupportedBackend('SPOT_GENERIC_CHECKPOINT_WRITES_UNSUPPORTED')

    def _raw(self):
        images, total = {}, 0
        for name, path in self._files.journals().items():
            if not path.is_file() or path.is_symlink():
                raise StorageUnavailable('SPOT_JOURNAL_MISSING_OR_UNSAFE')
            with path.open('rb') as file:
                raw = file.read(self.limits.max_checkpoint_bytes + 1)
            total += len(raw)
            if total > self.limits.max_checkpoint_bytes:
                raise StorageUnavailable('SPOT_JOURNAL_CAPACITY_EXCEEDED')
            images[name] = raw
        return images

    @staticmethod
    def _hashes(images):
        return {name: hashlib.sha256(raw).hexdigest() for name, raw in images.items()}

    def _initialize_spot(self, create, owner):
        if type(create) is not bool or create and self.binding.environment != 'OFFLINE_SIMULATION':
            raise StorageError('SPOT_BOOTSTRAP_OFFLINE_ONLY')
        if not create and owner is not None:
            raise StorageError('SPOT_COLD_REARM_REQUIRES_CONTROL_PLANE')
        self._account_lock = AccountRiskPoolLock(self.binding.account_uid, self._account_lock_root)
        self._account_lock.acquire()
        images = self._raw()  # Constructors may create empty state: refuse missing input first.
        try:
            self._sessions = SessionStore(self._files.session)
            self._wal = IntentWAL(self._files.wal)
            self._ledger = ReservationLedger.restore(self._files.reservations, limits=self._risk_limits)
            self._subsidy = SubsidyBudgetLedger(self._files.subsidy, **self._subsidy_policy.kwargs())
            self._loss = LossBudgetLedger(self._files.loss, **self._loss_policy.kwargs())
            journal = self._sessions.load()
            if journal is None:
                raise StorageUnavailable('SPOT_SESSION_UNAVAILABLE')
            record = journal.successor or journal.primary
            if record.config_version != self._config_version:
                raise StorageError('SPOT_CONFIG_VERSION_MISMATCH')
            self._session_identity = (record.session_id, record.epoch, record.expires_at.isoformat())
            self._monotonic_deadline = time.monotonic() + max(0, (record.expires_at - utc(self._clock())).total_seconds())
            initial = encode({'commands': {}, 'journal_hashes': self._hashes(images)})
            super()._initialize(create, initial, owner)
            self._cold_unarmed = not create
            self._snapshot()
        except (ValueError, KeyError, TypeError, OSError, ArithmeticError):
            raise StorageUnavailable('SPOT_LEGACY_JOURNAL_INVALID') from None

    def _snapshot(self):
        before = self._raw()
        try:
            self._wal.assert_verified()
            self._ledger.verify_memory()
            journal = self._sessions.load()
            if journal is None:
                raise StorageUnavailable('SPOT_SESSION_UNAVAILABLE')
            record = journal.successor or journal.primary
            now = utc(self._clock())
            subsidy = self._subsidy.verified_status(session_id=record.session_id, at_utc=now)
            loss = self._loss.verified_status(session_id=record.session_id, at_utc=now)
            meta = super()._read()
            if self._raw() != before:
                raise StorageUnavailable('SPOT_JOURNAL_CHANGED_DURING_READ')
            hashes = self._hashes(before)
            partial = (hashes != dict(meta.resources['journal_hashes'])
                       or self._incomplete(meta.resources))
            if self._fill_policy is not None and not partial:
                self._verify_fill_projection(meta.resources['commands'], meta.resources.get('fills', {}))
            if self._accounting_policy is not None and not partial:
                if self._accounting_policy.opened_at > now:
                    raise StorageUnavailable('SPOT_OPENING_CAPITAL_TIME_INVALID')
                self._verify_capital_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                                meta.resources.get('cashflows', {}))
            if self._terminal_policy is not None and not partial:
                self._verify_terminal_projection(meta.resources)
            fee_base, fee_quote, fee_overrun = self._fee_claims(meta.resources['commands'])
            pending_base, pending_quote = self._pending_terminal_principal(meta.resources)
            return SpotSnapshot(
                FinancialCut(meta.cut, tuple(sorted(hashes.items()))),
                SpotSession(record.session_id, record.epoch, record.config_version, record.started_at, record.expires_at,
                            journal.state, journal.last_seen_at, journal.clock_rollback_latched, record.anchors),
                SpotBalance(self._ledger.life_balance, self._ledger.usdt_balance, self._ledger.reserved_life + fee_base + pending_base,
                            self._ledger.reserved_usdt + fee_quote + pending_quote,
                            self._ledger.life_balance - self._ledger.reserved_life - fee_base - pending_base,
                            self._ledger.usdt_balance - self._ledger.reserved_usdt - fee_quote - pending_quote, fee_base, fee_quote),
                SpotBudget(subsidy.session_committed_quote, subsidy.day_committed_quote, subsidy.campaign_committed_quote,
                           subsidy.available_quote, loss.session_loss_quote, loss.day_loss_quote,
                           loss.campaign_loss_quote, loss.exhausted),
                partial or fee_overrun or self._cold_unarmed or any(row.state == 'SEND_UNKNOWN' for row in self._wal.active_records()))
        except (ValueError, KeyError, TypeError, OSError, ArithmeticError):
            raise StorageUnavailable('SPOT_FINANCIAL_CUT_UNVERIFIED') from None

    async def snapshot(self, binding):
        self._assert_binding(binding)
        return await self._call(self._snapshot)

    async def session(self, binding):
        return (await self.snapshot(binding)).session

    async def balance(self, binding):
        return (await self.snapshot(binding)).balance

    async def budget(self, binding):
        return (await self.snapshot(binding)).budget

    def _attempt(self, intent_id):
        self._snapshot()
        if not self._wal.has_intent(intent_id):
            return None
        row = self._wal.get(intent_id)
        return SpotAttempt(row.intent_id, row.client_order_id, row.reservation_id, row.session_id, row.epoch,
                           AttemptState(row.state), row.exchange_order_id, row.cancel_requested,
                           row.slot_market, row.slot_side, row.slot_level)

    async def attempt(self, intent_id, binding):
        self._assert_binding(binding)
        identifier(intent_id)
        return await self._call(self._attempt, intent_id)

    def _reservation(self, intent_id):
        self._snapshot()
        row = self._ledger.reservation_state(intent_id)
        if row is None:
            return None
        order = row.intent
        return SpotReservation(order.intent_id, order.side, order.session_id, order.epoch, order.quantity_base,
                               row.remaining_base, row.filled_base, order.limit_price_usdt, row.state)

    async def reservation(self, intent_id, binding):
        self._assert_binding(binding)
        identifier(intent_id)
        return await self._call(self._reservation, intent_id)

    def _lookup_prepare(self, operation_id):
        view = self._snapshot()
        record = super()._read().resources['commands'].get(operation_id)
        if record is None:
            return None
        partial = record['state'] in PARTIAL_PHASES or dict(view.cut.components) != dict(
            super()._read().resources['journal_hashes'])
        return PrepareReceipt(self.binding, operation_id, record['command']['intent_id'],
                              'RECOVERY_REQUIRED' if partial else record['state'], True, self.atomic_scope)

    async def lookup_prepare(self, operation_id, binding):
        self._assert_binding(binding)
        identifier(operation_id)
        return await self._call(self._lookup_prepare, operation_id)

    def _guard(self, view, owner):
        meta = super()._read()
        now = utc(self._clock())
        session = view.session
        if (view.recovery_required or self._cold_unarmed or meta.ownership.mode is not OwnerMode.ACTIVE
                or meta.ownership.token != owner or not owner.valid_from <= now < owner.expires_at):
            raise StorageError('SPOT_OWNER_OR_RECOVERY_GATE')
        if (session.state != 'ACTIVE' or session.rollback_latched or not session.started_at <= now < session.expires_at
                or now < session.last_seen_at or time.monotonic() >= self._monotonic_deadline
                or (session.session_id, session.epoch, session.expires_at.isoformat()) != self._session_identity
                or session.config_version != self._config_version
                or session.session_id != str(owner.session_id) or session.epoch != owner.epoch):
            raise StorageError('SPOT_SESSION_REVOKED_OR_EXPIRED')
        return meta

    @staticmethod
    def _intent(command):
        return SpotIntent(command.intent_id, command.side, command.quantity_base, command.limit_price_usdt,
                          command.session_id, command.epoch)

    def _publish(self, key, owner, commands, hashes):
        meta = super()._read()
        return super()._commit(meta.cut, owner, key, (('commands', encode(commands)), ('journal_hashes', encode(hashes))))

    def _prepare(self, command, cut, owner):
        view = self._snapshot()
        meta = self._guard(view, owner)
        command_hash = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        previous = meta.resources['commands'].get(command.operation_id)
        if previous is not None:
            if previous['fingerprint'] != command_hash:
                raise StorageConflict('SPOT_OPERATION_CONFLICT')
            if previous['state'] != 'PREPARED':
                raise StorageError('SPOT_RECONCILE_BEFORE_RETRY')
            self._assert_holds(command, previous)
            return PrepareReceipt(self.binding, command.operation_id, command.intent_id, 'PREPARED', True, self.atomic_scope)
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        if (command.session_id, command.epoch, command.config_version) != (
                view.session.session_id, view.session.epoch, view.session.config_version):
            raise StorageError('SPOT_COMMAND_SESSION_MISMATCH')
        if self._wal.has_intent(command.intent_id) or self._ledger.has_reservation(command.intent_id):
            raise StorageConflict('SPOT_INTENT_ID_REUSED')
        try:
            self._wal.find_by_client_order_id(command.client_order_id)
        except KeyError:
            pass
        else:
            raise StorageConflict('SPOT_WIRE_ID_REUSED')
        for row in self._wal.active_records():
            if row.state in ('TERMINAL', 'ABORTED_BEFORE_SEND'):
                continue
            if row.slot_market is None or (row.session_id, row.epoch) != (command.session_id, command.epoch):
                raise StorageError('SPOT_OLD_OR_UNSCOPED_ORDER')
            if (row.slot_market, row.slot_side, row.slot_level) == (command.market, command.side, command.quote_level):
                raise StorageConflict('SPOT_SLOT_OCCUPIED')
        required_quote = (command.quantity_base * command.limit_price_usdt if command.side == 'BUY' else Decimal('0'))
        required_base = command.quantity_base if command.side == 'SELL' else Decimal('0')
        if (required_quote + command.fee_buffer_quote_usdt > view.balance.available_quote_usdt
                or required_base + command.fee_buffer_base_life > view.balance.available_base_life
                or view.balance.available_base_life - required_base - command.fee_buffer_base_life
                < self._risk_limits.min_inventory_base):
            raise StorageError('SPOT_CAPITAL_INCLUDING_FEES_DENIED')
        decision = self._ledger.preview().check_and_hold(self._intent(command), reference_price=command.reference_price_usdt)
        if not decision.allowed or view.budget.loss_exhausted or command.subsidy_quote_usdt > view.budget.subsidy_available_usdt:
            raise StorageError('SPOT_CAPITAL_OR_BUDGET_DENIED')
        commands = thaw(meta.resources['commands'])
        commands[command.operation_id] = {'command': command.json(), 'fingerprint': command_hash,
                                          'state': 'PREPARING', 'prepared_at': utc(self._clock()).isoformat()}
        try:
            self._publish('prepare:' + command.operation_id, owner, commands, dict(view.cut.components))
            self._wal.begin(command.intent_id, client_order_id=command.client_order_id,
                            session_id=command.session_id, epoch=command.epoch, reservation_id=command.intent_id,
                            slot_market=command.market, slot_side=command.side, slot_level=command.quote_level)
            if not self._ledger.reserve(self._intent(command), reference_price=command.reference_price_usdt).allowed:
                raise StorageError('SPOT_RESERVATION_UNCERTAIN')
            at = datetime.fromisoformat(commands[command.operation_id]['prepared_at'])
            if not self._subsidy.reserve(command.intent_id, command.subsidy_quote_usdt,
                                         session_id=command.session_id, at_utc=at):
                raise StorageError('SPOT_SUBSIDY_UNCERTAIN')
            self._assert_holds(command, commands[command.operation_id])
            commands[command.operation_id]['state'] = 'PREPARED'
            self._publish('prepared:' + command.operation_id, owner, commands, self._hashes(self._raw()))
            return PrepareReceipt(self.binding, command.operation_id, command.intent_id, 'PREPARED', False, self.atomic_scope)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_PREPARATION_UNKNOWN') from None

    def _assert_holds(self, command, record):
        self._wal.assert_verified()
        self._ledger.verify_memory()
        attempt = self._wal.get(command.intent_id)
        if (attempt.state != 'PREPARED' or attempt.cancel_requested
                or (attempt.slot_market, attempt.slot_side, attempt.slot_level) != (command.market, command.side, command.quote_level)
                or (attempt.client_order_id, attempt.session_id, attempt.epoch, attempt.reservation_id) != (
                    command.client_order_id, command.session_id, command.epoch, command.intent_id)
                or not self._ledger.matches_open_intent(self._intent(command))
                or not self._subsidy.matches_reservation(command.intent_id, command.subsidy_quote_usdt,
                                                         session_id=command.session_id, at_utc=datetime.fromisoformat(record['prepared_at']))):
            raise StorageError('SPOT_PREPARE_EVIDENCE_INCOMPLETE')

    async def prepare(self, command, *, cut, owner):
        if not isinstance(command, PrepareSpot) or not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken):
            raise StorageError('SPOT_COMMAND_INVALID')
        self._assert_binding(cut.checkpoint.binding)
        try:
            return await self._call(self._prepare, command, cut, owner)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    def _arm(self, operation_id, cut, owner):
        view = self._snapshot()
        meta = self._guard(view, owner)
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        commands = thaw(meta.resources['commands'])
        record = commands.get(operation_id)
        if record is None or record['state'] != 'PREPARED':
            raise StorageError('SPOT_RECONCILE_BEFORE_RETRY')
        command = PrepareSpot.from_json(record['command'])
        self._assert_holds(command, record)
        try:
            record['state'] = 'ARMING'
            self._publish('arming:' + operation_id, owner, commands, dict(view.cut.components))
            self._wal.arm_send(command.intent_id, client_order_id=command.client_order_id,
                               session_id=command.session_id, epoch=command.epoch, reservation_id=command.intent_id)
            record['state'] = 'SEND_UNKNOWN'
            self._publish('armed:' + operation_id, owner, commands, self._hashes(self._raw()))
            return PrepareReceipt(self.binding, operation_id, command.intent_id, 'SEND_UNKNOWN', False, self.atomic_scope)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_ARMING_UNKNOWN') from None

    async def arm(self, operation_id, *, cut, owner):
        if not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken):
            raise StorageError('SPOT_COMMAND_INVALID')
        identifier(operation_id)
        self._assert_binding(cut.checkpoint.binding)
        try:
            return await self._call(self._arm, operation_id, cut, owner)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    def _ack_guard(self, view, owner):
        """Accept late observations from the same writer; never return send authority."""
        meta = super()._read()
        now = utc(self._clock())
        if (self._cold_unarmed or meta.ownership.token != owner
                or meta.ownership.mode not in (OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY)
                or now < owner.valid_from
                or dict(view.cut.components) != dict(meta.resources['journal_hashes'])
                or self._incomplete(meta.resources)):
            raise StorageError('SPOT_ACK_OWNER_OR_RECOVERY_GATE')
        return meta

    @staticmethod
    def _incomplete(resources):
        return (any(item['state'] in PARTIAL_PHASES for item in resources['commands'].values())
                or any(item['state'] != 'APPLIED' for item in resources.get('cashflow_operations', {}).values()))

    def _publish_ack(self, key, owner, commands, acknowledgements, hashes):
        meta = super()._read()
        return super()._commit(meta.cut, owner, key, (
            ('commands', encode(commands)), ('acknowledgements', encode(acknowledgements)),
            ('journal_hashes', encode(hashes))), observation=True)

    @staticmethod
    def _ack_receipt(binding, command, state, replayed):
        return AckReceipt(binding, command['operation_id'], command['intent_id'], command['exchange_order_id'],
                          state, replayed, AtomicScope.RECOVERABLE_SAGA)

    def _lookup_ack(self, operation_id):
        view = self._snapshot()
        meta = super()._read()
        row = meta.resources.get('acknowledgements', {}).get(operation_id)
        if row is None:
            return None
        incomplete = row['state'] != 'ACKED' or dict(view.cut.components) != dict(meta.resources['journal_hashes'])
        return self._ack_receipt(self.binding, row['command'], 'RECOVERY_REQUIRED' if incomplete else 'ACKED', True)

    async def lookup_ack(self, operation_id, binding):
        self._assert_binding(binding)
        identifier(operation_id)
        return await self._call(self._lookup_ack, operation_id)

    def _ack_evidence(self, ack, record):
        prepared = PrepareSpot.from_json(record['command'])
        if (ack.prepare_operation_id, ack.intent_id, ack.client_order_id, ack.market, ack.side, ack.session_id, ack.epoch) != (
                prepared.operation_id, prepared.intent_id, prepared.client_order_id, prepared.market, prepared.side,
                prepared.session_id, prepared.epoch):
            raise StorageError('SPOT_ACK_SCOPE_MISMATCH')
        row = self._wal.get(ack.intent_id)
        if (row.state not in ('SEND_UNKNOWN', 'ACKED')
                or (row.client_order_id, row.reservation_id, row.session_id, row.epoch,
                    row.slot_market, row.slot_side, row.slot_level) != (
                        prepared.client_order_id, prepared.intent_id, prepared.session_id, prepared.epoch,
                        prepared.market, prepared.side, prepared.quote_level)
                or row.exchange_order_id is not None and row.exchange_order_id != ack.exchange_order_id
                or not self._matches_accounting_hold(prepared, record)):
            raise StorageError('SPOT_ACK_EVIDENCE_INCOMPLETE')
        if any(other.intent_id != ack.intent_id and other.exchange_order_id == ack.exchange_order_id
               for other in self._wal.all_records()):
            raise StorageConflict('SPOT_EXCHANGE_ORDER_ID_REUSED')
        if any(value['intent_id'] == ack.intent_id and value['exchange_order_id'] != ack.exchange_order_id
               for value in super()._read().resources.get('fills', {}).values()):
            raise StorageConflict('SPOT_FILLED_EXCHANGE_ORDER_CONFLICT')
        if not datetime.fromisoformat(record['prepared_at']) <= ack.observed_at <= utc(self._clock()):
            raise StorageError('SPOT_ACK_OBSERVATION_TIME_INVALID')

    def _acknowledge(self, command, cut, owner):
        view = self._snapshot()
        meta = self._ack_guard(view, owner)
        commands = thaw(meta.resources['commands'])
        acknowledgements = thaw(meta.resources.get('acknowledgements', {}))
        # A retry may supply a fresh cut; the immutable observation and writer
        # identity must match. New operations still require an exact fresh cut.
        command_hash = fingerprint({'command': command.json(), 'owner': owner.json()})
        previous = acknowledgements.get(command.operation_id)
        if previous is not None and previous['fingerprint'] != command_hash:
            raise StorageConflict('SPOT_ACK_OPERATION_CONFLICT')
        record = commands.get(command.prepare_operation_id)
        if record is None or record['state'] not in ('SEND_UNKNOWN', 'ACKED'):
            raise StorageError('SPOT_ACK_ATTEMPT_NOT_SENT')
        self._ack_evidence(command, record)
        if previous is not None:
            if previous['state'] != 'ACKED' or self._wal.get(command.intent_id).state != 'ACKED':
                raise StorageError('SPOT_ACK_RECONCILE_BEFORE_RETRY')
            return self._ack_receipt(self.binding, command.json(), 'ACKED', True)
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        acknowledgements[command.operation_id] = {'command': command.json(), 'fingerprint': command_hash, 'state': 'ACKING'}
        record['state'] = 'ACKING'
        try:
            self._publish_ack('ack-start:' + command.operation_id, owner, commands, acknowledgements, dict(view.cut.components))
            self._wal.acknowledge(command.intent_id, command.exchange_order_id)
            row = self._wal.get(command.intent_id)
            if row.state != 'ACKED' or row.exchange_order_id != command.exchange_order_id:
                raise StorageError('SPOT_ACK_PUBLICATION_INCOMPLETE')
            record['state'] = 'ACKED'
            acknowledgements[command.operation_id]['state'] = 'ACKED'
            verified = self._snapshot()  # Never publish raw hashes of unverified financial changes.
            self._publish_ack('ack-done:' + command.operation_id, owner, commands, acknowledgements, dict(verified.cut.components))
            return self._ack_receipt(self.binding, command.json(), 'ACKED', False)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_ACK_UNKNOWN') from None

    async def acknowledge(self, command, *, cut, owner):
        if not isinstance(command, AckSpot) or not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken):
            raise StorageError('SPOT_COMMAND_INVALID')
        self._assert_binding(cut.checkpoint.binding)
        try:
            return await self._call(self._acknowledge, command, cut, owner)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    def _fee_claims(self, commands):
        # One pass over native fee identities; rebates never replenish a hold.
        paid = {}
        for trade_id in self._ledger.trade_ids:
            fee = self._ledger.fee_for_trade(trade_id)
            if fee is not None and fee[1] < 0:
                intent_id = self._ledger.trade_intent_id(trade_id)
                bucket = paid.setdefault(intent_id, {'LIFE': Decimal('0'), 'USDT': Decimal('0')})
                bucket[fee[0]] += -fee[1]
        base = quote = Decimal('0')
        overrun = False
        for record in commands.values():
            if record['state'] == 'TERMINAL':
                continue  # Release fee claims only after complete terminal publication.
            command = PrepareSpot.from_json(record['command'])
            debits = paid.get(command.intent_id, {'LIFE': Decimal('0'), 'USDT': Decimal('0')})
            base += max(Decimal('0'), command.fee_buffer_base_life - debits['LIFE'])
            quote += max(Decimal('0'), command.fee_buffer_quote_usdt - debits['USDT'])
            overrun |= (debits['LIFE'] > command.fee_buffer_base_life or debits['USDT'] > command.fee_buffer_quote_usdt)
        return base, quote, overrun

    def _matches_accounting_hold(self, prepared, record):
        reservation = self._ledger.reservation_state(prepared.intent_id)
        if reservation is None or reservation.intent != self._intent(prepared) or reservation.state == 'TERMINAL':
            return False
        if reservation.filled_base == 0:
            return (self._ledger.matches_open_intent(self._intent(prepared))
                    and self._subsidy.matches_reservation(prepared.intent_id, prepared.subsidy_quote_usdt,
                                                          session_id=prepared.session_id,
                                                          at_utc=datetime.fromisoformat(record['prepared_at'])))
        return (reservation.remaining_base + reservation.filled_base == prepared.quantity_base
                and self._subsidy.reserved_for(prepared.intent_id, session_id=prepared.session_id) == prepared.subsidy_quote_usdt)

    def _verify_fill_projection(self, commands, fills):
        if set(fills) != self._ledger.trade_ids:
            raise StorageUnavailable('SPOT_FILL_PROJECTION_INCOMPLETE')
        costs = {}
        for trade_id, value in fills.items():
            row = SpotFillObservation.from_json(value['fill'])
            prepared = PrepareSpot.from_json(commands[value['prepare_operation_id']]['command'])
            attempt = self._wal.get(prepared.intent_id)
            if (row.trade_id != trade_id or value['intent_id'] != prepared.intent_id
                    or (attempt.client_order_id, attempt.session_id, attempt.epoch, attempt.slot_market, attempt.slot_side) != (
                        prepared.client_order_id, prepared.session_id, prepared.epoch, prepared.market, prepared.side)
                    or attempt.exchange_order_id is not None and attempt.exchange_order_id != value['exchange_order_id']
                    or not self._ledger.matches_recorded_fill(prepared.intent_id, trade_id, row.quantity_base_life,
                                                              row.price_usdt, row.fee_currency, row.signed_fee)
                    or not self._loss.matches_event('fill:' + trade_id, row.loss(prepared.side),
                                                    session_id=prepared.session_id,
                                                    at_utc=datetime.fromtimestamp(row.fill_at_ms / 1000, timezone.utc))):
                raise StorageUnavailable('SPOT_FILL_PROJECTION_UNVERIFIED')
            costs[prepared.intent_id] = costs.get(prepared.intent_id, Decimal('0')) + row.loss(prepared.side)
        for intent_id, cost in costs.items():
            if not self._subsidy.matches_fill_floor(intent_id, cost):
                raise StorageUnavailable('SPOT_FILL_SUBSIDY_UNVERIFIED')

    @staticmethod
    def _fill_receipt(binding, row, state, replayed):
        data = row['command']
        return FillReceipt(binding, data['operation_id'], data['intent_id'], Decimal(data['cumulative_base_life']),
                           Decimal(row['execution_loss_usdt']), state, replayed, AtomicScope.RECOVERABLE_SAGA)

    def _lookup_fill(self, operation_id):
        view = self._snapshot()
        meta = super()._read()
        row = meta.resources.get('fill_operations', {}).get(operation_id)
        if row is None:
            return None
        partial = row['state'] != 'APPLIED' or dict(view.cut.components) != dict(meta.resources['journal_hashes'])
        return self._fill_receipt(self.binding, row, 'RECOVERY_REQUIRED' if partial else 'APPLIED', True)

    async def lookup_fill(self, operation_id, binding):
        self._assert_binding(binding)
        identifier(operation_id)
        return await self._call(self._lookup_fill, operation_id)

    def _publish_fills(self, key, owner, commands, operations, fills, hashes):
        meta = super()._read()
        return super()._commit(meta.cut, owner, key, (
            ('commands', encode(commands)), ('fill_operations', encode(operations)),
            ('fills', encode(fills)), ('journal_hashes', encode(hashes))), observation=True)

    def _apply_fills(self, command, cut, owner):
        if self._fill_policy is None:
            raise UnsupportedBackend('SPOT_FILL_POLICY_REQUIRED')
        view = self._snapshot()
        meta = self._ack_guard(view, owner)
        commands = thaw(meta.resources['commands'])
        operations = thaw(meta.resources.get('fill_operations', {}))
        fills = thaw(meta.resources.get('fills', {}))
        command_hash = fingerprint({'command': command.json(), 'owner': owner.json()})
        previous = operations.get(command.operation_id)
        if previous is not None:
            if previous['fingerprint'] != command_hash:
                raise StorageConflict('SPOT_FILL_OPERATION_CONFLICT')
            if previous['state'] != 'APPLIED':
                raise StorageError('SPOT_FILL_RECONCILE_BEFORE_RETRY')
            return self._fill_receipt(self.binding, previous, 'APPLIED', True)
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        record = commands.get(command.prepare_operation_id)
        if record is None or record['state'] not in ('SEND_UNKNOWN', 'ACKED'):
            raise StorageError('SPOT_FILL_ATTEMPT_NOT_SENT')
        # Reuse complete identity/hold checks; this deliberately permits fills
        # before the ACK reply, without changing SEND_UNKNOWN or granting send.
        ack = AckSpot(command.operation_id, command.prepare_operation_id, command.intent_id, command.client_order_id,
                      command.market, command.side, command.session_id, command.epoch, command.exchange_order_id, command.observed_at)
        self._ack_evidence(ack, record)
        prepared = PrepareSpot.from_json(record['command'])
        current = self._ledger.reservation_state(command.intent_id)
        if not current.filled_base <= command.cumulative_base_life <= prepared.quantity_base:
            raise StorageError('SPOT_FILL_CUMULATIVE_INVALID')
        old_ids = {trade_id for trade_id, row in fills.items() if row['intent_id'] == command.intent_id}
        if not old_ids.issubset({row.trade_id for row in command.fills}):
            raise StorageError('SPOT_FILL_SNAPSHOT_INCOMPLETE')
        received_ms = int(command.observed_at.timestamp() * 1000)
        started_ms = int(datetime.fromisoformat(record['prepared_at']).timestamp() * 1000)
        for row in command.fills:
            if (not started_ms <= row.fill_at_ms <= received_ms
                    or row.independent_observed_at_ms > received_ms
                    or abs(row.independent_value_at_ms - row.fill_at_ms) > self._fill_policy.max_reference_skew_ms
                    or abs(row.independent_observed_at_ms - row.fill_at_ms) > self._fill_policy.max_reference_skew_ms
                    or command.side == 'BUY' and row.price_usdt > prepared.limit_price_usdt
                    or command.side == 'SELL' and row.price_usdt < prepared.limit_price_usdt):
                raise StorageError('SPOT_FILL_OBSERVATION_UNQUALIFIED')
            stable = {'prepare_operation_id': command.prepare_operation_id, 'intent_id': command.intent_id,
                      'exchange_order_id': command.exchange_order_id, 'fill': row.json()}
            if row.trade_id in fills and fills[row.trade_id] != stable:
                raise StorageConflict('SPOT_FILL_IDENTITY_CONFLICT')
            fills[row.trade_id] = stable
        if self._accounting_policy is not None:
            self._capital_ledger(commands, fills, meta.resources.get('cashflows', {}))  # Causal preflight before writes.
        loss = sum((row.loss(command.side) for row in command.fills), Decimal('0'))
        operations[command.operation_id] = {'command': command.json(), 'fingerprint': command_hash,
                                            'state': 'FILLING', 'execution_loss_usdt': str(loss)}
        original_state = record['state']
        record['state'] = 'FILLING'
        try:
            self._publish_fills('fill-start:' + command.operation_id, owner, commands, operations,
                                thaw(meta.resources.get('fills', {})), dict(view.cut.components))
            self._ledger.apply_fills_snapshot(command.intent_id, tuple(
                (row.trade_id, row.quantity_base_life, row.price_usdt, row.fee_currency, row.signed_fee)
                for row in command.fills), command.cumulative_base_life, require_fees=True)
            if (self._ledger.reservation_state(command.intent_id).filled_base != command.cumulative_base_life
                    or any(not self._ledger.matches_recorded_fill(command.intent_id, row.trade_id, row.quantity_base_life,
                                                                  row.price_usdt, row.fee_currency, row.signed_fee)
                           for row in command.fills)):
                raise StorageError('SPOT_FILL_PUBLICATION_INCOMPLETE')
            for row in command.fills:
                self._loss.record('fill:' + row.trade_id, row.loss(command.side), session_id=command.session_id,
                                  at_utc=datetime.fromtimestamp(row.fill_at_ms / 1000, timezone.utc))
            self._subsidy.record_fill_floor(command.intent_id, loss)
            self._verify_fill_projection(commands, fills)
            if self._accounting_policy is not None:
                self._verify_capital_projection(commands, fills, meta.resources.get('cashflows', {}))
            verified = self._snapshot()
            record['state'] = original_state
            operations[command.operation_id]['state'] = 'APPLIED'
            self._publish_fills('fill-done:' + command.operation_id, owner, commands, operations, fills, dict(verified.cut.components))
            return self._fill_receipt(self.binding, operations[command.operation_id], 'APPLIED', False)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_FILL_APPLICATION_UNKNOWN') from None

    async def apply_fills(self, command, *, cut, owner):
        if not isinstance(command, FillSpot) or not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken):
            raise StorageError('SPOT_COMMAND_INVALID')
        self._assert_binding(cut.checkpoint.binding)
        try:
            return await self._call(self._apply_fills, command, cut, owner)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    def _require_accounting(self):
        if self._accounting_policy is None:
            raise UnsupportedBackend('SPOT_ACCOUNTING_POLICY_REQUIRED')

    @staticmethod
    def _cashflow_from_json(data):
        values = dict(data)
        values['signed_amount'] = Decimal(values['signed_amount'])
        values['independent_price_usdt'] = Decimal(values['independent_price_usdt'])
        values['observed_at'] = datetime.fromisoformat(values['observed_at'])
        return CashflowSpot(**values)

    def _validate_cashflow_value(self, command):
        policy = self._accounting_policy
        expected = next((row for row in policy.approved_cashflows if row.bill_id == command.bill_id), None)
        if expected is None or (expected.currency, expected.signed_amount, expected.transfer_at_ms) != (
                command.currency, command.signed_amount, command.transfer_at_ms):
            raise StorageError('SPOT_CASHFLOW_UNAPPROVED')
        receipt_ms = int(command.observed_at.timestamp() * 1000)
        if (command.transfer_at_ms > receipt_ms or command.reference_observed_at_ms > receipt_ms
                or command.observed_at > utc(self._clock())
                or abs(command.value_at_ms - command.transfer_at_ms) > self._fill_policy.max_reference_skew_ms
                or abs(command.reference_observed_at_ms - command.transfer_at_ms) > self._fill_policy.max_reference_skew_ms):
            raise StorageError('SPOT_CASHFLOW_VALUE_UNQUALIFIED')

    def _capital_ledger(self, commands, fills, cashflows):
        self._require_accounting()
        policy = self._accounting_policy
        capital = CapitalLedger(opening_life=policy.opening_base_life, opening_usdt=policy.opening_quote_usdt,
                                opening_independent_price_usdt=policy.opening_independent_price_usdt)
        timeline = []
        for trade_id, data in fills.items():
            row = SpotFillObservation.from_json(data['fill'])
            if row.fill_at_ms < int(policy.opened_at.timestamp() * 1000):
                raise StorageError('SPOT_CAPITAL_PREOPEN_FILL')
            side = PrepareSpot.from_json(commands[data['prepare_operation_id']]['command']).side
            timeline.append((row.fill_at_ms, 1, trade_id, (side, row)))
        for bill_id, data in cashflows.items():
            row = self._cashflow_from_json(data)
            if bill_id != row.bill_id:
                raise StorageError('SPOT_CAPITAL_CASHFLOW_IDENTITY_INVALID')
            self._validate_cashflow_value(row)
            timeline.append((row.transfer_at_ms, 0, bill_id, row))
        try:
            # Preserve existing attributor ordering: cashflow before fill at an
            # equal timestamp; lexical ID within kind. No invented clock order.
            for _, kind, event_id, event in sorted(timeline, key=lambda item: item[:3]):
                if kind == 0:
                    capital.record_asset_cashflow(event_id, event.currency, event.signed_amount,
                                                  independent_value_usdt=event.independent_price_usdt)
                else:
                    side, row = event
                    fee_cost = -row.signed_fee * (row.independent_price_usdt if row.fee_currency == 'LIFE' else Decimal('1'))
                    capital.record_fill(event_id, side, row.quantity_base_life, row.price_usdt, fee_cost,
                                        independent_value_usdt=row.independent_price_usdt,
                                        fee_currency=row.fee_currency, signed_fee=row.signed_fee)
            return capital
        except (ValueError, ArithmeticError):
            raise StorageError('SPOT_CAPITAL_CAUSAL_REPLAY_INVALID') from None

    def _verify_capital_projection(self, commands, fills, cashflows):
        native = {bill_id: (row['currency'], Decimal(row['signed_amount'])) for bill_id, row in cashflows.items()}
        capital = self._capital_ledger(commands, fills, cashflows)
        if (self._ledger.cashflow_events != native or self._ledger.joint_cash_events
                or capital.life_balance != self._ledger.life_balance or capital.usdt_balance != self._ledger.usdt_balance):
            raise StorageUnavailable('SPOT_CAPITAL_PROJECTION_UNVERIFIED')
        return capital

    @staticmethod
    def _cashflow_receipt(binding, row, state, replayed):
        return CashflowReceipt(binding, row['command']['operation_id'], row['command']['bill_id'],
                               state, replayed, AtomicScope.RECOVERABLE_SAGA)

    def _lookup_cashflow(self, operation_id):
        view = self._snapshot()
        meta = super()._read()
        row = meta.resources.get('cashflow_operations', {}).get(operation_id)
        if row is None:
            return None
        partial = row['state'] != 'APPLIED' or dict(view.cut.components) != dict(meta.resources['journal_hashes'])
        return self._cashflow_receipt(self.binding, row, 'RECOVERY_REQUIRED' if partial else 'APPLIED', True)

    async def lookup_cashflow(self, operation_id, binding):
        self._assert_binding(binding)
        identifier(operation_id)
        return await self._call(self._lookup_cashflow, operation_id)

    def _publish_cashflow(self, key, owner, operations, cashflows, hashes):
        meta = super()._read()
        return super()._commit(meta.cut, owner, key, (
            ('cashflow_operations', encode(operations)), ('cashflows', encode(cashflows)),
            ('journal_hashes', encode(hashes))), observation=True)

    def _apply_cashflow(self, command, cut, owner):
        self._require_accounting()
        view = self._snapshot()
        meta = self._ack_guard(view, owner)
        operations = thaw(meta.resources.get('cashflow_operations', {}))
        cashflows = thaw(meta.resources.get('cashflows', {}))
        command_hash = fingerprint({'command': command.json(), 'owner': owner.json()})
        previous = operations.get(command.operation_id)
        if previous is not None:
            if previous['fingerprint'] != command_hash:
                raise StorageConflict('SPOT_CASHFLOW_OPERATION_CONFLICT')
            if previous['state'] != 'APPLIED':
                raise StorageError('SPOT_CASHFLOW_RECONCILE_BEFORE_RETRY')
            return self._cashflow_receipt(self.binding, previous, 'APPLIED', True)
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        self._validate_cashflow_value(command)
        # Same bill under a new operation may only repeat the original facts;
        # local receipt time/operation are stored once, not economic identity.
        stable = command.json()
        if command.bill_id in cashflows:
            prior = cashflows[command.bill_id]
            if any(prior[key] != value for key, value in stable.items() if key not in ('operation_id', 'observed_at')):
                raise StorageConflict('SPOT_CASHFLOW_BILL_CONFLICT')
        else:
            cashflows[command.bill_id] = stable
        self._capital_ledger(meta.resources['commands'], meta.resources.get('fills', {}), cashflows)
        operations[command.operation_id] = {'command': command.json(), 'fingerprint': command_hash, 'state': 'CASHFLOWING'}
        try:
            self._publish_cashflow('cash-start:' + command.operation_id, owner, operations,
                                   thaw(meta.resources.get('cashflows', {})), dict(view.cut.components))
            self._ledger.record_cashflow(command.bill_id, command.currency, command.signed_amount)
            self._verify_capital_projection(meta.resources['commands'], meta.resources.get('fills', {}), cashflows)
            verified = self._snapshot()
            operations[command.operation_id]['state'] = 'APPLIED'
            self._publish_cashflow('cash-done:' + command.operation_id, owner, operations, cashflows, dict(verified.cut.components))
            return self._cashflow_receipt(self.binding, operations[command.operation_id], 'APPLIED', False)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_CASHFLOW_APPLICATION_UNKNOWN') from None

    async def apply_cashflow(self, command, *, cut, owner):
        if not isinstance(command, CashflowSpot) or not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken):
            raise StorageError('SPOT_COMMAND_INVALID')
        self._assert_binding(cut.checkpoint.binding)
        try:
            return await self._call(self._apply_cashflow, command, cut, owner)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    @staticmethod
    def _money_fingerprint(resources):
        return fingerprint({'fills': resources.get('fills', {}), 'cashflows': resources.get('cashflows', {})})

    def _nav_view(self, view, meta, row):
        values = {key: Decimal(value) for key, value in row['measurement'].items()}
        command = row['command']
        return SpotCapitalView(self.binding, view.cut, **values, independent_price_usdt=Decimal(command['independent_price_usdt']),
                               value_at_ms=command['value_at_ms'], observed_at=datetime.fromisoformat(command['observed_at']),
                               current=(row['financial_fingerprint'] == self._money_fingerprint(meta.resources)
                                        and not self._incomplete(meta.resources)
                                        and dict(view.cut.components) == dict(meta.resources['journal_hashes'])))

    def _capital(self):
        self._require_accounting()
        view = self._snapshot()
        meta = super()._read()
        if self._incomplete(meta.resources) or dict(view.cut.components) != dict(meta.resources['journal_hashes']):
            raise StorageUnavailable('SPOT_CAPITAL_RECOVERY_REQUIRED')
        latest = meta.resources.get('capital', {}).get('latest_operation_id')
        return None if latest is None else self._nav_view(view, meta, meta.resources['nav_operations'][latest])

    async def capital(self, binding):
        self._assert_binding(binding)
        return await self._call(self._capital)

    def _observe_nav(self, command, cut, owner):
        self._require_accounting()
        view = self._snapshot()
        meta = self._ack_guard(view, owner)
        operations = thaw(meta.resources.get('nav_operations', {}))
        command_hash = fingerprint({'command': command.json(), 'owner': owner.json()})
        previous = operations.get(command.operation_id)
        if previous is not None:
            if previous['fingerprint'] != command_hash:
                raise StorageConflict('SPOT_NAV_OPERATION_CONFLICT')
            return NavReceipt(self.binding, command.operation_id, self._nav_view(view, meta, previous), True, self.atomic_scope)
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        now = utc(self._clock())
        latest = meta.resources.get('capital', {}).get('latest_operation_id')
        if latest is not None:
            latest_command = operations[latest]['command']
            if (command.observed_at < datetime.fromisoformat(latest_command['observed_at'])
                    or command.value_at_ms < latest_command['value_at_ms']
                    or command.reference_observed_at_ms < latest_command['reference_observed_at_ms']):
                raise StorageError('SPOT_NAV_OBSERVATION_REGRESSION')
        if (command.observed_at > now or command.reference_observed_at_ms > int(command.observed_at.timestamp() * 1000)
                or abs(int(now.timestamp() * 1000) - command.value_at_ms) > self._fill_policy.max_reference_skew_ms
                or abs(int(now.timestamp() * 1000) - command.reference_observed_at_ms) > self._fill_policy.max_reference_skew_ms):
            raise StorageError('SPOT_NAV_VALUE_UNQUALIFIED')
        capital = self._verify_capital_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                                  meta.resources.get('cashflows', {}))
        capital.highwater_quote = max(capital.highwater_quote, Decimal(meta.resources.get('capital', {}).get('highwater_usdt', '0')))
        measurement = capital.measure(command.independent_price_usdt, source_kind='independent_market')
        values = {key: str(getattr(measurement, native)) for key, native in (
            ('nav_usdt', 'nav_quote'), ('adjusted_nav_usdt', 'adjusted_nav_quote'), ('highwater_usdt', 'highwater_quote'),
            ('drawdown_bps', 'drawdown_bps'), ('execution_loss_usdt', 'execution_loss_quote'),
            ('starting_inventory_pnl_usdt', 'starting_inventory_pnl_quote'))}
        values['net_cashflows_usdt'] = str(capital.net_cashflows_quote)
        operations[command.operation_id] = {'command': command.json(), 'fingerprint': command_hash, 'measurement': values,
                                            'financial_fingerprint': self._money_fingerprint(meta.resources)}
        try:
            super()._commit(meta.cut, owner, 'nav:' + command.operation_id, (
                ('nav_operations', encode(operations)),
                ('capital', encode({'latest_operation_id': command.operation_id, 'highwater_usdt': values['highwater_usdt']}))),
                observation=True)
            completed = self._snapshot()
            current_meta = super()._read()
            return NavReceipt(self.binding, command.operation_id,
                              self._nav_view(completed, current_meta, operations[command.operation_id]), False, self.atomic_scope)
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('SPOT_NAV_PUBLICATION_UNKNOWN') from None

    async def observe_nav(self, command, *, cut, owner):
        if not isinstance(command, ObserveSpotNAV) or not isinstance(cut, FinancialCut) or not isinstance(owner, OwnerToken):
            raise StorageError('SPOT_COMMAND_INVALID')
        self._assert_binding(cut.checkpoint.binding)
        try:
            return await self._call(self._observe_nav, command, cut, owner)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    def _release_account_lock(self):
        if self._account_lock is not None:
            self._account_lock.release()

    async def close(self):
        self._closed = True
        if self._cleanup_future is None:
            # FIFO: release runs on the owning worker after every admitted command.
            # There is exactly one cleanup slot; cancelled close never hands off early.
            self._cleanup_future = asyncio.get_running_loop().run_in_executor(
                self._executor, self._release_account_lock)
            self._cleanup_future.add_done_callback(
                lambda result: result.exception() if not result.cancelled() else None)
            self._executor.shutdown(wait=False, cancel_futures=False)
        await asyncio.shield(self._cleanup_future)


async def open_legacy_spot(files, *, binding, limits, risk_limits, subsidy_policy, loss_policy,
                           config_version, account_lock_root, create_binding=False, owner=None,
                           clock=lambda: datetime.now(timezone.utc), fill_policy=None, accounting_policy=None, terminal_policy=None):
    store = LegacySpotStore(files, binding, limits, risk_limits, subsidy_policy, loss_policy,
                            config_version, account_lock_root, clock, fill_policy, accounting_policy, terminal_policy)
    try:
        await store._call(store._initialize_spot, create_binding, owner)
        return store
    except BaseException:
        await store.close()
        raise
