"""Authenticated joint intake into the native, account-atomic monetary batch.

Independent historical prices remain explicit owning-service inputs; source
labels are not authentication. This observer never activates, releases a claim,
restores SDK tracking, refreshes allocation capacity or grants an HTTP send.
Native pending batches require signed recovery, not automatic recollection.
"""
import asyncio
from contextlib import AsyncExitStack
from copy import deepcopy
from decimal import ROUND_FLOOR, Decimal as D, localcontext

from hummingbot.strategy_v2.life_liquidity.account_bills import SpotAccountBillReader
from hummingbot.strategy_v2.life_liquidity.carry_monitor import CarryAccount, CarryBundle, CarryFunding, FundingPayment
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
from hummingbot.strategy_v2.life_liquidity.shared_capital import CapitalSnapshot
from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, identifier, utc
from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, SpotFillObservation, decimal
from hummingbot.strategy_v2.life_liquidity.storage.postgres.funding import ApplyFunding
from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import ROUTES, PgHedgeSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_batch import ApplyJointBatch, PgJointBatchRepository
from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_context import PgSpotObservationReader
from hummingbot.strategy_v2.life_liquidity.storage.postgres.swap_fills import FillSwap, SwapFillObservation


class PgJointFinancialObserver:
    def __init__(self, source, *, independent_fill_value, independent_cashflow_value):
        if (type(source) is not PgHedgeSource or not callable(independent_fill_value)
                or not callable(independent_cashflow_value)):
            raise StorageError('PG_JOINT_OBSERVER_CONFIG_INVALID')
        source.assert_binding(observation=True)
        self.source, self.collector = source, source.collector
        self.reader, self.financial, self.runtime = source.reader, source.financial, source.runtime
        self.binding, self.owner = source.binding, source.owner
        self.batch = PgJointBatchRepository(self.collector.swap)
        self.context = PgSpotObservationReader(
            self.financial, max_rows=source.capital.max_rows,
            max_bytes=min(self.reader.max_bytes, source.capital.max_bytes))
        self.fill_value, self.cashflow_value = independent_fill_value, independent_cashflow_value
        self._context = (source, self.collector, self.reader, self.financial, self.runtime, self.binding,
                         self.owner, self.batch, self.context, independent_fill_value, independent_cashflow_value)
        if not hasattr(self.runtime, '_financial_observer_mutex'):
            self.runtime._financial_observer_mutex = asyncio.Lock()
        self._lock = self.runtime._financial_observer_mutex

    def _original(self):
        try:
            if self._context != (self.source, self.collector, self.reader, self.financial, self.runtime, self.binding,
                                 self.owner, self.batch, self.context, self.fill_value, self.cashflow_value):
                raise StorageError('PG_JOINT_OBSERVER_CONTEXT_CHANGED')
            self.source.assert_binding(observation=True)
            self.batch._original()
        except BaseException:
            self._context[4]._healthy = False
            raise

    @staticmethod
    def _value(provider, *args):
        value = provider(*args)
        if type(value) is not IndependentFillObservation:
            raise StorageError('PG_JOINT_OBSERVER_VALUE_UNQUALIFIED')
        return value

    def _extend_fills(self, scope, product, rows, policy, now_ms):
        c = self.collector
        known, found = scope[product + '_FILL'], set()
        for row in rows:
            c._strings(row, 'instType', 'instId', 'tradeId', 'ordId', 'clOrdId', 'side', 'fillSz', 'fillPx', 'feeCcy', 'fee', 'ts')
            trade = row['tradeId']
            if trade in found:
                raise StorageError('PG_JOINT_OBSERVER_DUPLICATE_TRADE')
            found.add(trade)
            if trade in known:
                continue  # The existing strict comparison below checks it.
            candidates = [o for o in scope['ORDER'].values() if o['product'] == product and o['wire_id'] == row['clOrdId']]
            if len(candidates) != 1 or candidates[0]['state'] not in ('SEND_UNKNOWN', 'ACKED', 'TERMINAL'):
                raise StorageError('PG_JOINT_OBSERVER_UNOWNED_TRADE')
            native = candidates[0]
            if not row['ts'].isascii() or not row['ts'].isdecimal():
                raise StorageError('PG_JOINT_OBSERVER_TRADE_TIME_INVALID')
            at = int(row['ts'])
            value = self._value(self.fill_value, product, trade, at)
            if value.observed_at_ms > now_ms or at > now_ms:
                raise StorageError('PG_JOINT_OBSERVER_VALUE_UNQUALIFIED')
            quantity = D(row['fillSz']) * (D(policy['contract_value_life']) if product == 'SWAP' else D('1'))
            args = (trade, quantity, D(row['fillPx']), row['feeCcy'], D(row['fee']), at,
                    value.price_usdt, value.value_at_ms, value.observed_at_ms, value.source_kind)
            if product == 'SWAP':
                c._strings(row, 'fillPnl')
                fill = SwapFillObservation(*args, D(row['fillPnl']), None)
            else:
                fill = SpotFillObservation(*args)
            known[trade] = dict(intent_id=native['intent_id'], exchange_order_id=row['ordId'], fill=fill.json())
        if c._fills(rows, product, scope, policy) != set(known):
            raise StorageError('PG_JOINT_OBSERVER_TRADE_HISTORY_INCOMPLETE')

    def _extend_bills(self, scope, bills, context):
        # Approval signatures were imported independently. Only their exact
        # original asset/amount/economic time can become transfer observations.
        approvals = {v.bill_id: v for v in context.policy.approved_cashflows}
        for row in bills:
            self.collector._strings(row, 'billId', 'type', 'subType', 'ccy', 'balChg', 'ts')
            key = row['billId']
            if row['type'] == '1' and key not in scope['TRANSFER']:
                approval = approvals.get(key)
                if approval is None:
                    raise StorageError('PG_JOINT_OBSERVER_TRANSFER_UNAPPROVED')
                scope['TRANSFER'][key] = dict(currency=approval.currency, signed_amount=str(approval.signed_amount),
                                              at_ms=approval.transfer_at_ms)
            elif row['type'] == '8' and key not in scope['FUNDING']:
                if not row['ts'].isascii() or not row['ts'].isdecimal():
                    raise StorageError('PG_JOINT_OBSERVER_FUNDING_TIME_INVALID')
                scope['FUNDING'][key] = dict(signed_amount=row['balChg'], at_ms=int(row['ts']))
        self.collector._bills(bills, scope)

    def _commands(self, original_scope, scope, context, observed):
        commands = {'SPOT': [], 'SWAP': []}
        for product in commands:
            facts = scope[product + '_FILL']
            for intent, native in sorted(scope['ORDER'].items()):
                keys = sorted(k for k, v in facts.items() if v['intent_id'] == intent)
                if not keys or not any(k not in original_scope[product + '_FILL'] for k in keys):
                    continue
                kind, command = (SwapFillObservation, FillSwap) if product == 'SWAP' else (SpotFillObservation, FillSpot)
                fills = tuple(kind.from_json(facts[k]['fill']) for k in keys)
                exchange_ids = {facts[k]['exchange_order_id'] for k in keys}
                if len(exchange_ids) != 1:
                    raise StorageError('PG_JOINT_OBSERVER_ORDER_ID_CONFLICT')
                op = 'joint-observed:' + fingerprint([product, intent, [v.json() for v in fills]])[:48]
                commands[product].append(command(
                    op, native['prepare_operation_id'], intent, native['wire_id'], 'LIFE-USDT', native['side'],
                    native['session_id'], native['epoch'], next(iter(exchange_ids)),
                    sum((v.quantity_base_life for v in fills), D('0')), fills, observed))
        flows = []
        for key in sorted(set(scope['TRANSFER']) - set(original_scope['TRANSFER'])):
            row = scope['TRANSFER'][key]
            at, currency = row['at_ms'], row['currency']
            value = (IndependentFillObservation(D('1'), at, at, 'quote_unit') if currency == 'USDT'
                     else self._value(self.cashflow_value, key, at))
            flows.append(CashflowSpot(
                'joint-bill:' + key, key, currency, D(row['signed_amount']), at,
                value.price_usdt, value.value_at_ms, value.observed_at_ms, value.source_kind, observed))
        return tuple(commands['SPOT']), tuple(commands['SWAP']), tuple(flows)

    def _bundle(self, account, account_at, data, received, cap, carry, scope, native):
        sequences = [0 if cap['snapshot'] is None else cap['snapshot']['sequence']]
        if carry['bundle'] is not None:
            sequences.extend(carry['bundle'][k]['sequence'] for k in ('mark', 'index', 'funding', 'terms'))
            sequences.append(carry['bundle']['account']['snapshot']['sequence'])
        # Ordinary accounting deliberately does not refresh allocation/carry
        # capacity. Its original funding streams can therefore be ahead of the
        # last carry check; a successor collection must advance those streams
        # too, without rewriting any previous source or restarting sequences.
        for row in native['paid_funding']['operations']:
            if row['state'] == 'APPLIED':
                bundle = row['bundle']
                sequences.extend(bundle[k]['sequence'] for k in ('mark', 'index', 'funding', 'terms'))
                sequences.append(bundle['account']['snapshot']['sequence'])
        sequence = 1 + max(sequences)
        _, mark, index, rate, settlement, following, funding_at, terms = self.source._market(
            data, cap['policy'], carry['policy'], sequence, received)
        marked = account.cash_usdt + account.long_base * (mark.price_usdt - account.long_entry)
        marked += account.short_base * (account.short_entry - mark.price_usdt)
        collateral = min(account.collateral_usdt, marked).quantize(D('1e-18'), rounding=ROUND_FLOOR)
        cp = cap['policy']
        snapshot = CapitalSnapshot(self.binding.account_uid, '2', 'USDT', cp['position_mode'], 'cross', cp['leverage'],
                                   account_at, sequence, True, 'okx_account', decimal(account.base_life), decimal(account.cash_usdt),
                                   decimal(account.long_base), decimal(account.short_base), decimal(collateral),
                                   decimal(account.initial_margin_usdt), decimal(account.maintenance_margin_usdt),
                                   D('0'), mark.price_usdt, index.price_usdt, 'okx_mark', 'okx_index', ())
        payments = tuple(FundingPayment(k, v['at_ms'], D(v['signed_amount'])) for k, v in sorted(scope['FUNDING'].items()))
        return CarryBundle(CarryAccount(snapshot, tuple(v.bill_id for v in payments), True, True), mark, index,
                           CarryFunding(self.binding.account_uid, 'LIFE-USDT-SWAP', sequence, funding_at, rate, settlement, following,
                                        carry['policy']['anchor_ms'], min(account_at, funding_at), payments, True, 'okx_funding'), terms)

    async def observe(self, operation_id):
        identifier(operation_id)
        self._original()
        if self._lock.locked():
            raise StorageError('PG_JOINT_OBSERVER_BACKPRESSURE')
        async with self._lock:
            previous = await self.batch.lookup(operation_id, self.binding)
            if previous is not None:
                return previous  # Diagnostic original readback, never recollect.
            self.runtime._observation_inflight += 1
            try:
                with localcontext() as precision:
                    precision.prec = 160
                    async with asyncio.timeout(self.reader.timeout_ms / 1000), AsyncExitStack() as stack:
                        if self.reader.source._quota is not None:
                            await stack.enter_async_context(self.reader.source._quota.collection())
                        epoch = self.runtime._ingress_epoch + 1
                        original, view = await self.collector._native()
                        original_scope = await self.collector._scope()
                        context = await self.context.observation_context(self.binding)
                        frame = await self.batch.frame(self.binding)
                        if context.cut != view.cut or context.pending or any(v['state'] != 'APPLIED' for v in frame['batches']):
                            raise StorageError('PG_JOINT_OBSERVER_RECOVERY_REQUIRED')
                        cap = await self.source.capital.frame(self.binding)
                        carry = await self.source.carry.frame(self.binding)
                        scope = deepcopy(original_scope)
                        async with self.reader.collection(account_brackets=True):
                            started = self.reader.started_at
                            runner = await self.collector._runner(scope)
                            helper = SpotAccountBillReader(self.reader, anchor_bill_id=context.policy.anchor_bill_id,
                                                           max_pages=self.collector.max_pages, max_fill_pages=self.collector.max_pages)
                            bills = await helper.bills_since_anchor()
                            for product, read in (('SPOT', self.reader.get_all_spot_fill_history_page),
                                                  ('SWAP', self.reader.get_all_swap_fill_history_page)):
                                rows = await self.collector._pages(read, 'billId', anchor=context.policy.anchor_bill_id)
                                self._extend_fills(scope, product, rows, original['capital_policy'],
                                                   int(utc(self.reader.source.journal.clock()).timestamp() * 1000))
                            self._extend_bills(scope, bills, context)
                            rounds, accounts, stamps = [], [], []
                            for _ in range(2):
                                _, payload = await self.collector._round(scope, original['capital_policy'])
                                stamps.append(int(utc(self.reader.source.journal.clock()).timestamp() * 1000))
                                account, raw = await self.collector._sample(original['capital_policy'], account_brackets=True)
                                rounds.append((payload, raw))
                                accounts.append(account)
                            if (rounds[0] != rounds[1] or accounts[0] != accounts[1]
                                    or runner != await self.collector._runner(scope)):
                                raise StorageError('PG_JOINT_OBSERVER_SOURCE_CHANGED')
                            received = int(utc(self.reader.source.journal.clock()).timestamp() * 1000)
                            changed = any(set(scope[k]) != set(original_scope[k])
                                          for k in ('SPOT_FILL', 'SWAP_FILL', 'TRANSFER', 'FUNDING'))
                            # A no-op still requires complete signed private
                            # comparison and original cut checks. It needs no
                            # new public prices/terms or monetary source bundle.
                            data = [v['data'] for v in await self.reader.read_batch(ROUTES)] if changed else None
                        payload, raw = rounds[1]
                        accounts[1], closing_raw = self.collector._parse_sample(
                            self.reader.account_configuration(closing=True), raw['balance'], raw['positions'], original['capital_policy'])
                        rounds[1] = payload, closing_raw
                        if rounds[0] != rounds[1] or accounts[0] != accounts[1]:
                            raise StorageError('PG_JOINT_OBSERVER_SOURCE_CHANGED')
                        now = utc(self.reader.source.journal.clock())
                        self._original()
                        current, after = await self.collector._native()
                        if (view.cut != after.cut or original['positions'] != current['positions']
                                or original_scope != await self.collector._scope() or self.runtime._ingress_epoch != epoch
                                or not 0 <= (now - started).total_seconds() * 1000 <= self.collector.max_age_ms):
                            raise StorageError('PG_JOINT_OBSERVER_SOURCE_CHANGED')
                        spot, swap, flows = self._commands(original_scope, scope, context, now)
                        new_funding = set(scope['FUNDING']) - set(original_scope['FUNDING'])
                        if not (spot or swap or flows or new_funding):
                            self.collector._match(original, view, current, after, accounts[1])
                            return None
                        bundle = self._bundle(accounts[1], stamps[1], data, received, cap, carry, scope, original['snapshot'])
                        command = ApplyJointBatch(operation_id, spot, swap, flows, ApplyFunding('joint-funding:' + fingerprint([operation_id])[:48], bundle))
                        # The native batch retains this original complete body
                        # before monetary application and checks causal money,
                        # positions, PNL, approved flows, fees and gross budgets.
                        # Failure retains pending/HALT, never a partial credit.
                        result = await self.batch.apply(command, cut=view.cut, owner=self.owner)
                        if result.state != 'APPLIED':
                            raise StorageError('PG_JOINT_OBSERVER_RECOVERY_REQUIRED')
                        return result
            except BaseException as error:
                self.runtime._healthy = False
                if isinstance(error, (StorageError, asyncio.CancelledError)):
                    raise
                raise StorageError('PG_JOINT_OBSERVER_UNQUALIFIED') from None
            finally:
                self.runtime._observation_inflight -= 1
