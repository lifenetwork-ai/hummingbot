"""Read-only source capture for a staged financial configuration handoff.

This core SPOT image preserves original bytes and permanent IDs. It is not a
complete service backup: ancillary product/action journals and recorder SQL
require their own adapters. Neither an image nor its digest authorizes import,
source retirement, activation or sending. A future publisher must revalidate
the source cut and acquire a distinct signed target/session grant.
"""
import hashlib
import json
from dataclasses import dataclass

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerMode,
    ResourceVersion,
    StateCut,
    StorageConflict,
    StorageError,
    StorageUnavailable,
    UnsupportedBackend,
    fingerprint,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.financial_configuration import StageFinancialConfiguration
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotFinancialRecoveryFacts


@dataclass(frozen=True)
class FinancialConfigurationSource:
    cut: FinancialCut
    checkpoint_bytes: bytes
    journal_images: tuple[tuple[str, bytes], ...]
    facts: SpotFinancialRecoveryFacts

    scope = 'CORE_SPOT'

    def __post_init__(self):
        try:
            if (type(self.cut) is not FinancialCut or type(self.facts) is not SpotFinancialRecoveryFacts
                    or type(self.checkpoint_bytes) is not bytes or type(self.journal_images) is not tuple
                    or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str or type(row[1]) is not bytes
                           for row in self.journal_images)):
                raise ValueError()
            names = tuple(name for name, _ in self.journal_images)
            if names != tuple(sorted(set(names))):
                raise ValueError()
            envelope = json.loads(self.checkpoint_bytes)
            state = envelope['state']
            resources = state['resources']
            expected = dict(self.cut.components)
            actual_cut = StateCut(self.cut.checkpoint.binding, state['revision'], state['event_version'],
                                  tuple(ResourceVersion(name, version) for name, version in sorted(state['versions'].items())),
                                  fingerprint(state))
            if (set(envelope) != {'schema_version', 'policy', 'state'} or envelope['schema_version'] != 1
                    or envelope['policy']['binding'] != self.cut.checkpoint.binding.json()
                    or self.facts.binding != self.cut.checkpoint.binding
                    or actual_cut != self.cut.checkpoint
                    or expected != resources['journal_hashes']
                    or {name: hashlib.sha256(raw).hexdigest() for name, raw in self.journal_images} != expected
                    or not {'session', 'wal', 'reservations', 'subsidy', 'loss'} <= set(names)
                    or state['ownership']['mode'] != 'DRAINING'):
                raise ValueError()
            head = resources['financial_configuration']
            pending = StageFinancialConfiguration.from_json(resources['control_operations'][head['pending_operation_id']]['payload'])
            if pending.source_hash != self.cut.checkpoint.binding.config_hash:
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError, StorageError):
            raise StorageError('CONFIG_HANDOFF_SOURCE_INVALID') from None

    @property
    def pending(self):
        resources = json.loads(self.checkpoint_bytes)['state']['resources']
        return StageFinancialConfiguration.from_json(
            resources['control_operations'][resources['financial_configuration']['pending_operation_id']]['payload'])

    @property
    def unresolved_intents(self):
        commands = json.loads(self.checkpoint_bytes)['state']['resources']['commands']
        return tuple(sorted(row['command']['intent_id'] for row in commands.values()
                            if row['state'] not in ('TERMINAL', 'ABORTED_BEFORE_SEND')))

    def manifest(self):
        """Content identity only; fresh independent account facts stay separate."""
        return {'scope': self.scope, 'cut': self.cut.json(), 'pending_operation_id': self.pending.operation_id,
                'target_config_hash': self.pending.target_hash,
                'checkpoint_sha256': hashlib.sha256(self.checkpoint_bytes).hexdigest(),
                'journals': {name: hashlib.sha256(raw).hexdigest() for name, raw in self.journal_images}}

    @property
    def content_digest(self):
        return fingerprint(self.manifest())


class LegacyConfigurationHandoffAdapter:
    def _configuration_handoff_source(self, facts, cut):
        self.assert_worker_thread()
        if self._operator_keys is None or self._recovery_policy is None or self._accounting_policy is None:
            raise UnsupportedBackend('FINANCIAL_CONFIG_POLICIES_REQUIRED')
        self._assert_binding(facts.binding)
        self._assert_binding(cut.checkpoint.binding)
        meta, view = self._read(), self._snapshot()
        if cut != view.cut or meta.cut != cut.checkpoint:
            raise StorageConflict('SPOT_CUT_STALE')
        configuration = self._financial_configuration()
        if configuration.pending is None or meta.ownership.mode is not OwnerMode.DRAINING:
            raise StorageError('CONFIG_HANDOFF_STAGED_SOURCE_REQUIRED')
        now = utc(self._clock())
        if (self._incomplete(meta.resources) or self._reporting_pending(meta.resources)
                or dict(cut.components) != dict(meta.resources['journal_hashes'])
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
        self._check_file()
        with self._path.open('rb') as handle:
            checkpoint = handle.read(self.limits.max_checkpoint_bytes + 1)
        if len(checkpoint) > self.limits.max_checkpoint_bytes:
            raise StorageUnavailable('STORAGE_CAPACITY_EXCEEDED')
        source = FinancialConfigurationSource(cut, checkpoint, tuple(sorted(self._raw().items())), facts)
        if json.loads(source.checkpoint_bytes)['policy'] != self._policy:
            raise StorageUnavailable('CONFIG_HANDOFF_POLICY_MISMATCH')
        # Catch external changes across capture as well as queued local changes
        # before capture. The object remains evidence, not a future write fence.
        if self._read().cut != cut.checkpoint or self._hashes(self._raw()) != dict(cut.components):
            raise StorageConflict('CONFIG_HANDOFF_SOURCE_CHANGED')
        if not 0 <= (utc(self._clock()) - facts.observed_at).total_seconds() * 1000 <= self._recovery_policy.max_fact_age_ms:
            raise StorageError('FINANCIAL_CONFIG_FACTS_UNQUALIFIED')
        return source

    async def configuration_handoff_source(self, binding, *, facts, cut):
        self._assert_binding(binding)
        if type(facts) is not SpotFinancialRecoveryFacts or type(cut) is not FinancialCut:
            raise StorageError('CONFIG_HANDOFF_SOURCE_INVALID')
        return await self._call(self._configuration_handoff_source, facts, cut)

    def _validate_configuration_handoff_source(self, source, facts, cut):
        self._assert_binding(source.cut.checkpoint.binding)
        current = self._configuration_handoff_source(facts, cut)
        if (current.cut != source.cut or current.checkpoint_bytes != source.checkpoint_bytes
                or current.journal_images != source.journal_images):
            raise StorageConflict('CONFIG_HANDOFF_SOURCE_CHANGED')
        return current

    async def validate_configuration_handoff_source(self, source, *, facts, cut):
        if (type(source) is not FinancialConfigurationSource or type(facts) is not SpotFinancialRecoveryFacts
                or type(cut) is not FinancialCut):
            raise StorageError('CONFIG_HANDOFF_SOURCE_INVALID')
        return await self._call(self._validate_configuration_handoff_source, source, facts, cut)
