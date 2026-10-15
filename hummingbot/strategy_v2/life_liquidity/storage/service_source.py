"""Configured file-service ancestry; no source retirement or target import.

Explicit installed participants supply verification and paths. No directory
discovery, omitted optional component, raw SQLite copy or archive adoption.
"""
import hashlib
from dataclasses import dataclass
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.storage.configuration_handoff import FinancialConfigurationSource
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, StorageUnavailable, fingerprint, thaw
from hummingbot.strategy_v2.life_liquidity.storage.recorder_source import RecorderSource, capture_limits


@dataclass(frozen=True)
class FileServiceSource:
    core: FinancialConfigurationSource
    service_json: str
    ancillary_images: tuple[tuple[str, bytes], ...]
    recorder: RecorderSource

    scope = 'FILE_SERVICE_SCOPED'

    def __post_init__(self):
        if (type(self.core) is not FinancialConfigurationSource or type(self.recorder) is not RecorderSource
                or type(self.service_json) is not str or type(self.ancillary_images) is not tuple
                or self.recorder.cut != self.core.cut.checkpoint
                or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str or type(row[1]) is not bytes
                       for row in self.ancillary_images)):
            raise StorageError('SERVICE_HANDOFF_SOURCE_INVALID')
        names = tuple(name for name, _ in self.ancillary_images)
        if names != tuple(sorted(set(names))):
            raise StorageError('SERVICE_HANDOFF_SOURCE_INVALID')

    @property
    def content_digest(self):
        return fingerprint({'scope': self.scope, 'core': self.core.content_digest,
                            'service_sha256': hashlib.sha256(self.service_json.encode()).hexdigest(),
                            'recorder': self.recorder.content_digest,
                            'ancillary': {name: hashlib.sha256(raw).hexdigest() for name, raw in self.ancillary_images}})


class FileServiceSourceCapture:
    def __init__(self, service):
        self.service, self.store = service, service.store

    def assert_quiescent(self, source, facts):
        s = self.service
        view = self.store._snapshot()
        if (source.core.unresolved_intents or facts.accepted_wire_ids or facts.runner_wire_ids
                or view.balance.reserved_base_life != 0 or view.balance.reserved_quote_usdt != 0
                or view.session.rollback_latched or s.spot._pending or s._pending_events or s._action_events
                or s.quotes is not None and s.quotes.native.action_journal.active_records()
                or any(row['state'] not in ('TERMINAL', 'REJECTED') for app in (s.inventory, s.exit) if app is not None
                       for row in self.store._read().resources.get(app.children_key, {}).values())):
            raise StorageError('HANDOFF_SOURCE_NOT_QUIESCENT')
        if s.joint is not None:
            capital = s.joint._recovery.capital
            account = capital.qualified_snapshot()
            if (capital.claim_ids() or account.long_base != 0 or account.short_base != 0
                    or account.funding_liability_quote != 0 or s.swap._pending or s.swap._proposals
                    or any(row.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND') for row in s.swap.sender.wal.all_records())):
                raise StorageError('HANDOFF_SOURCE_NOT_QUIESCENT')

    def _participants(self):
        s, c, cfg = self.service, self.service.controller, self.service.config
        root, result = Path(cfg.state_dir), {}
        repo = s.reporting.repository
        if (repo.store is not self.store or repo.binding != self.store.binding or repo.controller_id != c.config.id
                or repo._database != Path(cfg.recorder_path).absolute()
                or Path(repo._recorder.sql_manager.engine.url.database).absolute() != repo._database):
            raise StorageError('SERVICE_HANDOFF_RECORDER_BINDING_MISMATCH')

        def add(name, native, filename, verify):
            if native.path.absolute() != (root / filename).absolute():
                raise StorageError('SERVICE_HANDOFF_PARTICIPANT_PATH_MISMATCH')
            if getattr(native, 'archive', None) is not None or getattr(native, '_archive', None) is not None:
                raise StorageError('SERVICE_HANDOFF_ARCHIVE_UNSUPPORTED')
            result[name] = (native.path, verify)

        def journal(name, native, filename):
            def verify():
                with native.inspect():
                    pass
            add(name, native, filename, verify)

        for name, configured in (('quotes', cfg.quotes), ('risk', cfg.risk), ('inventory', cfg.inventory),
                                 ('exit', cfg.exit), ('joint', cfg.joint), ('hedge', cfg.hedge_policy)):
            if (getattr(s, name) is None) != (configured is None):
                raise StorageError('SERVICE_HANDOFF_PARTICIPANT_MISMATCH')
        if (c._carry_monitor is None) != (cfg.carry_policy is None):
            raise StorageError('SERVICE_HANDOFF_PARTICIPANT_MISMATCH')
        if (s.swap is None) != (cfg.joint is None) or (s.products is None) != (cfg.carry_policy is None):
            raise StorageError('SERVICE_HANDOFF_PARTICIPANT_MISMATCH')
        if s.quotes is not None:
            s.quotes._verify_policy()
            if (s.quotes.controller is not c or s.quotes.native is not c._quote_action_planner
                    or thaw(self.store._read().resources.get('quote_service_policy')) != cfg.quotes.model_dump(mode='json')):
                raise StorageError('SERVICE_QUOTE_POLICY_CHANGED')
            add('quote_actions', s.quotes.native.action_journal, 'quote_actions.json', s.quotes.native.action_journal.verified_records)
        if s.risk is not None:
            s.risk._guard()
            add('safety', s.risk.gate, 'safety.json', s.risk.gate.journal_verified)
            if s.risk.probe is not None:
                journal('recovery_probe', s.risk.probe.journal, 'recovery_probe.json')
            if s.risk.markout_probe is not None:
                add('markout_probe', s.risk.markout_probe, 'markout_probe.json', s.risk.markout_probe._policy_verified)
        for app in (s.inventory, s.exit):
            if app is not None:
                app._read()  # Their money/children/policy histories live in core.
        if s.joint is not None:
            s.joint._guard(observation=True)
            if s.swap is None or s.swap.repository.joint is not s.joint:
                raise StorageError('SERVICE_HANDOFF_PARTICIPANT_MISMATCH')
            s.swap.repository._guard(s.spot.owner, observation=True)
            recovery, sender = s.joint._recovery, s.swap.sender
            journal('shared_capital', recovery.capital.journal, 'shared_capital.json')
            journal('swap_actions', sender.journal, 'swap_actions.json')
            journal('joint_recovery', recovery.journal, 'joint_recovery.json')
            add('swap_wal', sender.wal, 'swap_wal.json', sender.wal.assert_verified)
            add('requests', sender.request_budget, 'requests.json', sender.request_budget._read)
            if s.hedge is not None:
                if (s.hedge.native is not c._hedge_coordinator
                        or self.store._read().resources.get('hedge_application_policy') != s.hedge._policy()):
                    raise StorageError('HEDGE_APPLICATION_POLICY_MISMATCH')
                s.hedge.native._validate_state(s.hedge.native.journal._read())
                journal('hedge', s.hedge.native.journal, 'hedge_coordinator.json')
            if cfg.carry_policy is not None:
                if s.products._carry is not c._carry_monitor or s.products._capital is not c._shared_capital_authority:
                    raise StorageError('PRODUCT_CONTROLLER_BINDING_MISMATCH')
                s.products._native_cuts()
                journal('carry', c._carry_monitor.journal, 'carry.json')
        paths = [path.resolve() for path in self.store._files.__dict__.values() if path is not None]
        paths.extend(path.resolve() for path, _ in result.values())
        paths.append(s.reporting.repository._database.resolve())
        if len(set(paths)) != len(paths):
            raise StorageError('SERVICE_HANDOFF_PARTICIPANT_PATH_COLLISION')
        return result

    @staticmethod
    def _bytes(path, remaining):
        if not path.is_file() or path.is_symlink():
            raise StorageUnavailable('SERVICE_HANDOFF_PARTICIPANT_UNAVAILABLE')
        with path.open('rb') as handle:
            value = handle.read(max(0, remaining) + 1)
        if len(value) > remaining:
            raise StorageUnavailable('HANDOFF_CAPTURE_CAPACITY_EXCEEDED')
        return value

    def capture(self, snapshot, max_bytes, max_rows):
        self.store.assert_worker_thread()
        capture_limits(max_bytes, max_rows)
        try:
            for native in (self.store._wal, self.store._ledger, self.store._loss):
                if getattr(native, '_archive', None) is not None:
                    raise StorageError('SERVICE_HANDOFF_ARCHIVE_UNSUPPORTED')
            core = self.store._configuration_handoff_source(snapshot.facts, snapshot.cut)
            service_json = self.service.config.model_dump_json()
            total = len(core.checkpoint_bytes) + sum(len(raw) for _, raw in core.journal_images) + len(service_json.encode())
            if total >= max_bytes:
                raise StorageUnavailable('HANDOFF_CAPTURE_CAPACITY_EXCEEDED')
            participants = self._participants()
            images = []
            for name, (path, verify) in sorted(participants.items()):
                raw = self._bytes(path, min(max_bytes - total, self.store.limits.max_checkpoint_bytes))
                if verify() is False:
                    raise StorageUnavailable('SERVICE_HANDOFF_PARTICIPANT_UNVERIFIED')
                if self._bytes(path, len(raw)) != raw:
                    raise StorageUnavailable('SERVICE_HANDOFF_SOURCE_CHANGED')
                images.append((name, raw))
                total += len(raw)
            recorder = self.service.reporting.repository._handoff_source(max_bytes - total, max_rows)
            # Both SQL reads use stable transactions. A second read catches
            # scoped changes from another connection across graph collection.
            again = self.service.reporting.repository._handoff_source(max_bytes - total, max_rows)
            if again != recorder:
                raise StorageUnavailable('SERVICE_HANDOFF_SOURCE_CHANGED')
            for name, raw in images:
                if self._bytes(participants[name][0], len(raw)) != raw:
                    raise StorageUnavailable('SERVICE_HANDOFF_SOURCE_CHANGED')
            if {key: path for key, (path, _) in self._participants().items()} != {
                    key: path for key, (path, _) in participants.items()}:
                raise StorageUnavailable('SERVICE_HANDOFF_SOURCE_CHANGED')
            self.store._validate_configuration_handoff_source(core, snapshot.facts, snapshot.cut)
            if self.service.config.model_dump_json() != service_json:
                raise StorageUnavailable('SERVICE_HANDOFF_SOURCE_CHANGED')
            return FileServiceSource(core, service_json, tuple(images), recorder)
        except StorageError:
            raise
        except (ValueError, TypeError, KeyError, OSError):
            raise StorageUnavailable('SERVICE_HANDOFF_SOURCE_UNVERIFIED') from None
