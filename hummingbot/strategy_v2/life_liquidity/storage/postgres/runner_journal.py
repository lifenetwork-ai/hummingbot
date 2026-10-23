"""Explicit V2 quote-journal assembly, without connector or send authority.

Runner membership and local policy are supplied by the owning application. They
must be qualified independently; this adapter never infers complete scope from
SQL, creates a live owner, or turns a receipt into a transport permit.
"""
import asyncio
import json
import math
import re
from dataclasses import dataclass

from hummingbot.core.data_type.common import PositionAction, TradeType
from hummingbot.strategy_v2.executors.order_executor.data_types import ExecutionStrategy, OrderExecutorConfig
from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import PrepareQuoteAction
from hummingbot.strategy_v2.life_liquidity.storage.actions import (
    AbandonQuoteAction,
    ClaimQuoteActions,
    RunnerActionFacts,
    SpotQuoteAction,
    TransitionQuoteAction,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    OwnerToken,
    StorageError,
    fingerprint,
    identifier,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import decimal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository
from hummingbot.strategy_v2.life_liquidity.storage.quote_send import ArmQuoteAction
from hummingbot.strategy_v2.models.executor_actions import CreateExecutorAction


@dataclass
class _Entry:
    row: SpotQuoteAction
    dispatch_version: int | None = None
    accepted: bool = False
    revoked: bool = False
    prepare_started: bool = False
    prepared: PrepareQuoteAction | None = None
    arm_started: bool = False
    birth: object | None = None
    dispatch_facts: RunnerActionFacts | None = None


class _RunnerFactCapture:
    """Primary time before a NEW scope sample; never a fact or send grant."""

    def __init__(self, journal, observed_at):
        self._original = journal, asyncio.current_task(), utc(observed_at)
        self.used = False

    def take(self, journal):
        original, task, at = self._original
        if self.used or journal is not original or task is not asyncio.current_task():
            raise StorageError('RUNNER_SCOPE_CAPTURE_ORIGIN_REQUIRED')
        # Observation/reconciliation remains valid after HALT, local revocation
        # or owner expiry. This token grants time/scope sampling only; original
        # dispatch fences and native financial/observation guards still apply.
        self.used = True
        return at


class PgQuoteRunnerJournal:
    """Bounded, process-local runner hooks over permanent PostgreSQL claims.

    Install these hooks only in an explicitly assembled runner. No configured
    backend, planner, executor submit hook or external sender is installed here.
    Replaying a claim after restart cannot recreate local acceptance.
    """
    async_dispatch_enabled = True

    def __init__(self, repository, *, owner, controller_id, config_version, runner_facts, local_ready, clock, max_actions,
                 native_runner_facts=None, birth_repository=None):
        if birth_repository is not None:
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.birth import PgQuoteBirthRepository
            if type(birth_repository) is not PgQuoteBirthRepository or birth_repository.financial is not repository:
                raise StorageError('RUNNER_BIRTH_REPOSITORY_INVALID')
        if (type(repository) is not PgSpotRepository or type(owner) is not OwnerToken
                or not all(callable(v) for v in (runner_facts, local_ready, clock))
                or type(config_version) is not int or not 0 < config_version <= 9223372036854775807
                or type(max_actions) is not int or not 0 < max_actions <= 4096
                or native_runner_facts is not None and not callable(native_runner_facts)):
            raise StorageError('RUNNER_JOURNAL_CONFIG_INVALID')
        identifier(controller_id)
        self.repository, self.binding, self.owner = repository, repository.binding, owner
        self.controller_id, self.runner_facts, self.local_ready, self.clock = controller_id, runner_facts, local_ready, clock
        self.max_actions, self.config_version = max_actions, config_version
        self._entries, self._revoked = {}, False
        self._transport = None
        self._publication = None
        self._native_runner_facts = self._native_runner_original = native_runner_facts
        self.births = self._births_original = birth_repository
        self._lock = asyncio.Lock()

    def _ready(self):
        try:
            now = utc(self.clock())
            return (self.births is self._births_original and not self._revoked and self.owner.valid_from <= now < self.owner.expires_at
                    and self.local_ready() is True)
        except Exception:
            return False

    def _capture(self, action):
        if type(action) is not CreateExecutorAction or type(action.executor_config) is not OrderExecutorConfig:
            raise StorageError('RUNNER_QUOTE_CONFIG_INVALID')
        cfg = action.executor_config
        if (cfg.type != 'order_executor' or action.controller_id != self.controller_id or cfg.controller_id != self.controller_id
                or cfg.connector_name != 'okx' or cfg.trading_pair != 'LIFE-USDT'
                or cfg.side not in (TradeType.BUY, TradeType.SELL) or cfg.position_action != PositionAction.OPEN
                or cfg.execution_strategy != ExecutionStrategy.LIMIT_MAKER or type(cfg.leverage) is not int or cfg.leverage != 1
                or cfg.chaser_config is not None or type(cfg.level_id) is not str
                or re.fullmatch(r'0|[1-9][0-9]{0,9}', cfg.level_id) is None):
            raise StorageError('RUNNER_QUOTE_CONFIG_INVALID')
        if type(cfg.timestamp) not in (float, int) or not math.isfinite(cfg.timestamp) or not 0 < cfg.timestamp <= 253402300799:
            raise StorageError('RUNNER_QUOTE_CONFIG_INVALID')
        for value in (cfg.amount, cfg.price, cfg.slippage_pct, cfg.slippage_multiplier, cfg.max_slippage_pct):
            decimal(value, positive=True)
        if cfg.slippage_multiplier <= 1 or cfg.slippage_pct > cfg.max_slippage_pct:
            raise StorageError('RUNNER_QUOTE_CONFIG_INVALID')
        return SpotQuoteAction(cfg.id, self.controller_id, str(self.owner.session_id), self.owner.epoch,
                               self.config_version, cfg.trading_pair, cfg.side.name, int(cfg.level_id), cfg.model_dump_json())

    def _entry(self, action):
        row = self._capture(action)
        entry = self._entries.get(row.intent_id)
        if entry is None or entry.row != row:
            raise StorageError('RUNNER_CAPTURE_CHANGED_OR_UNKNOWN')
        return entry

    @staticmethod
    def _operation(entry, stage):
        return 'runner:' + stage + ':' + fingerprint(entry.row.json())

    async def _facts(self, *, native_transition=False):
        if native_transition and self._native_runner_original is not None:
            if self._native_runner_facts is not self._native_runner_original:
                raise StorageError('RUNNER_SCOPE_CAPTURE_CONTEXT_CHANGED')

            async def primary_clock(connection):
                return await connection.fetchval('SELECT clock_timestamp()')
            # The original CLAIM/cut read has already completed on this primary.
            # Sample current runner objects only AFTER this timestamp. The
            # timestamp is a conservative lower bound on that new observation,
            # in the SQL verifier's clock domain. No issued fact is re-dated.
            at = utc(await self.repository._runtime._admit(primary_clock, safety=True, operation='runner_scope_clock'))
            capture = _RunnerFactCapture(self, at)
            facts = await self._native_runner_facts(_capture=capture)
            if not capture.used or type(facts) is not RunnerActionFacts or facts.observed_at != at:
                raise StorageError('RUNNER_SCOPE_CAPTURE_REQUIRED')
        else:
            facts = await self.runner_facts()
        if (type(facts) is not RunnerActionFacts or facts.binding != self.binding
                or facts.controller_id != self.controller_id or facts.scope_complete is not True):
            raise StorageError('RUNNER_SCOPE_UNQUALIFIED')
        return facts

    async def preflight_publication(self, operation_id):
        """Read a one-use optimistic cut before capturing a NEW V2 action.

        This is neither a financial claim nor dispatch/send permission. The
        native claim still verifies this exact cut, owner and mode at COMMIT.
        An intervening writer must conflict; no timestamp/cut is refreshed.
        """
        identifier(operation_id)
        async with self._lock:
            if not self._ready() or self._publication is not None:
                raise StorageError('RUNNER_PUBLICATION_PREFLIGHT_UNAVAILABLE')
            if await self.repository.lookup_quote_action(operation_id, self.binding) is not None:
                raise StorageError('RUNNER_CLAIM_REPLAY_REQUIRES_RECOVERY')
            cut = (await self.repository.publication_cut(self.binding)).cut
            self._publication = (object(), asyncio.current_task(), operation_id, cut,
                                 self.repository._runtime._ingress_epoch)
            return self._publication[0]

    async def publish(self, operation_id, actions, *, _publication=None):
        if type(actions) is not tuple or not actions or len(actions) > 64:
            raise StorageError('RUNNER_PUBLICATION_INVALID')
        rows = tuple(self._capture(action) for action in actions)
        command = ClaimQuoteActions(operation_id, rows)
        async with self._lock:
            if not self._ready():
                raise StorageError('RUNNER_LOCAL_REVOKED')
            if len(self._entries) + len(rows) > self.max_actions:
                raise StorageError('RUNNER_JOURNAL_CAPACITY')
            if any(row.intent_id in self._entries for row in rows):
                raise StorageError('RUNNER_IDENTITY_ALREADY_REGISTERED')
            # Freeze original owner/cut/bytes through the physical commit. An
            # ambiguous reply retains SQL claims but registers no local authority.
            if _publication is None:
                if await self.repository.lookup_quote_action(operation_id, self.binding) is not None:
                    raise StorageError('RUNNER_CLAIM_REPLAY_REQUIRES_RECOVERY')
                # The command needs the verified primary cut, not a wallet
                # projection. SQL keeps the same independent spot_state audit.
                cut = (await self.repository.observation_cut(self.binding)).cut
            else:
                cut = self._consume_publication(operation_id, _publication)
            if not self._ready():
                raise StorageError('RUNNER_LOCAL_REVOKED')
            result = await self.repository.claim_quote_actions(command, cut=cut, owner=self.owner)
            if result.replayed:
                raise StorageError('RUNNER_CLAIM_REPLAY_REQUIRES_RECOVERY')
            for row in rows:
                # A single-action, confirmed CLAIM advances exactly once. This
                # is only a CAS version for metadata dispatch, never a cached
                # financial verdict. Any intervening writer must conflict.
                self._entries[row.intent_id] = _Entry(
                    row, dispatch_version=(cut.checkpoint.revision + 1
                                           if len(rows) == 1 and any(m.version == 97 for m in self.repository._runtime._bundle) else None),
                    revoked=not self._ready())
            return result

    def _consume_publication(self, operation_id, ticket):
        original, self._publication = self._publication, None
        if (original is None or original[0] is not ticket or original[1] is not asyncio.current_task()
                or original[2] != operation_id or original[4] != self.repository._runtime._ingress_epoch):
            raise StorageError('RUNNER_PUBLICATION_PREFLIGHT_ORIGIN_REQUIRED')
        return original[3]

    async def capture_and_publish(self, operation_id, template, *, _publication):
        """Create one NEW action from its confirmed native metadata capture.

        The template is never dispatched. An original action is constructed only
        after the backend supplies its one birth timestamp and confirms CLAIM.
        """
        if type(template) is not SpotQuoteAction:
            raise StorageError('RUNNER_PUBLICATION_INVALID')
        async with self._lock:
            if not self._ready():
                raise StorageError('RUNNER_LOCAL_REVOKED')
            if len(self._entries) >= self.max_actions:
                raise StorageError('RUNNER_JOURNAL_CAPACITY')
            if template.intent_id in self._entries:
                raise StorageError('RUNNER_IDENTITY_ALREADY_REGISTERED')
            cut = self._consume_publication(operation_id, _publication)
            result, captured = await self.repository.capture_quote_action(operation_id, template, cut=cut, owner=self.owner)
            if result.replayed:
                raise StorageError('RUNNER_CLAIM_REPLAY_REQUIRES_RECOVERY')
            if captured.json() | {'payload_json': template.payload_json} != template.json():
                raise StorageError('RUNNER_NATIVE_CAPTURE_MISMATCH')
            old, actual = json.loads(template.payload_json), json.loads(captured.payload_json)
            if old.pop('timestamp') != 0 or actual.pop('timestamp') <= 0 or actual != old:
                raise StorageError('RUNNER_NATIVE_CAPTURE_MISMATCH')
            action = CreateExecutorAction(controller_id=captured.controller_id,
                                          executor_config=OrderExecutorConfig.model_validate_json(captured.payload_json))
            if self._capture(action) != captured:
                raise StorageError('RUNNER_NATIVE_CAPTURE_MISMATCH')
            self._entries[captured.intent_id] = _Entry(
                captured, dispatch_version=cut.checkpoint.revision + 1, revoked=not self._ready())
            return action

    async def capture_provisional(self, operation_id, intent, *, model_hash, _publication):
        """One confirmed birth permits only a local, unaccepted executor."""
        async with self._lock:
            if self.births is None or not self._ready() or intent.intent_id in self._entries or len(self._entries) >= self.max_actions:
                raise StorageError('RUNNER_PROVISIONAL_ORIGIN_REQUIRED')
            cut = self._consume_publication(operation_id, _publication)
            ticket = await self.births.issue(operation_id, intent, model_hash=model_hash, expected_version=cut.checkpoint.revision)
            if not self._ready():
                raise StorageError('RUNNER_LOCAL_REVOKED')
            action = CreateExecutorAction(controller_id=ticket.action.controller_id,
                                          executor_config=OrderExecutorConfig.model_validate_json(ticket.action.payload_json))
            if self._capture(action) != ticket.action:
                raise StorageError('RUNNER_NATIVE_CAPTURE_MISMATCH')
            self._entries[ticket.action.intent_id] = _Entry(ticket.action, birth=ticket)
            return action

    def local_runner_create_preflight(self, action):
        try:
            entry = self._entry(action)
            return self._ready() and not entry.revoked and not entry.accepted and not entry.prepare_started
        except Exception:
            return False

    def local_runner_create_fence(self, action):
        try:
            return self._ready() and not self._entry(action).revoked
        except Exception:
            return False

    async def async_authorize_runner_create_action(self, action):
        if not self.local_runner_create_preflight(action):
            return False
        entry = self._entry(action)
        saved = await self.repository.quote_actions(self.binding)
        return (self.local_runner_create_preflight(action)
                and any(row.action == entry.row and row.state == 'PROPOSED' for row in saved))

    async def async_before_runner_dispatch(self, action):
        return self.local_runner_create_preflight(action)

    async def async_on_runner_create_action_dispatched(self, action):
        entry = self._entry(action)
        async with self._lock:
            if not self.local_runner_create_preflight(action):
                return False
            cut = ((await self.repository.observation_cut(self.binding)).cut
                   if entry.birth is None and entry.dispatch_version is None else None)
            facts = await self._facts(native_transition=True)
            if not self.local_runner_create_preflight(action):
                return False
            if entry.row.intent_id not in facts.executor_ids:
                raise StorageError('RUNNER_ACCEPTED_EXECUTOR_MISSING')
            if entry.birth is not None:
                if entry.dispatch_facts is not None or facts.observed_at.timestamp() * 1000 < entry.birth.born_at_ms:
                    raise StorageError('RUNNER_PROVISIONAL_ORIGIN_REQUIRED')
                entry.dispatch_facts = facts
                if self._transport is not None:
                    self._transport.dispatch_finished(action)
                return True  # Local provisional handoff, not acceptance.
            command = TransitionQuoteAction(self._operation(entry, 'dispatch'), entry.row, 'PROPOSED', 'DISPATCHED', facts)
            try:
                if entry.dispatch_version is None:
                    result = await self.repository.transition_quote_action(command, cut=cut, owner=self.owner)
                else:
                    result = await self.repository.transition_quote_action(
                        command, cut=None, owner=self.owner, _claim_version=entry.dispatch_version)
            except BaseException:
                entry.revoked = True
                raise
            entry.accepted = not result.replayed and self.local_runner_create_preflight(action)
            if self._transport is not None:
                self._transport.dispatch_finished(action)
            return entry.accepted

    def on_runner_create_action_rejected(self, action):
        # Synchronous queue overflow/stop/cancellation must not create unbounded
        # detached storage tasks. Keep the durable claim for explicit reconcile.
        key = getattr(getattr(action, 'executor_config', None), 'id', None)
        if key in self._entries:
            self._entries[key].revoked = True
            if self._transport is not None:
                self._transport.dispatch_finished(action)
        return False

    def on_runner_create_action_uncertain(self, action):
        # Creation was invoked. A failed/missing acceptance reply proves neither
        # executor absence nor a safe pre-acceptance rejection.
        return self.on_runner_create_action_rejected(action)

    async def async_on_runner_create_action_rejected(self, action):
        self.on_runner_create_action_rejected(action)
        return await self.reconcile(action)

    def on_runner_stop_action(self, action):
        if action.controller_id == self.controller_id and action.executor_id in self._entries:
            self._entries[action.executor_id].revoked = True

    @staticmethod
    def suppress_create_for_stop_batch():
        return True

    async def prepare_quote_action(self, action, command, *, _source_cut=None):
        entry = self._entry(action)
        provisional = entry.birth is not None and entry.dispatch_facts is not None
        if not (entry.accepted or provisional) or entry.prepare_started or not self.local_runner_create_fence(action):
            raise StorageError('RUNNER_ACCEPTANCE_REQUIRED')
        async with self._lock:
            if not (entry.accepted or provisional) or entry.prepare_started or not self.local_runner_create_fence(action):
                raise StorageError('RUNNER_ACCEPTANCE_REQUIRED')
            if type(command) is not PrepareQuoteAction or command.action != entry.row:
                raise StorageError('RUNNER_PREPARATION_CAPTURE_MISMATCH')
            # An owning native source may already hold the exact verified cut
            # paired with these facts. It is an optimistic token, not authority:
            # SQL must reject any intervening change instead of silently using
            # a newer unrelated snapshot. An original provisional birth may
            # propose its post-birth cut without another snapshot. SQL still
            # audits the current state and rejects any stale/wrong proposal.
            cut = _source_cut
            if cut is None and provisional:
                cut = entry.birth.preparation_cut
            if cut is None:
                cut = (await self.repository.snapshot(self.binding)).cut
            if not (entry.accepted or provisional) or not self.local_runner_create_fence(action):
                raise StorageError('RUNNER_ACCEPTANCE_REQUIRED')
            # One physical attempt. Failures require exact operation readback,
            # never a fresh operation/cut or implicit rearm/resend.
            entry.prepare_started = True
            if provisional:
                try:
                    result = await self.births.prepare(entry.birth, command, entry.dispatch_facts, cut=cut, owner=self.owner)
                except BaseException:
                    entry.revoked = True
                    raise
            else:
                result = await self.repository.prepare_quote_action(command, cut=cut, owner=self.owner)
            if not result.replayed and self.local_runner_create_fence(action):
                entry.prepared = command
                if provisional:
                    entry.accepted = True
            return result

    async def arm_quote_action(self, action, command, *, _source_cut=None):
        entry = self._entry(action)
        async with self._lock:
            if (entry.prepared is None or entry.arm_started or not entry.accepted
                    or not self.local_runner_create_fence(action)):
                raise StorageError('RUNNER_PREPARATION_REQUIRED')
            if (type(command) is not ArmQuoteAction or command.action != entry.row
                    or command.prepare_operation_id != entry.prepared.prepare.operation_id):
                raise StorageError('RUNNER_ARM_CAPTURE_MISMATCH')
            cut = (await self.repository.snapshot(self.binding)).cut if _source_cut is None else _source_cut
            if not self.local_runner_create_fence(action):
                raise StorageError('RUNNER_PREPARATION_REQUIRED')
            # This is durable bookkeeping only. The submit hook remains denied;
            # no transport capability can be reconstructed from this receipt.
            entry.arm_started = True
            return await self.repository.arm_quote_action(command, cut=cut, owner=self.owner)

    async def reconcile(self, action):
        entry = self._entry(action)
        entry.revoked = True
        async with self._lock:
            saved = await self.repository.quote_actions(self.binding)
            record = next((row for row in saved if row.action == entry.row), None)
            if record is None:
                raise StorageError('RUNNER_CAPTURE_CHANGED_OR_UNKNOWN')
            if record.state in ('REJECTED', 'RECONCILED'):
                return None  # Historical state restores no local authority.
            cut = (await self.repository.snapshot(self.binding)).cut
            facts = await self._facts(native_transition=True)
            if entry.row.intent_id in facts.executor_ids:
                raise StorageError('RUNNER_EXECUTOR_STILL_PRESENT')
            if record.state == 'DISPATCHED':
                return await self.repository.abandon_quote_action(
                    AbandonQuoteAction(self._operation(entry, 'retire'), entry.row, facts), cut=cut, owner=self.owner)
            state = 'RECONCILED' if record.state == 'PREPARED' else 'REJECTED'
            command = TransitionQuoteAction(self._operation(entry, 'close'), entry.row, record.state, state, facts)
            return await self.repository.transition_quote_action(command, cut=cut, owner=self.owner)

    def submit_executor_spot_order(self, config, **kwargs):
        # OrderExecutor otherwise falls back to strategy.buy/sell. Even a live
        # connector accidentally supplied to this offline assembly must not send.
        if self._transport is not None:
            return self._transport.submit(config, **kwargs)
        raise StorageError('PG_RUNNER_TRANSPORT_ASSEMBLY_REQUIRED')

    def revoke(self):
        self._revoked = True
        if self._transport is not None:
            self._transport.stop()
