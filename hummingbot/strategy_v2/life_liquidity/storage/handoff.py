"""Distinct signed target reservation, source freeze and source consumption.

Neither reservation nor consumption imports or activates a target. Frozen
sources permit typed maintenance; consumption permanently excludes cancellation.
"""
import asyncio
import json
from dataclasses import dataclass
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.config import StrategyConfig, parse_duration_seconds
from hummingbot.strategy_v2.life_liquidity.session import SessionRecord, _deadline
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    StateBinding,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    digest,
    encode,
    identifier,
    thaw,
    utc,
)


def session_record(raw):
    try:
        if type(raw) is not str or len(raw.encode()) > 8192:
            raise ValueError()
        record = SessionRecord.from_dict(json.loads(raw))
        if (type(record.epoch) is not int or type(record.config_version) is not int
                or UUID(record.session_id).int == 0):
            raise ValueError()
        return record
    except (ValueError, TypeError, KeyError):
        raise StorageError('HANDOFF_SESSION_INVALID') from None


def identity(operation_id, sequence, owner):
    identifier(operation_id)
    if len(operation_id) > 96 or type(sequence) is not int or sequence < 0 or type(owner) is not OwnerToken:
        raise StorageError('HANDOFF_COMMAND_INVALID')


@dataclass(frozen=True)
class AuthorizeConfigurationHandoff:
    operation_id: str
    expected_sequence: int
    owner: OwnerToken
    pending_operation_id: str
    source_digest: str
    source_session_json: str
    target_binding: StateBinding
    target_session_json: str
    max_bytes: int
    max_rows: int
    source_format: str = 'FILE_SERVICE_SCOPED'

    def __post_init__(self):
        identity(self.operation_id, self.expected_sequence, self.owner)
        identifier(self.pending_operation_id)
        digest(self.source_digest)
        if (type(self.target_binding) is not StateBinding or any(type(v) is not int or v < 1 for v in (self.max_bytes, self.max_rows))
                or self.source_format not in ('FILE_SERVICE_SCOPED', 'STOPPED_FILE_SERVICE_V1')):
            raise StorageError('HANDOFF_COMMAND_INVALID')
        source, target = session_record(self.source_session_json), session_record(self.target_session_json)
        if ((source.session_id, source.epoch) != (str(self.owner.session_id), self.owner.epoch)
                or source.expires_at < self.owner.expires_at or target.session_id == source.session_id
                or target.epoch != source.epoch + 1 or target.started_at < source.expires_at):
            raise StorageError('HANDOFF_SESSION_INVALID')
        object.__setattr__(self, 'source_session_json', encode(source.to_dict()).decode())
        object.__setattr__(self, 'target_session_json', encode(target.to_dict()).decode())

    def json(self):
        result = {'kind': 'AUTHORIZE_CONFIGURATION_HANDOFF', **self.__dict__,
                  'owner': self.owner.json(), 'target_binding': self.target_binding.json()}
        if self.source_format == 'FILE_SERVICE_SCOPED':
            result.pop('source_format')  # Preserve previously signed bytes.
        return result

    @classmethod
    def from_json(cls, value):
        data = thaw(value)
        if data.pop('kind') != 'AUTHORIZE_CONFIGURATION_HANDOFF':
            raise StorageError('HANDOFF_COMMAND_INVALID')
        data['owner'] = OwnerToken.from_json(data['owner'])
        data['target_binding'] = StateBinding.from_json(data['target_binding'])
        return cls(**data)


@dataclass(frozen=True)
class CancelConfigurationHandoff:
    operation_id: str
    expected_sequence: int
    owner: OwnerToken
    handoff_operation_id: str

    def __post_init__(self):
        identity(self.operation_id, self.expected_sequence, self.owner)
        identifier(self.handoff_operation_id)

    def json(self):
        return {'kind': 'CANCEL_CONFIGURATION_HANDOFF', **self.__dict__, 'owner': self.owner.json()}

    @classmethod
    def from_json(cls, value):
        data = thaw(value)
        if data.pop('kind') != 'CANCEL_CONFIGURATION_HANDOFF':
            raise StorageError('HANDOFF_COMMAND_INVALID')
        data['owner'] = OwnerToken.from_json(data['owner'])
        return cls(**data)


@dataclass(frozen=True)
class ConsumeConfigurationHandoff:
    """Irreversibly reserve one exact import identity; never a send grant."""
    operation_id: str
    expected_sequence: int
    owner: OwnerToken
    handoff_operation_id: str
    target_manifest_digest: str
    postgres_retirement_json: str | None = None

    def __post_init__(self):
        identity(self.operation_id, self.expected_sequence, self.owner)
        identifier(self.handoff_operation_id)
        digest(self.target_manifest_digest)
        if self.postgres_retirement_json is not None:
            from hummingbot.strategy_v2.life_liquidity.storage.stopped_retirement import StoppedRetirementProof
            StoppedRetirementProof.from_json(self.postgres_retirement_json)

    def json(self):
        result = {'kind': 'CONSUME_CONFIGURATION_HANDOFF', **self.__dict__, 'owner': self.owner.json()}
        if self.postgres_retirement_json is None:
            result.pop('postgres_retirement_json')  # Preserve existing signed File bytes.
        return result

    @classmethod
    def from_json(cls, value):
        data = thaw(value)
        if data.pop('kind') != 'CONSUME_CONFIGURATION_HANDOFF':
            raise StorageError('HANDOFF_COMMAND_INVALID')
        data['owner'] = OwnerToken.from_json(data['owner'])
        return cls(**data)


@dataclass(frozen=True)
class ConfigurationHandoffState:
    binding: StateBinding
    sequence: int
    active: AuthorizeConfigurationHandoff | None
    consumed: ConsumeConfigurationHandoff | None = None


@dataclass(frozen=True)
class ConfigurationHandoffReceipt:
    binding: StateBinding
    operation_id: str
    sequence: int
    state: str
    replayed: bool


def empty_handoff_head():
    return {'sequence': 0, 'active_operation_id': None}


def validate_target(command, stage, binding):
    source, target = session_record(command.source_session_json), session_record(command.target_session_json)
    old = StrategyConfig.model_validate_json(stage.source_json)
    new = StrategyConfig.model_validate_json(stage.candidate_json)
    namespace = command.target_binding
    if (command.pending_operation_id != stage.operation_id or stage.source_hash != binding.config_hash
            or namespace.lineage == binding.lineage or namespace.config_hash != stage.target_hash
            or any(getattr(namespace, k) != getattr(binding, k) for k in (
                'environment', 'exchange', 'account_uid', 'risk_pool', 'schema_version', 'policy_hash'))
            or source.config_version != old.config_version or source.expires_at != stage.session_expires_at
            or source.reference_mode != old.reference.mode or target.config_version != new.config_version
            or target.reference_mode != new.reference.mode or target.model_version != source.model_version
            or dict(target.anchors) != dict(source.anchors)
            or target.expires_at != _deadline(target.started_at, parse_duration_seconds(new.session.duration))):
        raise StorageError('HANDOFF_TARGET_INVALID')


def handoff_projection(payload, head, ownership, binding, configuration, records):
    if head.get('consumed_operation_id') is not None:
        raise StorageError('HANDOFF_ALREADY_CONSUMED')
    consumed = None
    if payload['kind'] == 'AUTHORIZE_CONFIGURATION_HANDOFF':
        from hummingbot.strategy_v2.life_liquidity.storage.financial_configuration import StageFinancialConfiguration
        command = AuthorizeConfigurationHandoff.from_json(payload)
        if head['active_operation_id'] is not None or configuration['pending_operation_id'] != command.pending_operation_id:
            raise StorageError('HANDOFF_PENDING_CONFLICT')
        validate_target(command, StageFinancialConfiguration.from_json(records[command.pending_operation_id]['payload']), binding)
        active = command.operation_id
    elif payload['kind'] == 'CANCEL_CONFIGURATION_HANDOFF':
        command = CancelConfigurationHandoff.from_json(payload)
        if head['active_operation_id'] != command.handoff_operation_id:
            raise StorageError('HANDOFF_PENDING_CONFLICT')
        active = None
    elif payload['kind'] == 'CONSUME_CONFIGURATION_HANDOFF':
        command = ConsumeConfigurationHandoff.from_json(payload)
        if head['active_operation_id'] != command.handoff_operation_id:
            raise StorageError('HANDOFF_PENDING_CONFLICT')
        grant = AuthorizeConfigurationHandoff.from_json(records[command.handoff_operation_id]['payload'])
        if grant.source_format == 'STOPPED_FILE_SERVICE_V1' and command.postgres_retirement_json is None:
            raise StorageError('POSTGRES_CUTOVER_CONSUMPTION_REQUIRED')
        if command.postgres_retirement_json is not None:
            from hummingbot.strategy_v2.life_liquidity.storage.stopped_retirement import StoppedRetirementProof
            StoppedRetirementProof.from_json(command.postgres_retirement_json).verify(command, grant, binding)
        active, consumed = command.handoff_operation_id, command.operation_id
    else:
        raise StorageError('HANDOFF_COMMAND_INVALID')
    if (thaw(payload) != command.json() or command.expected_sequence != head['sequence']
            or command.owner.json() != ownership['token'] or ownership['mode'] != 'DRAINING'):
        raise StorageError('HANDOFF_OWNER_OR_SEQUENCE_CONFLICT')
    result = {'sequence': head['sequence'] + 1, 'active_operation_id': active}
    if consumed is not None:
        result['consumed_operation_id'] = consumed
    return result


class LegacyConfigurationHandoffControlAdapter:
    def _before_call(self, function, args):
        if not getattr(self, '_handoff_frozen', False):
            return
        allowed = ('_read', '_snapshot', '_financial_configuration', '_handoff_state', '_lookup',
                   '_read_handoff_source', '_authorize_handoff', '_cancel_handoff', '_consume_handoff', '_import_target', '_target_preflight',
                   '_stopped_retirement_context', '_stopped_retirement_checkpoint')
        if (getattr(function, '__self__', None) is not self
                or not any(getattr(function, '__func__', None) is getattr(getattr(self, name), '__func__', None) for name in allowed)):
            raise StorageError('CONFIGURATION_HANDOFF_SOURCE_FROZEN')
        self._read()  # Re-audit the durable freeze before allowed maintenance.

    def _handoff_state(self):
        meta = self._read()
        head = meta.resources.get('configuration_handoff', empty_handoff_head())
        key = head['active_operation_id']
        consumed = head.get('consumed_operation_id')
        return ConfigurationHandoffState(self.binding, head['sequence'], None if key is None else
                                         AuthorizeConfigurationHandoff.from_json(meta.resources['control_operations'][key]['payload']),
                                         None if consumed is None else ConsumeConfigurationHandoff.from_json(
                                             meta.resources['control_operations'][consumed]['payload']))

    async def configuration_handoff(self, binding):
        self._assert_binding(binding)
        return await self._call(self._handoff_state)

    def _handoff_guard(self, command, approval, facts, cut):
        meta = self._read()
        previous = meta.resources.get('control_operations', {}).get(command.operation_id)
        if previous is not None:
            if (thaw(previous['payload']) != command.json() or previous['approval'] != approval.body()
                    or previous['signature'] != approval.signature or thaw(previous['cut']) != cut.json()
                    or thaw(previous['facts']) != facts.json()):
                raise StorageConflict('CONTROL_OPERATION_CONFLICT')
            return meta, True
        if self._operator_keys is None or self._recovery_policy is None:
            raise StorageError('HANDOFF_CONTROL_POLICY_REQUIRED')
        self._assert_binding(facts.binding)
        self._assert_binding(cut.checkpoint.binding)
        now = utc(self._clock())
        self._operator_keys.verify(approval, self.binding, 'HANDOFF', command.json(), cut, facts, now)
        if self._snapshot().cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        if (not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self._recovery_policy.max_fact_age_ms
                or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete):
            raise StorageError('HANDOFF_FACTS_UNQUALIFIED')
        return meta, False

    def _authorize_handoff(self, command, approval, facts, cut):
        from hummingbot.strategy_v2.life_liquidity.storage.control_plane import ControlSnapshot
        from hummingbot.strategy_v2.life_liquidity.storage.handoff_artifact import persist_source
        meta, replay = self._handoff_guard(command, approval, facts, cut)
        if not replay:
            capture = getattr(self, '_handoff_capture', None)
            if capture is None:
                raise StorageError('HANDOFF_CONFIGURED_SOURCE_REQUIRED')
            head = handoff_projection(command.json(), meta.resources.get('configuration_handoff', empty_handoff_head()),
                                      {'mode': meta.ownership.mode.value, 'token': meta.ownership.token.json()}, self.binding,
                                      meta.resources['financial_configuration'], meta.resources['control_operations'])
            collect = capture.capture_stopped if command.source_format == 'STOPPED_FILE_SERVICE_V1' else capture.capture
            source = collect(ControlSnapshot(self.binding, cut, facts), command.max_bytes, command.max_rows)
            journal = self._sessions.load()
            current = journal.successor or journal.primary
            if source.content_digest != command.source_digest or encode(current.to_dict()).decode() != command.source_session_json:
                raise StorageConflict('SERVICE_HANDOFF_SOURCE_CHANGED')
            if utc(self._clock()) >= session_record(command.target_session_json).expires_at:
                raise StorageError('HANDOFF_TARGET_EXPIRED')
            capture.assert_quiescent(source.base if command.source_format == 'STOPPED_FILE_SERVICE_V1' else source, facts)
            artifact = persist_source(self, command, approval, source)
            # Recollect after filesystem publication, before changing authority.
            verified = collect(ControlSnapshot(self.binding, cut, facts), command.max_bytes, command.max_rows)
            if verified.content_digest != command.source_digest:
                raise StorageConflict('SERVICE_HANDOFF_SOURCE_CHANGED')
            replay = self._control_commit(command.operation_id, cut, approval, command.json(),
                                          {'mode': 'DRAINING', 'token': command.owner.json()}, facts,
                                          {'configuration_handoff': head,
                                           'handoff_artifacts': {**thaw(meta.resources.get('handoff_artifacts', {})), command.operation_id: artifact}})
            self._handoff_frozen = self._cold_unarmed = True
        return ConfigurationHandoffReceipt(self.binding, command.operation_id, command.expected_sequence + 1, 'SOURCE_FROZEN', replay)

    def _cancel_handoff(self, command, approval, facts, cut):
        meta, replay = self._handoff_guard(command, approval, facts, cut)
        if not replay:
            head = handoff_projection(command.json(), meta.resources.get('configuration_handoff', empty_handoff_head()),
                                      {'mode': meta.ownership.mode.value, 'token': meta.ownership.token.json()}, self.binding,
                                      meta.resources['financial_configuration'], meta.resources['control_operations'])
            replay = self._control_commit(command.operation_id, cut, approval, command.json(),
                                          {'mode': 'DRAINING', 'token': command.owner.json()}, facts, {'configuration_handoff': head})
            self._handoff_frozen, self._cold_unarmed = False, True
        return ConfigurationHandoffReceipt(self.binding, command.operation_id, command.expected_sequence + 1, 'CANCELLED', replay)

    def _consume_handoff(self, command, approval, facts, cut):
        meta, replay = self._handoff_guard(command, approval, facts, cut)
        if not replay:
            head = handoff_projection(command.json(), meta.resources.get('configuration_handoff', empty_handoff_head()),
                                      {'mode': meta.ownership.mode.value, 'token': meta.ownership.token.json()}, self.binding,
                                      meta.resources['financial_configuration'], meta.resources['control_operations'])
            source = self._read_handoff_source()
            grant = AuthorizeConfigurationHandoff.from_json(meta.resources['control_operations'][command.handoff_operation_id]['payload'])
            if grant.source_format == 'STOPPED_FILE_SERVICE_V1':
                if command.postgres_retirement_json is None:
                    raise StorageError('POSTGRES_CUTOVER_CONSUMPTION_REQUIRED')
                from hummingbot.strategy_v2.life_liquidity.storage.stopped_retirement import StoppedRetirementProof
                StoppedRetirementProof.from_json(command.postgres_retirement_json).verify(
                    command, grant, self.binding, operator_keys=self._operator_keys.keys.values(), facts=facts,
                    source=source, now=utc(self._clock()), cut=cut)
                source_core = source.base.core
            else:
                source_core = source.core
            if utc(self._clock()) >= session_record(grant.target_session_json).expires_at:
                raise StorageError('HANDOFF_TARGET_EXPIRED')
            # An external transfer cannot be silently adopted during retirement.
            self._verify_recovery_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                             meta.resources.get('cashflows', {}), facts)
            if (facts.accepted_wire_ids or facts.runner_wire_ids or facts.liability_base_life != 0
                    or facts.liability_quote_usdt != 0 or dict(cut.components) != dict(source_core.cut.components)):
                raise StorageError('HANDOFF_CONSUMPTION_FACTS_UNQUALIFIED')
            replay = self._control_commit(command.operation_id, cut, approval, command.json(),
                                          {'mode': 'DRAINING', 'token': command.owner.json()}, facts, {'configuration_handoff': head})
            self._handoff_frozen = self._cold_unarmed = True
        return ConfigurationHandoffReceipt(self.binding, command.operation_id, command.expected_sequence + 1, 'SOURCE_CONSUMED', replay)

    async def _handoff_command(self, command, approval, facts, cut, kind, method):
        from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorApproval
        from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
        from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts
        if (type(command) is not kind or type(approval) is not OperatorApproval
                or type(facts) is not SpotFinancialRecoveryFacts or type(cut) is not FinancialCut):
            raise StorageError('HANDOFF_COMMAND_INVALID')
        try:
            return await self._call(method, command, approval, facts, cut)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    async def authorize_configuration_handoff(self, command, *, approval, facts, cut):
        return await self._handoff_command(command, approval, facts, cut, AuthorizeConfigurationHandoff, self._authorize_handoff)

    async def cancel_configuration_handoff(self, command, *, approval, facts, cut):
        return await self._handoff_command(command, approval, facts, cut, CancelConfigurationHandoff, self._cancel_handoff)

    async def consume_configuration_handoff(self, command, *, approval, facts, cut):
        return await self._handoff_command(command, approval, facts, cut, ConsumeConfigurationHandoff, self._consume_handoff)

    def _read_handoff_source(self):
        from hummingbot.strategy_v2.life_liquidity.storage.handoff_artifact import load_source
        state = self._handoff_state()
        if state.active is None:
            raise StorageError('HANDOFF_NOT_FROZEN')
        try:
            return load_source(self, state.active, self._read().resources['handoff_artifacts'][state.active.operation_id])
        except (KeyError, TypeError):
            raise StorageUnavailable('HANDOFF_ARTIFACT_ORIGIN_INVALID') from None

    async def frozen_configuration_source(self, binding):
        self._assert_binding(binding)
        return await self._call(self._read_handoff_source)

    def _stopped_retirement_context(self):
        head = self._handoff_state()
        if head.active is None or head.active.source_format != 'STOPPED_FILE_SERVICE_V1' or head.consumed is not None:
            raise StorageError('STOPPED_RETIREMENT_CONTEXT_UNAVAILABLE')
        cut = self._snapshot().cut
        with self._state.inspect() as state:
            if self._cut(state) != cut.checkpoint:
                raise StorageConflict('STOPPED_RETIREMENT_CUT_UNQUALIFIED')
            return cut, state['checked_at']

    async def stopped_retirement_context(self, binding):
        self._assert_binding(binding)
        return await self._call(self._stopped_retirement_context)

    def _stopped_retirement_checkpoint(self):
        from hummingbot.strategy_v2.life_liquidity.storage.handoff_artifact import read_bytes
        head = self._handoff_state()
        if head.active is None or head.active.source_format != 'STOPPED_FILE_SERVICE_V1' or head.consumed is None:
            raise StorageError('STOPPED_RETIREMENT_NOT_COMMITTED')
        raw = read_bytes(self._path, self.limits.max_checkpoint_bytes)
        state = json.loads(raw)['state']
        if self._cut(state) != self._read().cut:
            raise StorageConflict('STOPPED_RETIREMENT_CUT_UNQUALIFIED')
        return raw

    async def stopped_retirement_checkpoint(self, binding):
        self._assert_binding(binding)
        return await self._call(self._stopped_retirement_checkpoint)
