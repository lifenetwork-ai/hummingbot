"""Offline spot ports on existing journals; multi-file work has recovery barriers.

No exchange I/O, hold release, blind repair, grant issuance or controller wiring.
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
from hummingbot.strategy_v2.life_liquidity.economics import SubsidyBudgetLedger
from hummingbot.strategy_v2.life_liquidity.loss_budget import LossBudgetLedger
from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger, RiskLimits, SpotIntent
from hummingbot.strategy_v2.life_liquidity.session import SessionStore
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL
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
    AttemptState,
    BudgetPolicy,
    FinancialCut,
    PrepareReceipt,
    PrepareSpot,
    SpotAttempt,
    SpotBalance,
    SpotBudget,
    SpotReservation,
    SpotSession,
    SpotSnapshot,
)


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


class LegacySpotStore(FileCheckpointStore):
    atomic_scope = AtomicScope.RECOVERABLE_SAGA

    def __init__(self, files, binding, limits, risk_limits, subsidy_policy, loss_policy,
                 config_version, account_lock_root, clock):
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
        self._policy['legacy_spot'] = {
            'paths': {key: str(value) for key, value in files.journals().items()},
            'account_lock_root': str(Path(account_lock_root)),
            'risk': {key: str(value) for key, value in risk_limits.__dict__.items()},
            'subsidy': subsidy_policy.json(), 'execution_loss': loss_policy.json(), 'config_version': config_version}
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
            commands = [PrepareSpot.from_json(item['command']) for item in meta.resources['commands'].values()]
            fee_base = sum((command.fee_buffer_base_life for command in commands), Decimal('0'))
            fee_quote = sum((command.fee_buffer_quote_usdt for command in commands), Decimal('0'))
            partial = (hashes != dict(meta.resources['journal_hashes'])
                       or any(item['state'] in ('PREPARING', 'ARMING') for item in meta.resources['commands'].values()))
            return SpotSnapshot(
                FinancialCut(meta.cut, tuple(sorted(hashes.items()))),
                SpotSession(record.session_id, record.epoch, record.config_version, record.started_at, record.expires_at,
                            journal.state, journal.last_seen_at, journal.clock_rollback_latched, record.anchors),
                SpotBalance(self._ledger.life_balance, self._ledger.usdt_balance, self._ledger.reserved_life + fee_base,
                            self._ledger.reserved_usdt + fee_quote, self._ledger.life_balance - self._ledger.reserved_life - fee_base,
                            self._ledger.usdt_balance - self._ledger.reserved_usdt - fee_quote, fee_base, fee_quote),
                SpotBudget(subsidy.session_committed_quote, subsidy.day_committed_quote, subsidy.campaign_committed_quote,
                           subsidy.available_quote, loss.session_loss_quote, loss.day_loss_quote,
                           loss.campaign_loss_quote, loss.exhausted),
                partial or self._cold_unarmed or any(row.state == 'SEND_UNKNOWN' for row in self._wal.active_records()))
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
        partial = record['state'] in ('PREPARING', 'ARMING') or dict(view.cut.components) != dict(
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
                           clock=lambda: datetime.now(timezone.utc)):
    store = LegacySpotStore(files, binding, limits, risk_limits, subsidy_policy, loss_policy,
                            config_version, account_lock_root, clock)
    try:
        await store._call(store._initialize_spot, create_binding, owner)
        return store
    except BaseException:
        await store.close()
        raise
