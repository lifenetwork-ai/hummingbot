"""Signed, recoverable file target import; original source remains retired.

Ancestry is retained byte for byte. Historical source data never reissues an
activation grant. The account lock excludes concurrent file runtimes on one host.
"""
import asyncio
import base64
import copy
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.session import SessionJournal
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    CommitUnknown,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    digest,
    encode,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.handoff import (
    ConfigurationHandoffReceipt,
    session_record,
    validate_target,
)
from hummingbot.strategy_v2.life_liquidity.storage.handoff_artifact import (
    pack_source,
    read_bytes,
    sync_dir,
    unpack_source,
)


@dataclass(frozen=True)
class TargetImportManifest:
    operation_id: str
    handoff_operation_id: str
    source_digest: str
    target_service_json: str

    def __post_init__(self):
        from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig
        identifier(self.operation_id)
        identifier(self.handoff_operation_id)
        digest(self.source_digest)
        if len(self.operation_id) > 96 or type(self.target_service_json) is not str or len(self.target_service_json.encode()) > 65536:
            raise StorageError('TARGET_MANIFEST_INVALID')
        try:
            object.__setattr__(self, 'target_service_json', FileServiceConfig.model_validate_json(self.target_service_json).model_dump_json())
        except (ValueError, TypeError):
            raise StorageError('TARGET_MANIFEST_INVALID') from None

    @property
    def config(self):
        from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig
        return FileServiceConfig.model_validate_json(self.target_service_json)

    def json(self):
        return dict(self.__dict__)

    @property
    def content_digest(self):
        return fingerprint(self.json())


@dataclass(frozen=True)
class ImportConfigurationTarget:
    manifest: TargetImportManifest
    source_root_sha256: str

    def __post_init__(self):
        if type(self.manifest) is not TargetImportManifest:
            raise StorageError('TARGET_MANIFEST_INVALID')
        digest(self.source_root_sha256)

    def json(self):
        return {'kind': 'IMPORT_CONFIGURATION_TARGET', 'manifest': self.manifest.json(), 'source_root_sha256': self.source_root_sha256}

    @classmethod
    def from_json(cls, value):
        if set(value) != {'kind', 'manifest', 'source_root_sha256'} or value['kind'] != 'IMPORT_CONFIGURATION_TARGET':
            raise StorageError('TARGET_IMPORT_INVALID')
        return cls(TargetImportManifest(**value['manifest']), value['source_root_sha256'])


def validate_manifest(manifest, grant, source):
    from hummingbot.strategy_v2.life_liquidity.config import StrategyConfig
    from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig
    from hummingbot.strategy_v2.life_liquidity.storage.service_source import FileServiceSource
    if grant.source_format != 'FILE_SERVICE_SCOPED' or type(source) is not FileServiceSource:
        raise StorageError('POSTGRES_CUTOVER_CONSUMPTION_REQUIRED')
    original = FileServiceConfig.model_validate_json(source.service_json)
    target, session = manifest.config, session_record(grant.target_session_json)
    validate_target(grant, source.core.pending, source.core.cut.checkpoint.binding)
    root, old_root = Path(target.state_dir), Path(original.state_dir).resolve()
    owner = target.owner
    if (manifest.handoff_operation_id != grant.operation_id or manifest.source_digest != source.content_digest
            or target.controller.storage_binding != grant.target_binding
            or owner.session_id != UUID(session.session_id)
            or owner.epoch != session.epoch or owner.valid_from != session.started_at or owner.expires_at != session.expires_at
            or owner.process_id == grant.owner.process_id or owner.generation == grant.owner.generation
            or root.resolve() != root or root.is_relative_to(old_root) or old_root.is_relative_to(root)):
        raise StorageError('TARGET_MANIFEST_SCOPE_INVALID')
    candidate = StrategyConfig.model_validate_json(source.core.pending.candidate_json)
    expected_controller = original.controller.model_copy(update={'strategy': candidate, 'storage_binding': grant.target_binding,
                                                                 'recovery_state_dir': None if original.controller.recovery_state_dir is None else str(root)})
    expected = original.model_copy(update={'controller': expected_controller, 'state_dir': str(root), 'owner': owner,
                                           'config_version': candidate.config_version})
    if target.model_dump_json() != expected.model_dump_json():
        raise StorageError('TARGET_MANIFEST_POLICY_INVALID')
    return target, session


ANCILLARY_NAMES = {'hedge': 'hedge_coordinator.json'}


def native_target_images(source, config):
    """Change declared policy paths/strategy only; monetary state stays exact."""
    original = json.loads(source.service_json)
    before = original['controller']['strategy']
    after = config.controller.strategy.model_dump(mode='json')
    roots = {original['state_dir'], str(Path(original['state_dir']).resolve())}

    def relocated(value):
        if type(value) is dict:
            if value == before:
                return after
            if value == original['controller']['storage_binding']:
                return config.controller.storage_binding.json()
            return {key: relocated(item) for key, item in value.items()}
        if type(value) is list:
            return [relocated(item) for item in value]
        if type(value) is str:
            for root in roots:
                if value == root or value.startswith(root + '/'):
                    return config.state_dir + value[len(root):]
        return value

    images, policies = {}, {}
    for name, raw in source.ancillary_images:
        value = json.loads(raw)
        if set(value) == {'schema_version', 'policy', 'state'} or name == 'markout_probe' and set(value) == {'schema_version', 'policy'}:
            policy = relocated(value['policy'])
            policies[name] = policy
            if policy != value['policy']:
                value['policy'] = policy
                raw = encode(value)
        images[name] = raw
    return images, policies


def native_resource_policies(resources, config, policies):
    from hummingbot.strategy_v2.life_liquidity.storage.products import _json
    from hummingbot.strategy_v2.life_liquidity.storage.recorder_configuration import financial_controller_config
    if config.joint is None:
        return {}
    joint = {**resources['joint_repository_policy'], 'binding': config.controller.storage_binding.json(),
             'configuration': fingerprint(financial_controller_config(config.controller)),
             'native': fingerprint(policies['joint_recovery']),
             'products': {key: None if key not in policies else fingerprint(policies[key]) for key in ('hedge', 'carry')}}
    result = {'joint_repository_policy': joint,
              'swap_repository_policy': {'joint': joint, 'sender': policies['swap_actions']},
              'shared_spot_policy': {'joint': joint, 'capital': policies['shared_capital']}}
    if config.hedge_policy is not None:
        result['hedge_application_policy'] = fingerprint(policies['hedge'])
    if 'product_policy' in resources:
        names = {'capital': ('shared_capital', config.capital_policy), 'hedge': ('hedge', config.hedge_policy),
                 'carry': ('carry', config.carry_policy)}
        result['product_policy'] = {key: fingerprint({'journal': policies[names[key][0]], 'configured': _json(asdict(names[key][1]))})
                                    for key in resources['product_policy']}
    return result


def target_files(config):
    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotFiles
    root = Path(config.state_dir)
    return LegacySpotFiles(root / 'binding.json', root / 'session.json', root / 'wal.json', root / 'reservations.json',
                           root / 'subsidy.json', root / 'loss.json', None if config.exit is None else root / 'exit_budget.json')


def target_policy(store, config):
    """Derive the exact normal constructor policy; no filesystem bootstrap."""
    from hummingbot.strategy_v2.life_liquidity.storage.legacy_spot import LegacySpotStore
    model = LegacySpotStore(target_files(config), config.controller.storage_binding, config.file_limits, config.risk_limits,
                            config.subsidy_policy, config.loss_policy, config.config_version, config.account_lock_root, store._clock,
                            config.fill_policy, config.accounting_policy, config.terminal_policy, config.recovery_policy,
                            config.settlement_policy, config.control.verifier(), exit_subsidy_policy=None if config.exit is None else config.exit.budget)
    model._executor.shutdown(wait=False)
    return model._policy


def publish_exact(path, raw, *, before_link=None):
    """No overwrite/adoption. Complete or interrupted identical imports replay."""
    import os
    import tempfile
    if path.parent.is_symlink() or path.parent.resolve() != path.parent:
        raise StorageUnavailable('TARGET_DIRECTORY_UNSAFE')
    if path.exists() or path.is_symlink():
        if read_bytes(path, len(raw)) != raw:
            raise StorageConflict('TARGET_IMPORT_FILE_CONFLICT')
        with path.open('rb') as handle:
            os.fsync(handle.fileno())
        sync_dir(path.parent)
        return
    descriptor, temporary = tempfile.mkstemp(prefix='.import-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        if before_link is not None:
            before_link()
        try:
            os.link(temporary, path)
        except FileExistsError:
            if read_bytes(path, len(raw)) != raw:
                raise StorageConflict('TARGET_IMPORT_FILE_CONFLICT')
        os.unlink(temporary)
        sync_dir(path.parent)
    finally:
        if Path(temporary).exists():
            Path(temporary).unlink()


RESET_RESOURCES = ('control_seed', 'control_operations', 'financial_configuration', 'configuration_handoff', 'handoff_artifacts',
                   'session_operations', 'session_head', 'session_observation_grants', 'recorder_configurations')
IMMUTABLE_MAPS = ('commands', 'fills', 'fill_operations', 'cashflows', 'cashflow_operations', 'nav_operations', 'terminal_operations',
                  'lifecycle_operations', 'inventory_operations', 'inventory_cycles', 'recorder_outbox', 'recorder_progress',
                  'recorder_positions', 'recorder_events', 'swap_operations', 'joint_cash_operations', 'hedge_plans', 'product_operations')


class LegacyTargetImportAdapter:
    def _target_preflight(self, manifest):
        if type(manifest) is not TargetImportManifest:
            raise StorageError('TARGET_MANIFEST_INVALID')
        state = self._handoff_state()
        if state.active is None:
            raise StorageError('TARGET_SOURCE_NOT_FROZEN')
        validate_manifest(manifest, state.active, self._read_handoff_source())
        return manifest

    async def validate_configuration_target(self, manifest):
        return await self._call(self._target_preflight, manifest)

    def _import_target(self, command, approval, facts, cut):
        from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorApproval
        from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
        from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
        if (type(command) is not ImportConfigurationTarget or type(approval) is not OperatorApproval
                or type(facts) is not SpotFinancialRecoveryFacts or type(cut) is not FinancialCut):
            raise StorageError('TARGET_IMPORT_INVALID')
        state = self._handoff_state()
        if state.active is None or state.consumed is None or state.consumed.target_manifest_digest != command.manifest.content_digest:
            raise StorageError('TARGET_SOURCE_NOT_CONSUMED')
        source = self._read_handoff_source()
        config, session = validate_manifest(command.manifest, state.active, source)
        ancillary, policies = native_target_images(source, config)
        root = Path(config.state_dir)
        original = read_bytes(self._path, self.limits.max_checkpoint_bytes)
        if (hashlib.sha256(original).hexdigest() != command.source_root_sha256 or cut != self._snapshot().cut
                or dict(cut.components) != dict(source.core.cut.components)):
            raise StorageConflict('TARGET_SOURCE_ROOT_CHANGED')
        qualification = encode({'command': command.json(), 'approval': approval.body(), 'signature': approval.signature,
                                'cut': cut.json(), 'facts': facts.json()})
        qualifier_sha = hashlib.sha256(qualification).hexdigest()
        if len(qualification) > self.limits.max_checkpoint_bytes:
            raise StorageError('TARGET_QUALIFICATION_CAPACITY_EXCEEDED')
        capsule = encode({**json.loads(qualification), 'source_root': base64.b64encode(original).decode(),
                          'source_image': base64.b64encode(pack_source(source)).decode()})
        if len(capsule) > self.limits.max_checkpoint_bytes * 16:
            raise StorageError('TARGET_ANCESTRY_CAPACITY_EXCEEDED')
        if (root / 'ancestry.json').exists():
            retained = read_bytes(root / 'ancestry.json', self.limits.max_checkpoint_bytes * 16)
            previous = json.loads(retained)
            current = json.loads(capsule)
            if any(previous.get(key) != current[key] for key in ('command', 'cut', 'source_root', 'source_image')):
                raise StorageConflict('TARGET_IMPORT_FILE_CONFLICT')
            capsule = retained  # Never rewrite the initial signed ancestry.
        descriptor = {'sha256': hashlib.sha256(capsule).hexdigest(), 'qualification_sha256': qualifier_sha}
        if (root / 'binding.json').exists():
            existing = json.loads(read_bytes(root / 'binding.json', config.file_limits.max_checkpoint_bytes))
            if existing['state']['resources'].get('import_origin') != descriptor:
                raise StorageConflict('TARGET_IMPORT_ALREADY_EXISTS')
            # Historical receipt, never rewrite files or rearm a target.
            return ConfigurationHandoffReceipt(config.controller.storage_binding, command.manifest.operation_id, 1, 'TARGET_IMPORTED', True)
        now = utc(self._clock())
        self._operator_keys.verify(approval, config.controller.storage_binding, 'IMPORT', command.json(), cut, facts, now)
        self._assert_binding(facts.binding)
        if (not session.started_at <= now < session.expires_at or not all((facts.order_scope_complete, facts.bill_scope_complete, facts.runner_scope_complete))
                or not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self._recovery_policy.max_fact_age_ms
                or facts.accepted_wire_ids or facts.runner_wire_ids or facts.liability_base_life or facts.liability_quote_usdt):
            raise StorageError('TARGET_IMPORT_FACTS_UNQUALIFIED')
        meta = self._read()
        self._verify_recovery_projection(meta.resources['commands'], meta.resources.get('fills', {}), meta.resources.get('cashflows', {}), facts)
        if root.is_symlink() or root.exists() and not root.is_dir():
            raise StorageError('TARGET_DIRECTORY_UNOWNED')
        if root.exists():
            # A previously interrupted import may contain only its declared files.
            expected = {'ancestry.json', *(p.name for p in target_files(config).journals().values()),
                        *(ANCILLARY_NAMES.get(name, name + '.json') for name in ancillary)}
            for count, path in enumerate(root.iterdir(), 1):
                qualification_file = path.name.startswith('qualification-') and path.name.endswith('.json')
                if count > self.limits.max_operations or path.is_symlink() or not path.is_file() or path.name not in expected and not qualification_file:
                    raise StorageError('TARGET_DIRECTORY_UNOWNED')
        if root.exists() and any(root.iterdir()) and not (root / 'ancestry.json').is_file():
            raise StorageError('TARGET_DIRECTORY_UNOWNED')
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        sync_dir(root.parent)
        publish_exact(root / 'ancestry.json', capsule)
        publish_exact(root / ('qualification-' + qualifier_sha + '.json'), qualification)
        images = dict(source.core.journal_images)
        images['session'] = encode(SessionJournal(session, None, 'ACTIVE', 'CONFIGURATION_IMPORTED', session.started_at,
                                                  config.controller.strategy.session.on_expiry,
                                                  config.controller.strategy.session.successor_duration_seconds).to_dict())
        files = target_files(config)
        for name, path in files.journals().items():
            publish_exact(path, images[name])
        for name, raw in ancillary.items():
            publish_exact(root / ANCILLARY_NAMES.get(name, name + '.json'), raw)
        data = json.loads(original)['state']
        for name in RESET_RESOURCES:
            data['resources'].pop(name, None)
            data['versions'].pop(name, None)
        for name, policy in native_resource_policies(data['resources'], config, policies).items():
            data['resources'][name] = policy
            data['versions'][name] = data['versions'].get(name, 0) + 1
        data['resources']['import_origin'] = descriptor
        data['resources']['journal_hashes'] = self._hashes(images)
        data['ownership'] = {'mode': 'RECOVERY_ONLY', 'token': config.owner.json()}
        data['revision'] += 1
        data['event_version'] = data['revision']
        data['checked_at'] = now.isoformat()
        data['versions']['import_origin'] = 1
        data['versions']['journal_hashes'] += 1
        data['operations']['target-import:' + command.manifest.operation_id] = {'fingerprint': command.manifest.content_digest, 'revision': data['revision']}
        payload = encode({'schema_version': 1, 'policy': target_policy(self, config), 'state': data})
        if len(payload) > config.file_limits.max_checkpoint_bytes or len(data['operations']) > config.file_limits.max_operations:
            raise StorageError('TARGET_IMPORT_CAPACITY_EXCEEDED')

        def qualified_at_link():
            # Check after the marker's temporary file fsync, immediately before
            # its atomic publication. A slow disk cannot extend the grant.
            current = utc(self._clock())
            self._operator_keys.verify(approval, config.controller.storage_binding, 'IMPORT', command.json(), cut, facts, current)
            if not 0 <= (current - facts.observed_at).total_seconds() * 1000 <= self._recovery_policy.max_fact_age_ms or current >= session.expires_at:
                raise StorageError('TARGET_IMPORT_EXPIRED_AT_COMMIT')
            if read_bytes(self._path, self.limits.max_checkpoint_bytes) != original:
                raise StorageConflict('TARGET_SOURCE_ROOT_CHANGED')
        try:
            publish_exact(root / 'binding.json', payload, before_link=qualified_at_link)
        except StorageError:
            # The guard runs before link; no executable target was published.
            raise
        except BaseException:
            self._uncertain = True
            raise CommitUnknown('TARGET_IMPORT_COMMIT_UNKNOWN') from None
        return ConfigurationHandoffReceipt(config.controller.storage_binding, command.manifest.operation_id, 1, 'TARGET_IMPORTED', False)

    async def import_configuration_target(self, command, *, approval, facts, cut):
        try:
            return await self._call(self._import_target, command, approval, facts, cut)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    def _import_capsule(self, meta):
        raw = read_bytes(self._path.parent / 'ancestry.json', self.limits.max_checkpoint_bytes * 16)
        if hashlib.sha256(raw).hexdigest() != meta.resources['import_origin']['sha256']:
            raise StorageUnavailable('TARGET_ANCESTRY_UNVERIFIED')
        capsule = json.loads(raw)
        qualifier_sha = meta.resources['import_origin']['qualification_sha256']
        digest(qualifier_sha)
        qualified = read_bytes(self._path.parent / ('qualification-' + qualifier_sha + '.json'), self.limits.max_checkpoint_bytes)
        if hashlib.sha256(qualified).hexdigest() != qualifier_sha:
            raise StorageUnavailable('TARGET_QUALIFICATION_UNVERIFIED')
        qualification = json.loads(qualified)
        if capsule['command'] != qualification['command'] or capsule['cut'] != qualification['cut']:
            raise StorageUnavailable('TARGET_QUALIFICATION_UNVERIFIED')
        return {**capsule, **qualification}

    def _import_source_resources(self):
        capsule = self._import_capsule(super()._read())
        return json.loads(base64.b64decode(capsule['source_root'], validate=True))['state']['resources']

    def _inherited_record(self, name, key, row, resources=None):
        meta = self._read() if resources is None else SimpleNamespace(resources=resources)
        if 'import_origin' not in meta.resources:
            return False
        capsule = self._import_capsule(meta)
        original = json.loads(base64.b64decode(capsule['source_root'], validate=True))['state']['resources']
        return key in original.get(name, {}) and original[name][key] == thaw(row)

    def _import_recorder_source(self, meta):
        capsule = self._import_capsule(meta)
        return unpack_source(base64.b64decode(capsule['source_image'], validate=True)).recorder

    def _verify_import_reporting(self, meta, recorder):
        from hummingbot.model.controllers import Controllers
        from hummingbot.model.executors import Executors
        from hummingbot.model.position import Position
        from hummingbot.strategy_v2.life_liquidity.storage.recorder_events import TABLES
        from hummingbot.strategy_v2.life_liquidity.storage.recorder_source import row_values
        wanted = json.loads(self._import_recorder_source(meta).rows_json)
        try:
            with recorder._recorder.sql_manager.get_new_session() as session:
                session.connection().exec_driver_sql('BEGIN')
                for model in (Executors, Position, Controllers, *TABLES.values()):
                    name = recorder._recorder.sql_manager.engine.dialect.identifier_preparer.quote(model.__tablename__)
                    schema = [dict(row) for row in session.connection().exec_driver_sql('PRAGMA table_info(' + name + ')').mappings()]
                    if schema != wanted['schema'][model.__tablename__]:
                        raise StorageUnavailable('TARGET_RECORDER_SCHEMA_CHANGED')
                    if model is Position:
                        # Position is a mutable reporting head. Its original
                        # rows stay in ancestry; the sequenced outbox qualifies
                        # the current before/after set, including cold repair.
                        head = recorder._position_head(meta)
                        candidates = [recorder._expected_position_rows(head)]
                        pending = []
                        for key, entry in meta.resources.get('recorder_positions', {}).items():
                            if entry['state'] == 'APPLIED':
                                continue
                            from hummingbot.strategy_v2.life_liquidity.storage.recorder_positions import PositionReport
                            command = PositionReport.from_json(thaw(entry['command']))
                            ledger = self._state_operation(meta, 'position-start:' + key)
                            if (entry['state'] != 'PROJECTING' or command.binding != self.binding
                                    or command.controller_id != recorder.controller_id or command.operation_id != key
                                    or command.expected_sequence != (0 if head is None else head.expected_sequence + 1)
                                    or ledger['revision'] != entry['cut']['revision'] + 1
                                    or entry['identity'] != fingerprint({'command': command.json(), 'cut': thaw(entry['cut']),
                                                                         'owner': thaw(entry['owner'])})):
                                raise StorageUnavailable('TARGET_POSITION_ORIGIN_UNVERIFIED')
                            pending.append(command)
                            candidates.append(recorder._expected_position_rows(command))
                        actual = recorder._position_rows()
                        if (len(pending) > 1 or actual not in candidates and (pending or
                                                                              any(row not in candidates[0] for row in actual))):
                            raise StorageUnavailable('TARGET_POSITION_ANCESTRY_UNVERIFIED')
                        continue
                    for row in wanted['tables'][model.__tablename__]:
                        query = session.query(model)
                        for column in model.__table__.primary_key.columns:
                            query = query.filter(column == row[column.name])
                        actual = query.all()
                        # MarketState is an SDK diagnostic snapshot. Preserve
                        # its original PK/bytes in ancestry, allow its current
                        # value to change; it never provides financial evidence.
                        if len(actual) != 1 or model.__tablename__ != 'MarketState' and row_values(model, actual[0]) != row:
                            raise StorageUnavailable('TARGET_RECORDER_ANCESTRY_UNVERIFIED')
        except StorageError:
            raise
        except Exception:
            raise StorageUnavailable('TARGET_RECORDER_ANCESTRY_UNVERIFIED') from None

    def _verify_import_seed(self, meta):
        from datetime import datetime

        from hummingbot.strategy_v2.life_liquidity.policy_state import PolicyState
        from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorApproval
        from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
        from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
        from hummingbot.strategy_v2.life_liquidity.storage.service_config import FileServiceConfig
        capsule = self._import_capsule(meta)
        command = ImportConfigurationTarget.from_json(capsule['command'])
        source = unpack_source(base64.b64decode(capsule['source_image'], validate=True))
        origin = FileServiceConfig.model_validate_json(source.service_json)
        source_path = Path(origin.state_dir) / 'binding.json'
        seen = getattr(self, '_import_audit_paths', ())
        if source_path in seen or source_path == self._path or len(seen) >= 16:
            raise StorageUnavailable('TARGET_ANCESTRY_CYCLE_OR_DEPTH')
        raw = base64.b64decode(capsule['source_root'], validate=True)
        if hashlib.sha256(raw).hexdigest() != command.source_root_sha256 or read_bytes(source_path, origin.file_limits.max_checkpoint_bytes) != raw:
            raise StorageUnavailable('TARGET_SOURCE_RETIREMENT_UNVERIFIED')
        auditor = copy.copy(self)
        auditor._path, auditor._binding, auditor._limits = source_path, source.core.cut.checkpoint.binding, origin.file_limits
        auditor._policy = json.loads(source.core.checkpoint_bytes)['policy']
        auditor._config_version = origin.config_version
        from hummingbot.strategy_v2.life_liquidity.session import SessionStore
        auditor._sessions = SessionStore(source_path.parent / 'session.json')
        auditor._import_audit_paths = (*seen, self._path)
        auditor._state = PolicyState(source_path, policy=auditor._policy, initial={}, create=False)
        audited = auditor._read()
        head = auditor._handoff_state()
        if (head.active is None or head.consumed is None or head.consumed.target_manifest_digest != command.manifest.content_digest
                or head.active.source_digest != source.content_digest):
            raise StorageUnavailable('TARGET_SOURCE_RETIREMENT_UNVERIFIED')
        config, session = validate_manifest(command.manifest, head.active, source)
        if config.controller.storage_binding != self.binding or self._config_version != config.config_version:
            raise StorageUnavailable('TARGET_IMPORT_BINDING_INVALID')
        approval = OperatorApproval(**{**capsule['approval'], 'binding': self.binding,
                                       'issued_at': datetime.fromisoformat(capsule['approval']['issued_at']),
                                       'expires_at': datetime.fromisoformat(capsule['approval']['expires_at']), 'signature': capsule['signature']})
        cut, facts = FinancialCut.from_json(capsule['cut']), SpotFinancialRecoveryFacts.from_json(capsule['facts'])
        if cut.checkpoint != audited.cut or facts.binding != auditor.binding:
            raise StorageUnavailable('TARGET_IMPORT_ORIGIN_INVALID')
        self._operator_keys.verify(approval, self.binding, 'IMPORT', command.json(), cut, facts, approval.issued_at)
        # Audit the initial qualifier as well as a refreshed publication grant.
        # Refreshing an expired attempt never replaces its original signature.
        initial = json.loads(read_bytes(self._path.parent / 'ancestry.json', self.limits.max_checkpoint_bytes * 16))
        initial_approval = OperatorApproval(**{**initial['approval'], 'binding': self.binding,
                                               'issued_at': datetime.fromisoformat(initial['approval']['issued_at']),
                                               'expires_at': datetime.fromisoformat(initial['approval']['expires_at']), 'signature': initial['signature']})
        self._operator_keys.verify(initial_approval, self.binding, 'IMPORT', command.json(),
                                   FinancialCut.from_json(initial['cut']), SpotFinancialRecoveryFacts.from_json(initial['facts']),
                                   initial_approval.issued_at)
        if self._sessions.load().primary != session:
            raise StorageUnavailable('TARGET_SESSION_ORIGIN_INVALID')
        old = json.loads(raw)['state']
        with self._state.inspect() as current:
            if any(thaw(current['operations'].get(key)) != row for key, row in old['operations'].items()):
                raise StorageUnavailable('TARGET_OPERATION_ANCESTRY_INVALID')
        for name in IMMUTABLE_MAPS:
            for key, row in old['resources'].get(name, {}).items():
                if thaw(meta.resources.get(name, {}).get(key)) != row:
                    raise StorageUnavailable('TARGET_FINANCIAL_ANCESTRY_INVALID')
        return ({'mode': 'RECOVERY_ONLY', 'token': config.owner.json()}, old['resources'])
