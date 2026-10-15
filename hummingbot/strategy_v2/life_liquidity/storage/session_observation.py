"""Signed, process-local successor observation; never financial/send authority."""
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerMode,
    OwnerToken,
    StorageConflict,
    StorageError,
    identifier,
    thaw,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import ControlReceipt


@dataclass(frozen=True)
class AuthorizeSessionObservation:
    operation_id: str
    observer_id: UUID
    session_id: str
    epoch: int
    owner: OwnerToken
    expires_at: datetime

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.session_id)
        utc(self.expires_at)
        if (len(self.operation_id) > 96 or not isinstance(self.observer_id, UUID)
                or type(self.epoch) is not int or self.epoch < 1 or not isinstance(self.owner, OwnerToken)
                or (self.session_id, self.epoch) != (str(self.owner.session_id), self.owner.epoch)):
            raise StorageError('SESSION_OBSERVATION_COMMAND_INVALID')

    def json(self):
        return {'kind': 'OBSERVE_SESSION_SUCCESSOR', 'operation_id': self.operation_id,
                'observer_id': str(self.observer_id), 'session_id': self.session_id, 'epoch': self.epoch,
                'owner': self.owner.json(), 'expires_at': self.expires_at.isoformat()}


class LegacySessionObservationAdapter:
    def _authorize_session_observation(self, command, approval, facts, cut):
        if self._operator_keys is None or self._recovery_policy is None:
            raise StorageError('SESSION_OBSERVATION_POLICY_REQUIRED')
        now, meta = utc(self._clock()), self._read()
        prior = meta.resources.get('control_operations', {}).get(command.operation_id)
        if prior is not None:
            if thaw(prior['payload']) != command.json() or prior['approval'] != approval.body():
                raise StorageConflict('CONTROL_OPERATION_CONFLICT')
            # Readback never reinstalls the process-local capability.
            return ControlReceipt(self.binding, command.operation_id, meta.ownership.mode, meta.ownership.token, True)
        self._operator_keys.verify(approval, self.binding, 'OBSERVE_SESSION', command.json(), cut, facts, now)
        view, journal = self._snapshot(), self._sessions.load()
        if view.cut != cut:
            raise StorageConflict('SESSION_OBSERVATION_CUT_STALE')
        if (command.owner != meta.ownership.token or meta.ownership.mode not in (
                OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY)
                or journal.successor is not None or journal.on_expiry != 'switch_to_market_reference'
                or journal.clock_rollback_latched or journal.state not in ('ACTIVE', 'PAUSED', 'TRANSITIONING')
                or (journal.primary.session_id, journal.primary.epoch) != (command.session_id, command.epoch)
                or not journal.primary.expires_at <= now < command.expires_at <= approval.expires_at
                or now < journal.last_seen_at or command.owner.expires_at > journal.primary.expires_at
                or facts.binding != self.binding or not facts.order_scope_complete or not facts.bill_scope_complete
                or not facts.runner_scope_complete or facts.accepted_wire_ids or facts.runner_wire_ids
                or facts.liability_base_life != 0 or facts.liability_quote_usdt != 0
                or not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self._recovery_policy.max_fact_age_ms
                or self._incomplete(meta.resources) or self._reporting_pending(meta.resources)
                or dict(view.cut.components) != dict(meta.resources['journal_hashes'])
                or any(r.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND') for r in self._wal.all_records())):
            raise StorageError('SESSION_OBSERVATION_SCOPE_UNQUALIFIED')
        self._verify_recovery_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                         meta.resources.get('cashflows', {}), facts)
        self._verify_fill_projection(meta.resources['commands'], meta.resources.get('fills', {}))
        self._verify_capital_projection(meta.resources['commands'], meta.resources.get('fills', {}),
                                        meta.resources.get('cashflows', {}))
        grants = thaw(meta.resources.get('session_observation_grants', {}))
        grants[command.operation_id] = command.json()
        replayed = self._control_commit(command.operation_id, cut, approval, command.json(),
                                        {'mode': meta.ownership.mode.value, 'token': meta.ownership.token.json()}, facts,
                                        {'session_observation_grants': grants})
        if not replayed:
            self._session_observation_grant = command.operation_id, str(command.observer_id)
        return ControlReceipt(self.binding, command.operation_id, meta.ownership.mode, meta.ownership.token, replayed)

    def _session_observation_guard(self, view, meta, owner, grant_id):
        self.assert_worker_thread()
        row = meta.resources.get('session_observation_grants', {}).get(grant_id)
        now, journal = utc(self._clock()), self._sessions.load()
        if (row is None or getattr(self, '_session_observation_grant', None) != (grant_id, row['observer_id'])
                or meta.ownership.token != owner or row['owner'] != owner.json()
                or meta.ownership.mode not in (OwnerMode.ACTIVE, OwnerMode.DRAINING, OwnerMode.RECOVERY_ONLY)
                or not owner.valid_from <= now < datetime.fromisoformat(row['expires_at'])
                or journal.successor is not None or journal.clock_rollback_latched or now < journal.last_seen_at
                or now < journal.primary.expires_at or journal.on_expiry != 'switch_to_market_reference'
                or (journal.primary.session_id, journal.primary.epoch) != (row['session_id'], row['epoch'])
                or self._incomplete(meta.resources) or self._reporting_pending(meta.resources)
                or dict(view.cut.components) != dict(meta.resources['journal_hashes'])
                or any(r.state not in ('TERMINAL', 'ABORTED_BEFORE_SEND') for r in self._wal.all_records())):
            raise StorageError('SESSION_OBSERVATION_GRANT_UNQUALIFIED')
        return meta
