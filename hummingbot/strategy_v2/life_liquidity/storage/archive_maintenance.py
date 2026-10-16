"""Signed exact-source file normalization on an exclusive, nontrading worker.

Only an already frozen, signed FILE_SERVICE_SCOPED image is a repair origin.
Archive segments are verified by the existing native readers. No discovered
file, inferred balance, new policy, deadline or monetary record can be adopted.
The coordinator and its consumed-grant identity are never rewritten.
"""
import asyncio
import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.account_lock import AccountRiskPoolLock
from hummingbot.strategy_v2.life_liquidity.checkpoint_archive import ArchivePolicy, CheckpointArchive
from hummingbot.strategy_v2.life_liquidity.policy_state import _freeze
from hummingbot.strategy_v2.life_liquidity.session import SessionStore
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    digest,
    encode,
    identifier,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.file import FileCheckpointStore
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.handoff import ConfigurationHandoffReceipt
from hummingbot.strategy_v2.life_liquidity.storage.handoff_artifact import read_bytes, sync_dir
from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotStore
from hummingbot.strategy_v2.life_liquidity.storage.target_import import ANCILLARY_NAMES, publish_exact, target_files


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ArchiveRepair:
    operation_id: str
    handoff_operation_id: str
    source_digest: str
    component: str
    before_sha256: str | None
    source_root_sha256: str
    archive_policy_json: str | None

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.handoff_operation_id)
        identifier(self.component)
        digest(self.source_digest)
        digest(self.source_root_sha256)
        if self.before_sha256 is not None:
            digest(self.before_sha256)
        if len(self.operation_id) > 96:
            raise StorageError('SOURCE_REPAIR_INVALID')
        if self.archive_policy_json is not None:
            try:
                if type(self.archive_policy_json) is not str or len(self.archive_policy_json.encode()) > 8192:
                    raise ValueError()
                value = json.loads(self.archive_policy_json)
                policy = ArchivePolicy(**{**value, 'paths': tuple(tuple(p) for p in value['paths'])})
                object.__setattr__(self, 'archive_policy_json', encode(policy.json()).decode())
            except (TypeError, KeyError, ValueError):
                raise StorageError('SOURCE_REPAIR_ARCHIVE_POLICY_INVALID') from None

    def json(self):
        return {'kind': 'NORMALIZE_FROZEN_SOURCE', **self.__dict__}


def restore_exact(path, before, after, guard):
    """Compare, fsync, requalify and replace one exact original image."""
    descriptor, temporary = tempfile.mkstemp(prefix='.normalize-', dir=path.parent)
    attempted = False
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(after)
            handle.flush()
            os.fsync(handle.fileno())
        guard()
        actual = None if not path.exists() and not path.is_symlink() else read_bytes(path, max(len(before or b''), len(after)))
        if actual != before:
            raise StorageConflict('SOURCE_REPAIR_NATIVE_CHANGED')
        attempted = True
        os.replace(temporary, path)
        sync_dir(path.parent)
    except StorageError:
        raise
    except BaseException:
        if attempted:
            raise CommitUnknown('SOURCE_REPAIR_PUBLICATION_UNKNOWN') from None
        raise
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


class FileArchiveMaintenance(LegacySpotStore):
    """No controller, recorder, SDK listener or activation API is installed."""

    def _before_call(self, function, args):
        allowed = (self._initialize_maintenance, self._repair_cut, self._repair)
        if getattr(function, '__self__', None) is not self or function not in allowed:
            raise StorageError('SOURCE_REPAIR_MAINTENANCE_ONLY')

    def _initialize_maintenance(self):
        self._account_lock = AccountRiskPoolLock(self.binding.account_uid, self._account_lock_root)
        self._account_lock.acquire()
        # Deliberately avoid ordinary constructors that cannot decode archives.
        # The coordinator/control signatures are still audited by normal _read.
        self._sessions = SessionStore(self._files.session)
        FileCheckpointStore._initialize(self, False, None, None)
        meta = self._read()
        if meta.resources.get('configuration_handoff', {}).get('active_operation_id') is None:
            raise StorageError('SOURCE_REPAIR_SIGNED_ORIGIN_REQUIRED')
        self._read_handoff_source()

    def _repair_cut(self):
        images = {}
        for name, path in self._files.journals().items():
            images[name] = b'' if not path.exists() and not path.is_symlink() else read_bytes(path, self.limits.max_checkpoint_bytes)
        if sum(map(len, images.values())) > self.limits.max_checkpoint_bytes:
            raise StorageUnavailable('SOURCE_REPAIR_CAPACITY_EXCEEDED')
        return FinancialCut(self._read().cut, tuple(sorted(self._hashes(images).items())))

    async def repair_cut(self):
        return await self._call(self._repair_cut)

    def _repair_history(self, command, approval, facts, cut):
        from datetime import datetime

        from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorApproval
        from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
        folder = self._path.parent / 'source_repairs'
        if folder.is_symlink() or folder.exists() and not folder.is_dir():
            raise StorageUnavailable('SOURCE_REPAIR_HISTORY_UNSAFE')
        destination = folder / (sha(command.operation_id.encode()) + '.json')
        raw = encode({'command': command.json(), 'approval': approval.body(), 'signature': approval.signature,
                      'cut': cut.json(), 'facts': facts.json()})
        if len(raw) > self.limits.max_checkpoint_bytes:
            raise StorageError('SOURCE_REPAIR_HISTORY_CAPACITY_EXCEEDED')
        if destination.exists() or destination.is_symlink():
            raw = read_bytes(destination, self.limits.max_checkpoint_bytes)
            try:
                entry = json.loads(raw)
                if entry['command'] != command.json() or entry['cut'] != cut.json():
                    raise StorageConflict('SOURCE_REPAIR_OPERATION_CONFLICT')
                initial = OperatorApproval(**{**entry['approval'], 'binding': self.binding,
                                              'issued_at': datetime.fromisoformat(entry['approval']['issued_at']),
                                              'expires_at': datetime.fromisoformat(entry['approval']['expires_at']), 'signature': entry['signature']})
                self._operator_keys.verify(initial, self.binding, 'IMPORT', command.json(), cut,
                                           SpotFinancialRecoveryFacts.from_json(entry['facts']), initial.issued_at)
            except (ValueError, KeyError, TypeError):
                raise StorageUnavailable('SOURCE_REPAIR_HISTORY_UNVERIFIED') from None
        elif folder.exists():
            for count, entry in enumerate(folder.iterdir(), 1):
                if count >= self.limits.max_operations or not entry.is_file() or entry.is_symlink():
                    raise StorageUnavailable('SOURCE_REPAIR_HISTORY_CAPACITY_EXCEEDED')
        return folder, destination, raw

    def _repair(self, command, approval, facts, cut):
        from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorApproval
        from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
        if (type(command) is not ArchiveRepair or type(approval) is not OperatorApproval
                or type(facts) is not SpotFinancialRecoveryFacts or type(cut) is not FinancialCut):
            raise StorageError('SOURCE_REPAIR_INVALID')
        self._assert_binding(facts.binding)
        root = read_bytes(self._path, self.limits.max_checkpoint_bytes)
        source = self._read_handoff_source()
        head = self._handoff_state()
        if (head.active.operation_id != command.handoff_operation_id or source.content_digest != command.source_digest
                or sha(root) != command.source_root_sha256):
            raise StorageConflict('SOURCE_REPAIR_ORIGIN_CHANGED')
        images = {**dict(source.core.journal_images), **dict(source.ancillary_images)}
        if command.component not in images:
            raise StorageError('SOURCE_REPAIR_COMPONENT_UNDECLARED')
        expected = images[command.component]
        path = self._files.journals().get(command.component)
        if path is None:
            path = self._path.parent / ANCILLARY_NAMES.get(command.component, command.component + '.json')
        if path.parent.resolve() != path.parent or path.is_symlink():
            raise StorageError('SOURCE_REPAIR_PATH_UNSAFE')
        before = None if not path.exists() else read_bytes(path, self.limits.max_checkpoint_bytes)
        replay = before == expected
        current = self._repair_cut()
        parts = dict(current.components)
        if replay and command.component in parts:
            parts[command.component] = sha(b'') if command.before_sha256 is None else command.before_sha256
        if cut != FinancialCut(current.checkpoint, tuple(sorted(parts.items()))):
            raise StorageConflict('SOURCE_REPAIR_CUT_CHANGED')
        now = utc(self._clock())
        # Completed readback verifies the original signature historically. It
        # performs no write and never refreshes a session or activation grant.
        self._operator_keys.verify(approval, self.binding, 'IMPORT', command.json(), cut, facts,
                                   approval.issued_at if replay else now)
        reference = source.core.facts
        if (not all((facts.order_scope_complete, facts.bill_scope_complete, facts.runner_scope_complete))
                or facts.accepted_wire_ids or facts.runner_wire_ids or facts.liability_base_life or facts.liability_quote_usdt
                or any(getattr(facts, key) != getattr(reference, key) for key in (
                    'account_base_life', 'account_quote_usdt', 'account_trade_ids', 'account_cashflow_ids'))):
            raise StorageError('SOURCE_REPAIR_FACTS_UNQUALIFIED')
        folder, receipt_path, authorization = self._repair_history(command, approval, facts, cut)
        if replay:
            return ConfigurationHandoffReceipt(self.binding, command.operation_id, head.sequence, 'SOURCE_NORMALIZED', True)
        if (None if before is None else sha(before)) != command.before_sha256:
            raise StorageConflict('SOURCE_REPAIR_NATIVE_CHANGED')
        archive = None
        if command.archive_policy_json is not None:
            try:
                value = json.loads(command.archive_policy_json)
                policy = ArchivePolicy(**{**value, 'paths': tuple(tuple(p) for p in value['paths'])})
                if (max(policy.max_active_bytes, policy.max_archive_bytes, policy.max_segment_bytes) > head.active.max_bytes
                        or policy.max_segments > self.limits.max_operations):
                    raise ValueError('ARCHIVE_CAPACITY_EXCEEDED')
                if command.component in ('wal', 'swap_wal', 'reservations'):
                    from hummingbot.strategy_v2.life_liquidity.journal_archive import JournalArchive
                    kind = 'wal' if command.component in ('wal', 'swap_wal') else 'reservations'
                    limits = None if kind == 'wal' else json.loads(expected)['limits']
                    archive = JournalArchive(path, policy, kind=kind, limits=limits)
                    archive.archive.assert_capacity()
                    logical = archive.read()[1]
                else:
                    original = json.loads(expected)
                    if set(original) != {'schema_version', 'policy', 'state'}:
                        raise ValueError('ARCHIVE_COMPONENT_UNSUPPORTED')
                    archive = CheckpointArchive(path, policy)
                    archive.assert_capacity()
                    logical = {'schema_version': 1, 'policy': original['policy'], 'state': archive.read(before, freeze=_freeze)}
                    if json.loads(before)['policy'] != original['policy']:
                        raise ValueError('ARCHIVE_POLICY_MISMATCH')
                if encode(logical) != encode(json.loads(expected)):
                    raise ValueError('ARCHIVE_LOGICAL_ORIGIN_MISMATCH')
            except (ValueError, KeyError, TypeError, OSError):
                raise StorageUnavailable('SOURCE_REPAIR_ARCHIVE_UNVERIFIED') from None

        def guarded():
            at = utc(self._clock())
            self._operator_keys.verify(approval, self.binding, 'IMPORT', command.json(), cut, facts, at)
            if not 0 <= (at - facts.observed_at).total_seconds() * 1000 <= self._recovery_policy.max_fact_age_ms:
                raise StorageError('SOURCE_REPAIR_FACTS_EXPIRED')
            if read_bytes(self._path, self.limits.max_checkpoint_bytes) != root or self._repair_cut() != cut:
                raise StorageConflict('SOURCE_REPAIR_ROOT_OR_CUT_CHANGED')
            if archive is not None:
                if isinstance(archive, CheckpointArchive):
                    archive.assert_capacity()
                    archive.read(read_bytes(path, self.limits.max_checkpoint_bytes), freeze=_freeze)
                else:
                    archive.archive.assert_capacity()
                    archive.read()
        # Durable authorization history, independent of money and activation.
        # Retain the first grant byte-for-byte; a fresh grant may finish an
        # expired interrupted attempt only for the same command and source cut.
        folder.mkdir(mode=0o700, exist_ok=True)
        sync_dir(folder.parent)
        guarded()
        publish_exact(receipt_path, authorization)
        try:
            restore_exact(path, before, expected, guarded)
        except CommitUnknown:
            self._uncertain = True
            raise
        return ConfigurationHandoffReceipt(self.binding, command.operation_id, head.sequence, 'SOURCE_NORMALIZED', False)

    async def repair(self, command, *, approval, facts, cut):
        try:
            return await self._call(self._repair, command, approval, facts, cut)
        except asyncio.CancelledError:
            self._uncertain = True
            raise


async def open_archive_maintenance(config, *, clock):
    """Explicit maintenance entry point; never bootstrap or enable trading."""
    if config.control is None or config.recovery_policy is None:
        raise StorageError('SOURCE_REPAIR_SIGNED_POLICY_REQUIRED')
    store = FileArchiveMaintenance(target_files(config), config.controller.storage_binding, config.file_limits, config.risk_limits,
                                   config.subsidy_policy, config.loss_policy, config.config_version, config.account_lock_root, clock,
                                   config.fill_policy, config.accounting_policy, config.terminal_policy, config.recovery_policy,
                                   config.settlement_policy, config.control.verifier(),
                                   exit_subsidy_policy=None if config.exit is None else config.exit.budget)
    try:
        await store._call(store._initialize_maintenance)
        return store
    except BaseException:
        await store.close()
        raise
