"""Signed next-session configuration staging; no active policy handoff here.

Staging drains admission in the same checkpoint as the approved candidate. The
current binding, session deadline, native money and journals remain immutable.
Applying a candidate to a new policy/lineage requires a separate handoff port.
"""
import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.config import StrategyConfig, apply_update
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerMode,
    OwnerToken,
    StateBinding,
    StorageConflict,
    StorageError,
    UnsupportedBackend,
    fingerprint,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import OperatorApproval
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


def _identity(operation_id, expected_sequence, owner):
    identifier(operation_id)
    if (len(operation_id) > 96 or type(expected_sequence) is not int or expected_sequence < 0
            or type(owner) is not OwnerToken):
        raise StorageError('FINANCIAL_CONFIG_COMMAND_INVALID')


def _strategy(raw):
    if type(raw) is not str or len(raw.encode()) > 65536:
        raise StorageError('FINANCIAL_CONFIG_CANDIDATE_INVALID')
    return StrategyConfig.model_validate_json(raw)


@dataclass(frozen=True)
class StageFinancialConfiguration:
    operation_id: str
    expected_sequence: int
    owner: OwnerToken
    session_expires_at: datetime
    source_json: str
    candidate_json: str

    def __post_init__(self):
        _identity(self.operation_id, self.expected_sequence, self.owner)
        utc(self.session_expires_at)
        if self.session_expires_at < self.owner.expires_at:
            raise StorageError('FINANCIAL_CONFIG_DEADLINE_INVALID')
        try:
            source, candidate = _strategy(self.source_json), _strategy(self.candidate_json)
            changes = {}
            # Reuse the declared hot-reload contract. Budgets, execution mode,
            # instruments and ownership cannot be smuggled into a revision.
            for section, fields in (('session', ('duration',)), ('reference', ('lookback',)),
                                    ('quotes', ('spreads_bps', 'sizes_base'))):
                values = {field: getattr(getattr(candidate, section), field) for field in fields
                          if getattr(getattr(candidate, section), field) != getattr(getattr(source, section), field)}
                if values:
                    changes[section] = values
            if apply_update(source, changes) != candidate:
                raise ValueError()
            object.__setattr__(self, 'source_json', source.model_dump_json())
            object.__setattr__(self, 'candidate_json', candidate.model_dump_json())
        except (ValueError, TypeError, StorageError):
            raise StorageError('FINANCIAL_CONFIG_CANDIDATE_INVALID') from None

    @property
    def source_hash(self):
        return fingerprint(_strategy(self.source_json).model_dump(mode='json'))

    @property
    def target_hash(self):
        return fingerprint(_strategy(self.candidate_json).model_dump(mode='json'))

    def json(self):
        return {'kind': 'STAGE_FINANCIAL_CONFIGURATION', 'operation_id': self.operation_id,
                'expected_sequence': self.expected_sequence, 'owner': self.owner.json(),
                'session_expires_at': self.session_expires_at.isoformat(),
                'source_json': self.source_json, 'candidate_json': self.candidate_json}

    @classmethod
    def from_json(cls, value):
        data = thaw(value)
        if data.pop('kind') != 'STAGE_FINANCIAL_CONFIGURATION':
            raise StorageError('FINANCIAL_CONFIG_KIND_INVALID')
        data['owner'] = OwnerToken.from_json(data['owner'])
        data['session_expires_at'] = datetime.fromisoformat(data['session_expires_at'])
        return cls(**data)


@dataclass(frozen=True)
class DiscardFinancialConfiguration:
    operation_id: str
    expected_sequence: int
    pending_operation_id: str
    owner: OwnerToken

    def __post_init__(self):
        _identity(self.operation_id, self.expected_sequence, self.owner)
        identifier(self.pending_operation_id)

    def json(self):
        return {'kind': 'DISCARD_FINANCIAL_CONFIGURATION', 'operation_id': self.operation_id,
                'expected_sequence': self.expected_sequence, 'pending_operation_id': self.pending_operation_id,
                'owner': self.owner.json()}

    @classmethod
    def from_json(cls, value):
        data = thaw(value)
        if data.pop('kind') != 'DISCARD_FINANCIAL_CONFIGURATION':
            raise StorageError('FINANCIAL_CONFIG_KIND_INVALID')
        data['owner'] = OwnerToken.from_json(data['owner'])
        return cls(**data)


@dataclass(frozen=True)
class FinancialConfigurationState:
    binding: StateBinding
    sequence: int
    active_config_version: int
    pending: StageFinancialConfiguration | None


@dataclass(frozen=True)
class FinancialConfigurationReceipt:
    binding: StateBinding
    operation_id: str
    sequence: int
    state: str
    replayed: bool


class FinancialConfigurationRepository(Protocol):
    async def financial_configuration(self, binding: StateBinding) -> FinancialConfigurationState:
        ...

    async def stage_financial_configuration(self, command: StageFinancialConfiguration, *, approval: OperatorApproval,
                                            facts: SpotFinancialRecoveryFacts, cut: FinancialCut) -> FinancialConfigurationReceipt:
        ...

    async def discard_financial_configuration(self, command: DiscardFinancialConfiguration, *, approval: OperatorApproval,
                                              facts: SpotFinancialRecoveryFacts, cut: FinancialCut) -> FinancialConfigurationReceipt:
        ...


def empty_configuration_head():
    return {'sequence': 0, 'pending_operation_id': None}


def configuration_projection(payload, head, ownership, binding):
    """Pure replay of signed decisions; never infer activation from a revision."""
    if payload['kind'] == 'STAGE_FINANCIAL_CONFIGURATION':
        command = StageFinancialConfiguration.from_json(payload)
        if head['pending_operation_id'] is not None or command.source_hash != binding.config_hash:
            raise StorageError('FINANCIAL_CONFIG_SOURCE_OR_PENDING_CONFLICT')
        pending = command.operation_id
    else:
        command = DiscardFinancialConfiguration.from_json(payload)
        if head['pending_operation_id'] != command.pending_operation_id:
            raise StorageError('FINANCIAL_CONFIG_PENDING_CONFLICT')
        pending = None
    if (command.json() != thaw(payload) or command.expected_sequence != head['sequence']
            or command.owner.json() != ownership['token']
            or ownership['mode'] not in ('ACTIVE', 'DRAINING', 'RECOVERY_ONLY')):
        raise StorageError('FINANCIAL_CONFIG_SEQUENCE_OR_OWNER_CONFLICT')
    return {'sequence': head['sequence'] + 1, 'pending_operation_id': pending}, {'mode': 'DRAINING', 'token': ownership['token']}


class LegacyFinancialConfigurationAdapter:
    def _financial_configuration(self):
        meta = self._read()
        head = meta.resources.get('financial_configuration', empty_configuration_head())
        pending = head['pending_operation_id']
        return FinancialConfigurationState(self.binding, head['sequence'], self._config_version,
                                           None if pending is None else StageFinancialConfiguration.from_json(
            meta.resources['control_operations'][pending]['payload']))

    async def financial_configuration(self, binding):
        self._assert_binding(binding)
        return await self._call(self._financial_configuration)

    def _configuration_guard(self, command, approval, facts, cut):
        if self._operator_keys is None or self._recovery_policy is None or self._accounting_policy is None:
            raise UnsupportedBackend('FINANCIAL_CONFIG_POLICIES_REQUIRED')
        self._assert_binding(facts.binding)
        self._assert_binding(cut.checkpoint.binding)
        meta = self._read()
        previous = meta.resources.get('control_operations', {}).get(command.operation_id)
        if previous is not None:
            if (thaw(previous['payload']) != command.json() or previous['approval'] != approval.body()
                    or previous['signature'] != approval.signature or thaw(previous['cut']) != cut.json()
                    or thaw(previous['facts']) != facts.json()):
                raise StorageConflict('CONTROL_OPERATION_CONFLICT')
            return meta, None, True
        now = utc(self._clock())
        self._operator_keys.verify(approval, self.binding, 'CONFIGURE', command.json(), cut, facts, now)
        view = self._snapshot()
        if view.cut != cut:
            raise StorageConflict('SPOT_CUT_STALE')
        if (meta.ownership.token != command.owner or now < command.owner.valid_from
                or meta.ownership.mode not in (OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY)
                or self._incomplete(meta.resources) or self._reporting_pending(meta.resources)
                or dict(view.cut.components) != dict(meta.resources['journal_hashes'])
                or not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self._recovery_policy.max_fact_age_ms
                or not facts.order_scope_complete or not facts.bill_scope_complete or not facts.runner_scope_complete
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0):
            raise StorageError('FINANCIAL_CONFIG_FACTS_UNQUALIFIED')
        self._verify_recovery_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                         meta.resources.get('cashflows', {}), facts)
        self._verify_fill_projection(meta.resources['commands'], meta.resources.get('fills', {}))
        self._verify_capital_projection(meta.resources['commands'], meta.resources.get('fills', {}), meta.resources.get('cashflows', {}))
        self._verify_inventory_projection(meta.resources)
        self._verify_terminal_projection(meta.resources)
        return meta, view, False

    def _configuration_decision(self, command, approval, facts, cut):
        meta, view, replay = self._configuration_guard(command, approval, facts, cut)
        staging = type(command) is StageFinancialConfiguration
        if not replay:
            if staging and (command.source_hash != self.binding.config_hash
                            or _strategy(command.source_json).config_version != self._config_version
                            or command.session_expires_at != view.session.expires_at
                            or (str(command.owner.session_id), command.owner.epoch) != (view.session.session_id, view.session.epoch)
                            or utc(self._clock()) >= min(command.owner.expires_at, view.session.expires_at)):
                raise StorageError('FINANCIAL_CONFIG_SOURCE_OR_SESSION_INVALID')
            head, ownership = configuration_projection(command.json(),
                                                       meta.resources.get('financial_configuration', empty_configuration_head()),
                                                       {'mode': meta.ownership.mode.value, 'token': meta.ownership.token.json()}, self.binding)
            replay = self._control_commit(command.operation_id, cut, approval, command.json(), ownership, facts,
                                          {'financial_configuration': head})
            self._cold_unarmed = True
        return FinancialConfigurationReceipt(self.binding, command.operation_id, command.expected_sequence + 1,
                                             'STAGED' if staging else 'DISCARDED', replay)

    async def _configuration_command(self, command, approval, facts, cut, kind):
        if (type(command) is not kind or type(approval) is not OperatorApproval
                or type(facts) is not SpotFinancialRecoveryFacts or type(cut) is not FinancialCut):
            raise StorageError('FINANCIAL_CONFIG_COMMAND_INVALID')
        try:
            return await self._call(self._configuration_decision, command, approval, facts, cut)
        except asyncio.CancelledError:
            self._uncertain = True
            raise

    async def stage_financial_configuration(self, command, *, approval, facts, cut):
        return await self._configuration_command(command, approval, facts, cut, StageFinancialConfiguration)

    async def discard_financial_configuration(self, command, *, approval, facts, cut):
        return await self._configuration_command(command, approval, facts, cut, DiscardFinancialConfiguration)
