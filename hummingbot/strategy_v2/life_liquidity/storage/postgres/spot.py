"""Standalone SPOT storage ports; no external executor/send authority."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal as D, InvalidOperation, localcontext

from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import (
    PrepareQuoteAction,
    QuoteAdmissionPolicy,
    QuotePolicySnapshot,
)
from hummingbot.strategy_v2.life_liquidity.storage.actions import (
    AbandonQuoteAction,
    ClaimQuoteActions,
    QuoteActionReceipt,
    SpotQuoteAction,
    StoredQuoteAction,
    TransitionQuoteAction,
)
from hummingbot.strategy_v2.life_liquidity.storage.capital import (
    CashflowReceipt,
    CashflowSpot,
    NavReceipt,
    ObserveSpotNAV,
    SpotCapitalView,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import (
    AtomicScope,
    OwnerToken,
    ResourceVersion,
    StateCut,
    StorageError,
    StorageUnavailable,
    fingerprint,
    identifier,
    utc,
)
from hummingbot.strategy_v2.life_liquidity.storage.financial import (
    AckReceipt,
    AckSpot,
    AttemptState,
    FillReceipt,
    FillSpot,
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
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
from hummingbot.strategy_v2.life_liquidity.storage.quote_send import ArmQuoteAction, QuoteArmReceipt
from hummingbot.strategy_v2.life_liquidity.storage.session import SessionTick, SessionTickReceipt
from hummingbot.strategy_v2.life_liquidity.storage.spot_lifecycle import SpotLifecycleCommand, SpotLifecycleReceipt
from hummingbot.strategy_v2.life_liquidity.storage.terminal import TerminalReceipt, TerminalSpot


@dataclass(frozen=True)
class ObservedAccountCut:
    """Primary capture metadata for received facts; no submission authority."""
    cut: FinancialCut
    observed_at: datetime

    def __post_init__(self):
        if type(self.cut) is not FinancialCut:
            raise StorageError('PG_OBSERVATION_CUT_INVALID')
        utc(self.observed_at)


class PgSpotRepository:
    atomic_scope = AtomicScope.ACCOUNT

    def __init__(self, runtime):
        if type(runtime) is not PgRuntime:
            raise StorageError('PG_SPOT_RUNTIME_REQUIRED')
        self._runtime = runtime
        self.binding = runtime.config.binding

    def _binding(self, binding):
        if binding != self.binding:
            raise StorageError('PG_SPOT_BINDING_MISMATCH')

    def _cut(self, cut, owner):
        if type(cut) is not FinancialCut or type(owner) is not OwnerToken or not self._runtime.config.write:
            raise StorageError('PG_SPOT_COMMAND_INVALID')
        self._binding(cut.checkpoint.binding)
        version = cut.checkpoint.revision
        if (cut.checkpoint.event_version != version
                or cut.checkpoint.resource_versions != (ResourceVersion('account', version),)
                or cut.components != (('postgres_account', cut.checkpoint.state_digest),)):
            raise StorageError('PG_SPOT_CUT_INVALID')
        return version, cut.checkpoint.state_digest

    def _receipt(self, value):
        if value.get('trading_permission') is not False or value.get('kind') not in ('SPOT_PREPARE', 'SPOT_ARM'):
            raise StorageUnavailable('PG_SPOT_RECEIPT_INVALID')
        return PrepareReceipt(self.binding, value['operation_id'], value['intent_id'], value['state'], value['replayed'], self.atomic_scope)

    async def snapshot(self, binding):
        self._binding(binding)

        async def read(c):
            return await c.fetchval('SELECT life_financial.spot_state($1)', self._runtime.config.account_id)
        value = await self._runtime._admit(read, safety=True, operation='spot_state')
        try:
            # Schema allows 38 integer + 18 fractional digits. Never use the
            # ambient application's smaller Decimal context for financial reads.
            with localcontext() as context:
                context.prec = 120
                return self._snapshot(value, binding)
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise StorageUnavailable('PG_SPOT_STATE_INVALID') from None

    def _snapshot(self, value, binding):
        a, s, p = value['account'], value['session'], value['policy']
        b, q = value['balances']['LIFE'], value['balances']['USDT']
        # SQL returns all monetary fields as exact decimal strings.
        budgets = {(row['family'], row['scope']): row for row in value['budgets']}

        def amount(family, scope, key):
            return D(budgets.get((family, scope), {}).get(key, '0'))
        subsidy = {scope: amount('SUBSIDY', scope, 'committed') + amount('SUBSIDY', scope, 'incurred') for scope in ('SESSION', 'DAY', 'CAMPAIGN')}
        loss = {scope: amount('LOSS', scope, 'incurred') for scope in ('SESSION', 'DAY', 'CAMPAIGN')}
        limits = {scope: D(str(p['subsidy_' + scope.lower()])) for scope in subsidy}
        exhausted = any(loss[scope] + amount('LOSS', scope, 'committed') >= D(str(p['loss_' + scope.lower()])) for scope in loss)
        cut = StateCut(binding, a['version'], a['version'], (ResourceVersion('account', a['version']),), value['digest'])
        observed = datetime.fromisoformat(value['observed_at'])
        started, expires = datetime.fromisoformat(s['started_at']), datetime.fromisoformat(s['expires_at'])
        lifecycle = value['session_lifecycle']
        active = a['mode'] == 'ACTIVE' and not a['halted'] and started <= observed < expires
        state = lifecycle['state'] if lifecycle is not None else 'PAUSED'
        last_seen = datetime.fromisoformat(lifecycle['last_seen_at']) if lifecycle is not None else started
        rollback = lifecycle['rollback_latched'] if lifecycle is not None else False
        if state == 'ACTIVE' and (not active or observed < last_seen or rollback or value['unverified']):
            state = 'PAUSED'
        if any(type(v) is not str for v in s['anchors'].values()):
            raise StorageUnavailable('PG_SPOT_ANCHOR_ENCODING_INVALID')
        anchors = {k: D(v) for k, v in s['anchors'].items()}
        if any(not v.is_finite() or v <= 0 for v in anchors.values()):
            raise StorageUnavailable('PG_SPOT_ANCHOR_VALUE_INVALID')
        return SpotSnapshot(
            FinancialCut(cut, (('postgres_account', value['digest']),)),
            SpotSession(s['session_id'], s['epoch'], s['config_version'], started, expires,
                        state, last_seen, rollback, anchors),
            SpotBalance(D(b['total']), D(q['total']), D(b['reserved']), D(q['reserved']),
                        max(D('0'), D(b['total']) - D(b['reserved']) - D(b['liability'])),
                        max(D('0'), D(q['total']) - D(q['reserved']) - D(q['liability'])), D(b['fees']), D(q['fees'])),
            SpotBudget(subsidy['SESSION'], subsidy['DAY'], subsidy['CAMPAIGN'],
                       max(D('0'), min(limits[k] - subsidy[k] for k in limits)),
                       loss['SESSION'], loss['DAY'], loss['CAMPAIGN'], exhausted),
            value['recovery_required'])

    async def observation_cut(self, binding):
        """One primary statement supplies both the exact cut and DB capture time.

        Received ACKs must remain accountable after local clock changes. The
        SQL observation guard still rejects actual primary clock rollback,
        changed original ownership, stale cuts and inconsistent accounting.
        """
        self._binding(binding)

        async def read(c):
            return await c.fetchrow("""WITH current_state AS MATERIALIZED (
                SELECT life_financial.spot_state($1) AS v)
                SELECT v#>>'{account,version}' AS version,v->>'digest' AS digest,
                    v->>'observed_at' AS observed_at FROM current_state""", self._runtime.config.account_id)
        value = await self._runtime._admit(read, safety=True)
        try:
            version = int(value['version'])
            checkpoint = StateCut(binding, version, version, (ResourceVersion('account', version),), value['digest'])
            return ObservedAccountCut(FinancialCut(checkpoint, (('postgres_account', value['digest']),)),
                                      datetime.fromisoformat(value['observed_at']))
        except (KeyError, TypeError, ValueError):
            raise StorageUnavailable('PG_OBSERVATION_CUT_INVALID') from None

    async def session(self, binding):
        return (await self.snapshot(binding)).session

    async def quote_admission_policy(self, binding):
        """Exact imported decimals and one compact, verified primary cut.

        SQL still performs the existing retained-state verification. The caller
        receives no history materialization or ownership/send capability.
        """
        self._binding(binding)

        async def read(c):
            return await c.fetchrow('''WITH current_state AS MATERIALIZED (
                SELECT life_financial.spot_state($1) AS v)
                SELECT p.*,v#>>'{account,version}' AS version,v->>'digest' AS digest,
                    v->>'observed_at' AS observed_at,v->>'unverified' AS unverified
                FROM current_state CROSS JOIN life_financial.spot_quote_admission_policies p
                WHERE p.account_id=$1''', self._runtime.config.account_id)
        row = await self._runtime._admit(read, safety=True)
        if row is None:
            raise StorageError('PG_QUOTE_ADMISSION_POLICY_REQUIRED')
        if row['policy_hash'] != binding.policy_hash or row['unverified'] != 'false':
            raise StorageUnavailable('PG_QUOTE_POLICY_UNVERIFIED')
        policy = QuoteAdmissionPolicy(binding, *(row[key] for key in (
            'objective', 'min_net_edge_bps', 'uncertainty_bps', 'tick_size', 'lot_size', 'min_size',
            'max_fact_age_ms', 'max_reference_age_ms', 'max_fee_age_ms', 'max_capture_age_ms')))
        version = int(row['version'])
        cut = StateCut(binding, version, version, (ResourceVersion('account', version),), row['digest'])
        return QuotePolicySnapshot(policy, FinancialCut(cut, (('postgres_account', row['digest']),)),
                                   datetime.fromisoformat(row['observed_at']))

    async def balance(self, binding):
        return (await self.snapshot(binding)).balance

    async def budget(self, binding):
        return (await self.snapshot(binding)).budget

    async def _attempt(self, intent_id, binding):
        self._binding(binding)
        identifier(intent_id)

        async def read(c):
            return await c.fetchrow('''SELECT t.*,d.quote_level,d.operation_id AS prepare_id,
                r.amount AS principal_amount,r.remaining AS principal_remaining,r.state AS principal_state,
                CASE WHEN t.side='BUY' THEN (t.quantity-COALESCE(g.filled_base,0))*t.price ELSE t.quantity-COALESCE(g.filled_base,0) END AS principal_expected,
                COALESCE(g.filled_base,0) AS filled_base,
                c.intent_id IS NOT NULL AS cancel_requested,COALESCE(c.cancel_attempts,0) AS cancel_attempts,c.last_cancel_attempt_at
                FROM life_financial.send_attempts t
                JOIN life_financial.spot_preparations d USING(account_id,intent_id)
                LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
                LEFT JOIN life_financial.spot_cancel_progress c USING(account_id,intent_id)
                LEFT JOIN life_financial.reservations r ON r.account_id=t.account_id AND r.reservation_id=d.operation_id||':principal'
                WHERE t.account_id=$1 AND t.intent_id=$2 AND t.product='SPOT' ''', self._runtime.config.account_id, intent_id)
        return await self._runtime._admit(read, safety=True)

    async def attempt(self, intent_id, binding):
        row = await self._attempt(intent_id, binding)
        if row is None:
            return None
        return SpotAttempt(intent_id, row['wire_id'], intent_id, str(row['session_id']), row['epoch'],
                           AttemptState.SEND_UNKNOWN if row['state'] == 'ARMED' else AttemptState(row['state']),
                           row['exchange_order_id'], row['cancel_requested'], 'LIFE-USDT', row['side'], row['quote_level'],
                           row['cancel_attempts'], row['last_cancel_attempt_at'])

    async def reservation(self, intent_id, binding):
        row = await self._attempt(intent_id, binding)
        if row is None:
            return None
        closed = row['state'] in ('TERMINAL', 'ABORTED_BEFORE_SEND')
        if (row['state'] not in ('PREPARED', 'ARMED', 'SEND_UNKNOWN', 'ACKED', 'TERMINAL', 'ABORTED_BEFORE_SEND')
                or row['principal_state'] != ('TERMINAL' if closed else 'HELD')
                or row['principal_remaining'] != (0 if closed else row['principal_expected'])):
            raise StorageUnavailable('PG_SPOT_RESERVATION_STATE_UNSUPPORTED')
        with localcontext() as context:
            context.prec = 120
            return SpotReservation(intent_id, row['side'], str(row['session_id']), row['epoch'], row['quantity'],
                                   D('0') if closed else row['quantity'] - row['filled_base'], row['filled_base'], row['price'],
                                   'TERMINAL' if closed else 'CANCEL_PENDING' if row['cancel_requested'] else 'OPEN')

    async def lookup_prepare(self, operation_id, binding):
        self._binding(binding)
        identifier(operation_id)

        async def read(c):
            return await c.fetchrow('''SELECT o.receipt,t.state,
                o.command->>'kind'='SPOT_PREPARE' AND o.command->'command'=d.request AND
                o.receipt=(jsonb_build_object('kind','SPOT_PREPARE','operation_id',d.operation_id,'intent_id',d.intent_id,
                    'state','PREPARED','version',o.committed_version,'replayed',false,'trading_permission',false)
                    ||CASE WHEN o.command ? 'action' THEN jsonb_build_object(
                        'states',jsonb_build_array(jsonb_build_array(d.intent_id,'PREPARED'))) ELSE '{}'::jsonb END) AS verified
                FROM life_financial.operations o
                JOIN life_financial.spot_preparations d USING(account_id,operation_id)
                JOIN life_financial.send_attempts t USING(account_id,intent_id)
                WHERE o.account_id=$1 AND o.operation_id=$2''', self._runtime.config.account_id, operation_id)
        row = await self._runtime._admit(read, safety=True)
        if row is not None and row['verified'] is not True:
            raise StorageUnavailable('PG_SPOT_RECEIPT_UNVERIFIED')
        return None if row is None else self._receipt({**row['receipt'], 'state': row['state'], 'replayed': True})

    async def prepare(self, command, *, cut, owner):
        if type(command) is not PrepareSpot:
            raise StorageError('PG_SPOT_COMMAND_INVALID')
        version, digest = self._cut(cut, owner)
        cfg = self._runtime.config
        identity = fingerprint({'kind': 'SPOT_PREPARE', 'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        args = (cfg.account_id, self.binding.json(), owner.json(), version, digest, identity, command.json())
        return await self._runtime._transaction('SELECT life_financial.spot_prepare($1,$2,$3,$4,$5,$6,$7)', args, owner, self._receipt)

    async def arm(self, operation_id, *, cut, owner):
        identifier(operation_id)
        version, digest = self._cut(cut, owner)
        cfg = self._runtime.config
        identity = fingerprint({'kind': 'SPOT_ARM', 'prepare': operation_id, 'cut': cut.json(), 'owner': owner.json()})
        args = (cfg.account_id, self.binding.json(), owner.json(), version, digest, identity, operation_id)
        return await self._runtime._transaction('SELECT life_financial.spot_arm($1,$2,$3,$4,$5,$6,$7)', args, owner, self._receipt)

    async def close(self):
        await self._runtime.close()

    def _observation_receipt(self, value):
        if value.get('trading_permission') is not False:
            raise StorageUnavailable('PG_OBSERVATION_RECEIPT_INVALID')
        if value.get('kind') == 'SPOT_ACK':
            return AckReceipt(self.binding, value['operation_id'], value['intent_id'], value['exchange_order_id'],
                              value['state'], value['replayed'], self.atomic_scope)
        if value.get('kind') == 'SPOT_FILL':
            return FillReceipt(self.binding, value['operation_id'], value['intent_id'], D(value['cumulative_base_life']),
                               D(value['execution_loss_usdt']), value['state'], value['replayed'], self.atomic_scope)
        raise StorageUnavailable('PG_OBSERVATION_RECEIPT_INVALID')

    async def _observe(self, command, cut, owner, kind):
        version, digest = self._cut(cut, owner)
        cfg = self._runtime.config
        identity = fingerprint({'kind': kind, 'command': command.json(), 'owner': owner.json(), 'binding': self.binding.json()})
        args = (cfg.account_id, self.binding.json(), owner.json(), version, digest, identity, kind, command.json())
        query = 'SELECT life_financial.spot_observe($1,$2,$3,$4,$5,$6,$7,$8)'
        return await self._runtime._transaction(query, args, owner, self._observation_receipt, observation=True)

    async def acknowledge(self, command, *, cut, owner):
        if type(command) is not AckSpot:
            raise StorageError('PG_OBSERVATION_COMMAND_INVALID')
        return await self._observe(command, cut, owner, 'SPOT_ACK')

    async def apply_fills(self, command, *, cut, owner):
        if type(command) is not FillSpot:
            raise StorageError('PG_OBSERVATION_COMMAND_INVALID')
        return await self._observe(command, cut, owner, 'SPOT_FILL')

    async def _capital_command(self, command, cut, owner, kind):
        version, digest = self._cut(cut, owner)
        cfg = self._runtime.config
        identity = fingerprint({'kind': kind, 'command': command.json(), 'owner': owner.json(), 'binding': self.binding.json()})
        args = (cfg.account_id, self.binding.json(), owner.json(), version, digest, identity, kind, command.json())
        result = await self._runtime._transaction(
            'SELECT life_financial.spot_capital_command($1,$2,$3,$4,$5,$6,$7,$8)', args, owner, lambda value: value, observation=True)
        if result.get('trading_permission') is not False or result.get('kind') != kind:
            raise StorageUnavailable('PG_CAPITAL_RECEIPT_INVALID')
        return result

    async def apply_cashflow(self, command, *, cut, owner):
        if type(command) is not CashflowSpot:
            raise StorageError('PG_CAPITAL_COMMAND_INVALID')
        result = await self._capital_command(command, cut, owner, 'SPOT_CASHFLOW')
        return CashflowReceipt(self.binding, result['operation_id'], result['bill_id'], result['state'], result['replayed'], self.atomic_scope)

    async def lookup_cashflow(self, operation_id, binding):
        self._binding(binding)
        identifier(operation_id)

        async def read(c):
            return await c.fetchval("SELECT receipt FROM life_financial.operations WHERE account_id=$1 AND operation_id=$2 AND command->>'kind'='SPOT_CASHFLOW'",
                                    self._runtime.config.account_id, operation_id)
        result = await self._runtime._admit(read, safety=True)
        if result is None:
            return None
        if result.get('trading_permission') is not False:
            raise StorageUnavailable('PG_CAPITAL_RECEIPT_INVALID')
        return CashflowReceipt(binding, result['operation_id'], result['bill_id'], result['state'], True, self.atomic_scope)

    async def _capital_view(self, binding, operation_id=None):
        self._binding(binding)

        async def read(c):
            return await c.fetchval('SELECT life_financial.spot_capital_read($1,$2)', self._runtime.config.account_id, operation_id)
        result = await self._runtime._admit(read, safety=True)
        if result is None:
            return None
        try:
            with localcontext() as context:
                context.prec = 120
                snapshot = self._snapshot(result['snapshot'], binding)
                command = result['command']
                return SpotCapitalView(binding, snapshot.cut, **{key: D(value) for key, value in result['measurement'].items()},
                                       independent_price_usdt=D(command['independent_price_usdt']), value_at_ms=command['value_at_ms'],
                                       observed_at=datetime.fromisoformat(command['observed_at']), current=result['current'])
        except (KeyError, TypeError, ValueError, InvalidOperation):
            raise StorageUnavailable('PG_CAPITAL_VIEW_INVALID') from None

    async def capital(self, binding):
        return await self._capital_view(binding)

    async def observe_nav(self, command, *, cut, owner):
        if type(command) is not ObserveSpotNAV:
            raise StorageError('PG_CAPITAL_COMMAND_INVALID')
        result = await self._capital_command(command, cut, owner, 'SPOT_NAV')
        capital = await self._capital_view(self.binding, command.operation_id)
        if capital is None:
            raise StorageUnavailable('PG_CAPITAL_VIEW_MISSING')
        return NavReceipt(self.binding, command.operation_id, capital, result['replayed'], self.atomic_scope)

    async def _lookup_observation(self, operation_id, binding, kind):
        self._binding(binding)
        identifier(operation_id)

        async def read(c):
            return await c.fetchval('''SELECT receipt FROM life_financial.operations
                WHERE account_id=$1 AND operation_id=$2 AND command->>'kind'=$3''', self._runtime.config.account_id, operation_id, kind)
        value = await self._runtime._admit(read, safety=True)
        return None if value is None else self._observation_receipt({**value, 'replayed': True})

    async def lookup_ack(self, operation_id, binding):
        return await self._lookup_observation(operation_id, binding, 'SPOT_ACK')

    async def lookup_fill(self, operation_id, binding):
        return await self._lookup_observation(operation_id, binding, 'SPOT_FILL')

    def _session_receipt(self, value):
        if value.get('kind') != 'SPOT_SESSION_TICK' or value.get('trading_permission') is not False:
            raise StorageUnavailable('PG_SESSION_RECEIPT_INVALID')
        return SessionTickReceipt(self.binding, value['operation_id'], value['state'], value['reason_code'],
                                  value['replayed'], self.atomic_scope)

    async def tick_session(self, command, *, cut, owner):
        if type(command) is not SessionTick:
            raise StorageError('PG_SESSION_COMMAND_INVALID')
        version, digest = self._cut(cut, owner)
        identity = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        args = (self._runtime.config.account_id, self.binding.json(), owner.json(), version, digest, identity, command.json())
        return await self._runtime._transaction('SELECT life_financial.spot_session_tick($1,$2,$3,$4,$5,$6,$7)',
                                                args, owner, self._session_receipt, observation=True)

    async def lookup_session_tick(self, operation_id, binding):
        self._binding(binding)
        identifier(operation_id)

        async def read(c):
            return await c.fetchval("SELECT receipt FROM life_financial.operations WHERE account_id=$1 AND operation_id=$2 AND command->>'kind'='SPOT_SESSION_TICK'",
                                    self._runtime.config.account_id, operation_id)
        value = await self._runtime._admit(read, safety=True)
        return None if value is None else self._session_receipt({**value, 'replayed': True})

    async def pending_session_ticks(self, binding):
        # PostgreSQL publishes the state/event/receipt atomically, with no saga
        # phase to resume. Unknown COMMIT is resolved by the original ID lookup.
        await self.snapshot(binding)
        return ()

    def _order_receipt(self, value):
        if value.get('trading_permission') is not False:
            raise StorageUnavailable('PG_ORDER_RECEIPT_INVALID')
        if value.get('kind') == 'SPOT_LIFECYCLE':
            return SpotLifecycleReceipt(self.binding, value['operation_id'], value['intent_id'], value['state'], value['replayed'], self.atomic_scope)
        if value.get('kind') == 'SPOT_TERMINAL':
            return TerminalReceipt(self.binding, value['operation_id'], value['intent_id'], value['exchange_order_id'],
                                   value['state'], value['subsidy_settled'], value['replayed'], self.atomic_scope)
        raise StorageUnavailable('PG_ORDER_RECEIPT_INVALID')

    async def _order_command(self, command, cut, owner, kind, *, resume=False):
        version, digest = self._cut(cut, owner)
        identity = fingerprint({'kind': kind, 'command': command.json(), 'owner': owner.json(), 'binding': self.binding.json()})
        args = (self._runtime.config.account_id, self.binding.json(), owner.json(), version, digest, identity, kind, command.json(), resume)
        return await self._runtime._transaction('SELECT life_financial.spot_order_command($1,$2,$3,$4,$5,$6,$7,$8,$9)',
                                                args, owner, self._order_receipt, observation=True)

    async def apply_lifecycle(self, command, *, cut, owner):
        if type(command) is not SpotLifecycleCommand:
            raise StorageError('PG_ORDER_COMMAND_INVALID')
        return await self._order_command(command, cut, owner, 'SPOT_LIFECYCLE')

    async def confirm_terminal(self, command, *, cut, owner):
        if type(command) is not TerminalSpot or command.facts.binding != self.binding:
            raise StorageError('PG_TERMINAL_COMMAND_INVALID')
        return await self._order_command(command, cut, owner, 'SPOT_TERMINAL')

    async def resume_terminal(self, command, *, cut, owner):
        if type(command) is not TerminalSpot or command.facts.binding != self.binding:
            raise StorageError('PG_TERMINAL_COMMAND_INVALID')
        # An atomic PG transaction has no partially applied saga. Resume can
        # only read an existing terminal receipt with the original economic core.
        return await self._order_command(command, cut, owner, 'SPOT_TERMINAL', resume=True)

    async def _lookup_order(self, operation_id, binding, kind):
        self._binding(binding)
        identifier(operation_id)

        async def read(c):
            return await c.fetchval("SELECT receipt FROM life_financial.operations WHERE account_id=$1 AND operation_id=$2 AND command->>'kind'=$3",
                                    self._runtime.config.account_id, operation_id, kind)
        value = await self._runtime._admit(read, safety=True)
        return None if value is None else self._order_receipt({**value, 'replayed': True})

    async def lookup_terminal(self, operation_id, binding):
        return await self._lookup_order(operation_id, binding, 'SPOT_TERMINAL')

    async def lookup_lifecycle(self, operation_id, binding):
        return await self._lookup_order(operation_id, binding, 'SPOT_LIFECYCLE')

    def _action_receipt(self, value):
        if value.get('kind') not in ('SPOT_ACTION_CLAIM', 'SPOT_ACTION_TRANSITION', 'SPOT_ACTION_ABANDON') or value.get('trading_permission') is not False:
            raise StorageUnavailable('PG_ACTION_RECEIPT_INVALID')
        return QuoteActionReceipt(self.binding, value['operation_id'], tuple(tuple(row) for row in value['states']),
                                  value['replayed'], self.atomic_scope)

    async def _action_command(self, command, cut, owner, kind):
        version, digest = self._cut(cut, owner)
        identity = fingerprint({'kind': kind, 'command': command.json(), 'owner': owner.json(), 'binding': self.binding.json()})
        args = (self._runtime.config.account_id, self.binding.json(), owner.json(), version, digest, identity, kind, command.json())
        observation = kind in ('SPOT_ACTION_TRANSITION', 'SPOT_ACTION_ABANDON') and command.state != 'DISPATCHED'
        return await self._runtime._transaction('SELECT life_financial.spot_action_command($1,$2,$3,$4,$5,$6,$7,$8)',
                                                args, owner, self._action_receipt, observation=observation)

    async def claim_quote_actions(self, command, *, cut, owner):
        if type(command) is not ClaimQuoteActions:
            raise StorageError('PG_ACTION_COMMAND_INVALID')
        return await self._action_command(command, cut, owner, 'SPOT_ACTION_CLAIM')

    async def transition_quote_action(self, command, *, cut, owner):
        if type(command) is not TransitionQuoteAction or command.facts.binding != self.binding:
            raise StorageError('PG_ACTION_COMMAND_INVALID')
        return await self._action_command(command, cut, owner, 'SPOT_ACTION_TRANSITION')

    async def quote_actions(self, binding):
        self._binding(binding)

        async def read(c):
            return await c.fetchval('SELECT life_financial.spot_action_read($1)', self._runtime.config.account_id)
        rows = await self._runtime._admit(read, safety=True)
        return tuple(StoredQuoteAction(SpotQuoteAction(**row['action']), row['state']) for row in rows)

    async def lookup_quote_action(self, operation_id, binding):
        self._binding(binding)
        identifier(operation_id)

        async def read(c):
            return await c.fetchval("""SELECT receipt FROM life_financial.operations WHERE account_id=$1 AND operation_id=$2
                AND command->>'kind' IN ('SPOT_ACTION_CLAIM','SPOT_ACTION_TRANSITION','SPOT_ACTION_ABANDON')""", self._runtime.config.account_id, operation_id)
        value = await self._runtime._admit(read, safety=True)
        return None if value is None else self._action_receipt({**value, 'replayed': True})

    async def prepare_quote_action(self, command, *, cut, owner):
        if type(command) is not PrepareQuoteAction or command.admission.binding != self.binding:
            raise StorageError('PG_ACTION_PREPARATION_INVALID')
        version, digest = self._cut(cut, owner)
        identity = fingerprint({'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        args = (self._runtime.config.account_id, self.binding.json(), owner.json(), version, digest, identity, command.json())
        return await self._runtime._transaction('SELECT life_financial.spot_action_prepare($1,$2,$3,$4,$5,$6,$7)', args, owner, self._receipt)

    async def abandon_quote_action(self, command, *, cut, owner):
        if type(command) is not AbandonQuoteAction or command.facts.binding != self.binding:
            raise StorageError('PG_ACTION_ABANDONMENT_INVALID')
        return await self._action_command(command, cut, owner, 'SPOT_ACTION_ABANDON')

    def _quote_arm_receipt(self, value):
        if value.get('kind') != 'SPOT_QUOTE_ARM' or value.get('trading_permission') is not False:
            raise StorageUnavailable('PG_QUOTE_ARM_RECEIPT_INVALID')
        return QuoteArmReceipt(self.binding, value['operation_id'], value['intent_id'], value['wire_sha256'],
                               value['state'], value['replayed'])

    async def arm_quote_action(self, command, *, cut, owner):
        if type(command) is not ArmQuoteAction:
            raise StorageError('PG_QUOTE_ARM_COMMAND_INVALID')
        version, digest = self._cut(cut, owner)
        cfg = self._runtime.config
        identity = fingerprint({'kind': 'SPOT_QUOTE_ARM', 'command': command.json(), 'cut': cut.json(), 'owner': owner.json()})
        args = (cfg.account_id, self.binding.json(), owner.json(), version, digest, identity, command.json())
        return await self._runtime._transaction('SELECT life_financial.spot_arm_quote($1,$2,$3,$4,$5,$6,$7)', args, owner, self._quote_arm_receipt)

    async def lookup_quote_arm(self, prepare_operation_id, binding):
        self._binding(binding)
        identifier(prepare_operation_id)

        async def read(c):
            if not await c.fetchval('SELECT life_financial.spot_quote_arms_verified($1)', self._runtime.config.account_id):
                raise StorageUnavailable('PG_QUOTE_SEND_UNVERIFIED')
            return await c.fetchval("""SELECT receipt FROM life_financial.operations WHERE account_id=$1
                AND operation_id=$2 AND command->>'kind'='SPOT_QUOTE_ARM'""", self._runtime.config.account_id, 'arm:' + prepare_operation_id)
        result = await self._runtime._admit(read, safety=True)
        return None if result is None else self._quote_arm_receipt({**result, 'replayed': True})
