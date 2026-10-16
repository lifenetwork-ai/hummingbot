"""Standalone SPOT preparation and observation ports; no release/send authority."""
from datetime import datetime
from decimal import Decimal as D, InvalidOperation, localcontext

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
from hummingbot.strategy_v2.life_liquidity.storage.session import SessionTick, SessionTickReceipt


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
        value = await self._runtime._admit(read, safety=True)
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

    async def session(self, binding):
        return (await self.snapshot(binding)).session

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
                COALESCE(g.filled_base,0) AS filled_base
                FROM life_financial.send_attempts t
                JOIN life_financial.spot_preparations d USING(account_id,intent_id)
                LEFT JOIN life_financial.spot_accounting_progress g USING(account_id,intent_id)
                LEFT JOIN life_financial.reservations r ON r.account_id=t.account_id AND r.reservation_id=d.operation_id||':principal'
                WHERE t.account_id=$1 AND t.intent_id=$2 AND t.product='SPOT' ''', self._runtime.config.account_id, intent_id)
        return await self._runtime._admit(read, safety=True)

    async def attempt(self, intent_id, binding):
        row = await self._attempt(intent_id, binding)
        if row is None:
            return None
        return SpotAttempt(intent_id, row['wire_id'], intent_id, str(row['session_id']), row['epoch'],
                           AttemptState.SEND_UNKNOWN if row['state'] == 'ARMED' else AttemptState(row['state']),
                           row['exchange_order_id'], False, 'LIFE-USDT', row['side'], row['quote_level'])

    async def reservation(self, intent_id, binding):
        row = await self._attempt(intent_id, binding)
        if row is None:
            return None
        if (row['state'] not in ('PREPARED', 'ARMED', 'SEND_UNKNOWN', 'ACKED') or row['principal_state'] != 'HELD'
                or row['principal_remaining'] != row['principal_expected']):
            raise StorageUnavailable('PG_SPOT_RESERVATION_STATE_UNSUPPORTED')
        with localcontext() as context:
            context.prec = 120
            return SpotReservation(intent_id, row['side'], str(row['session_id']), row['epoch'], row['quantity'],
                                   row['quantity'] - row['filled_base'], row['filled_base'], row['price'], 'OPEN')

    async def lookup_prepare(self, operation_id, binding):
        self._binding(binding)
        identifier(operation_id)

        async def read(c):
            return await c.fetchrow('''SELECT o.receipt,t.state FROM life_financial.operations o
                JOIN life_financial.spot_preparations d USING(account_id,operation_id)
                JOIN life_financial.send_attempts t USING(account_id,intent_id)
                WHERE o.account_id=$1 AND o.operation_id=$2''', self._runtime.config.account_id, operation_id)
        row = await self._runtime._admit(read, safety=True)
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
