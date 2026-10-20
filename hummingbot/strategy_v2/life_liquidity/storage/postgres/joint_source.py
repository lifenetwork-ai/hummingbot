"""Bounded authenticated joint source comparison against existing native facts.

No new monetary observations are applied here. Unexpected exchange facts require
intake/signed recovery; a decoded provider journal cannot recreate this report.
Runner topology remains an owning-application contract, checked through the
original runner journal, not inferred from account or SQL completeness.
"""
import asyncio
import time
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal as D, localcontext

from hummingbot.strategy_v2.life_liquidity.account_bills import SpotAccountBillReader
from hummingbot.strategy_v2.life_liquidity.storage.action_preparation import QuoteAdmissionPolicy
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, utc
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut
from hummingbot.strategy_v2.life_liquidity.storage.postgres.collateral_source import (
    AccountCollateral,
    PgCollateralReconciler,
    _amount,
)

_SCOPE_QUERY = r"""WITH candidates AS MATERIALIZED (
    SELECT 'ORDER' AS kind,t.intent_id::text AS id,jsonb_build_object(
        'intent_id',t.intent_id,'prepare_operation_id',t.operation_id,'wire_id',t.wire_id,'product',t.product,
        'side',t.side,'quantity',t.quantity::text,'price',t.price::text,'state',t.state,
        'exchange_order_id',t.exchange_order_id,'session_id',t.session_id,'epoch',t.epoch,
        'position_action',d.permit->>'position_action') AS body
    FROM life_financial.send_attempts t LEFT JOIN life_financial.swap_preparations d USING(account_id,intent_id)
    WHERE t.account_id=$1
    UNION ALL
    SELECT 'SPOT_FILL',trade_id::text,jsonb_build_object('intent_id',intent_id,'exchange_order_id',exchange_order_id,'fill',observation)
    FROM life_financial.spot_fill_facts WHERE account_id=$1
    UNION ALL
    SELECT 'SWAP_FILL',trade_id::text,jsonb_build_object('intent_id',intent_id,'exchange_order_id',exchange_order_id,'fill',observation)
    FROM life_financial.swap_fill_facts WHERE account_id=$1
    UNION ALL
    SELECT 'TRANSFER',c.bill_id::text,jsonb_build_object('currency',a.asset,'signed_amount',a.signed_amount::text,
        'at_ms',floor(extract(epoch FROM a.transfer_at)*1000)::bigint)
    FROM life_financial.cashflows c JOIN life_financial.bill_approvals a USING(account_id,bill_id) WHERE c.account_id=$1
    UNION ALL
    SELECT 'FUNDING',bill_id::text,jsonb_build_object('signed_amount',signed_payment_usdt::text,'at_ms',settled_at_ms)
    FROM life_financial.funding_cash_payments WHERE account_id=$1
    UNION ALL
    SELECT 'ANCHOR','anchor',jsonb_build_object('bill_id',anchor_bill,'opened_at_ms',floor(extract(epoch FROM opened_at)*1000)::bigint)
    FROM life_financial.spot_capital_policies WHERE account_id=$1
    LIMIT $2+1
), bounded AS (SELECT count(*) AS n,COALESCE(sum(octet_length(body::text)+128),0) AS bytes FROM candidates)
SELECT n,bytes,CASE WHEN n<=$2 AND bytes<=$3 THEN
    (SELECT COALESCE(jsonb_agg(jsonb_build_object('kind',kind,'id',id,'body',body) ORDER BY kind,id),'[]'::jsonb) FROM candidates)
    ELSE NULL END AS rows FROM bounded
"""


@dataclass(frozen=True)
class JointOrderEvidence:
    native_json: str
    exchange_json: str | None
    trade_ids: tuple[str, ...]
    fills_json: str


@dataclass(frozen=True)
class JointSourceReport:
    cut: FinancialCut
    config_version: int
    risk_epoch: int
    observed_at: datetime
    coverage_from_ms: int
    collection_id: str
    source_hash: str
    account: AccountCollateral
    account_observed_at: datetime  # Start of the last fresh private account sample.
    orders: tuple[JointOrderEvidence, ...]
    product_trade_ids: tuple[tuple[str, str], ...]
    cashflow_ids: tuple[str, ...]
    funding_payments: tuple[tuple[str, int, D], ...]
    runner_executor_ids: tuple[str, ...]
    market_json: str | None = None
    market_observed_at: datetime | None = None
    spot_market_json: str | None = None
    spot_market_observed_at: datetime | None = None
    spot_market_monotonic: float | None = None
    account_observed_monotonic: float | None = None
    quote_policy: QuoteAdmissionPolicy | None = None
    primary_observed_at: datetime | None = None
    producer_configuration_hash: str | None = None
    trading_permission: bool = field(default=False, init=False)


class PgJointAccountReconciler(PgCollateralReconciler):
    def __init__(self, reader, swap, *, max_pages, max_age_ms):
        super().__init__(reader, swap)
        if (type(max_pages) is not int or not 0 < max_pages <= 64 or type(max_age_ms) is not int
                or not 0 < max_age_ms <= reader.source.max_uid_age_ms):
            raise StorageError('PG_JOINT_SOURCE_CONFIG_INVALID')
        self.max_pages, self.max_age_ms = max_pages, max_age_ms
        self._lock, self._report = asyncio.Lock(), None

    async def _scope(self, *, spot_policy=False):
        self._original()

        async def read(c):
            query = _SCOPE_QUERY
            if spot_policy:
                # Exact decimal strings, in the same primary scope read. The
                # surrounding native before/after cut already includes this
                # policy; no extra full spot_state pass is needed here.
                query = '''SELECT s.*,(SELECT jsonb_build_object(
                    'policy_hash',p.policy_hash,'objective',p.objective,
                    'min_net_edge_bps',p.min_net_edge_bps::text,'uncertainty_bps',p.uncertainty_bps::text,
                    'tick_size',p.tick_size::text,'lot_size',p.lot_size::text,'min_size',p.min_size::text,
                    'max_fact_age_ms',p.max_fact_age_ms,'max_reference_age_ms',p.max_reference_age_ms,
                    'max_fee_age_ms',p.max_fee_age_ms,'max_capture_age_ms',p.max_capture_age_ms)
                    FROM life_financial.spot_quote_admission_policies p WHERE p.account_id=$1) AS quote_policy,
                    (SELECT CASE WHEN octet_length(c.body::text)<=65536 THEN c.body->'spot_quote_producer' END
                        FROM life_financial.configurations c JOIN life_financial.accounts a
                        ON a.account_id=c.account_id AND a.config_hash=c.config_hash AND a.policy_hash=c.policy_hash
                        WHERE a.account_id=$1) AS quote_producer
                    FROM (''' + query + ') s'
            return await c.fetchrow(query, self.runtime.config.account_id,
                                    self.swap.capital.max_rows, self.swap.capital.max_bytes)
        value = await self.runtime._admit(read, safety=True)
        self._original()
        if value['rows'] is None:
            raise StorageError('PG_JOINT_SOURCE_NATIVE_BOUND')
        result = {kind: {} for kind in ('ORDER', 'SPOT_FILL', 'SWAP_FILL', 'TRANSFER', 'FUNDING', 'ANCHOR')}
        for row in value['rows']:
            if row['id'] in result[row['kind']]:
                raise StorageError('PG_JOINT_SOURCE_NATIVE_CONFLICT')
            result[row['kind']][row['id']] = row['body']
        anchor = result['ANCHOR'].get('anchor')
        if (anchor is None or type(anchor.get('bill_id')) is not str or not anchor['bill_id'].isascii()
                or not anchor['bill_id'].isdecimal() or not 0 < int(anchor['bill_id']) or len(anchor['bill_id']) > 96):
            raise StorageError('PG_JOINT_SOURCE_ANCHOR_REQUIRED')
        if spot_policy:
            p = value['quote_policy']
            if type(p) is not dict or p['policy_hash'] != self.binding.policy_hash:
                raise StorageError('PG_JOINT_SOURCE_QUOTE_POLICY_REQUIRED')
            result['QUOTE_POLICY'] = p
            result['QUOTE_PRODUCER'] = value['quote_producer']
        return result

    async def _collection_native(self, spot_policy):
        if not spot_policy:
            return await self._native()
        clock = self.reader.source.journal.clock
        before, started = utc(clock()), time.monotonic()
        original, view = await self._native()
        if spot_policy:
            after, elapsed = utc(clock()), time.monotonic() - started
            skew_ms = self.reader.source.max_clock_skew_ms
            skew = timedelta(milliseconds=skew_ms)
            snapshot = original['snapshot']
            if (after < before or abs((after - before).total_seconds() - elapsed) * 1000 > skew_ms
                    or not before - skew <= datetime.fromisoformat(snapshot['observed_at']) <= after + skew
                    or snapshot['unverified'] or snapshot['financial_recovery_required']):
                raise StorageError('PG_JOINT_SOURCE_QUOTE_NATIVE_UNQUALIFIED')
        return original, view

    async def _pages(self, read, key, *, anchor=None, first_page=None):
        cursor, previous, result = None, None, []
        for page_number in range(self.max_pages):
            response = (first_page if page_number == 0 and first_page is not None else await read(after=cursor))
            rows = response['data']
            if len(rows) > 100:
                raise StorageError('PG_JOINT_SOURCE_PAGE_INVALID')
            for row in rows:
                value = row.get(key) if type(row) is dict else None
                if (type(value) is not str or not value.isascii() or not value.isdecimal() or not 0 < len(value) <= 96
                        or not 0 < int(value) or previous is not None and int(value) >= previous):
                    raise StorageError('PG_JOINT_SOURCE_CURSOR_INVALID')
                previous = int(value)
                if anchor is not None and previous <= int(anchor):
                    return tuple(result)
                result.append(row)
                if len(result) > self.swap.capital.max_rows:
                    raise StorageError('PG_JOINT_SOURCE_ROW_BOUND')
            if len(rows) < 100:
                return tuple(result)
            cursor = rows[-1][key]
        raise StorageError('PG_JOINT_SOURCE_HISTORY_INCOMPLETE')

    @staticmethod
    def _strings(row, *keys):
        if type(row) is not dict or any(type(row.get(k)) is not str for k in keys):
            raise StorageError('PG_JOINT_SOURCE_WIRE_INVALID')

    def _order(self, row, native, fills, policy):
        self._strings(row, 'instType', 'instId', 'ordId', 'clOrdId', 'state', 'sz', 'px', 'accFillSz', 'side', 'tdMode', 'ordType')
        product = native['product']
        ct = D(policy['contract_value_life']) if product == 'SWAP' else D('1')
        if (row['instType'] != product or row['instId'] != ('LIFE-USDT-SWAP' if product == 'SWAP' else 'LIFE-USDT')
                or row['clOrdId'] != native['wire_id'] or row['side'] != native['side'].lower()
                or native['exchange_order_id'] not in (None, row['ordId']) or not row['ordId'].isascii() or not row['ordId'].isdecimal()
                or not 0 < int(row['ordId']) or row['tdMode'] != ('cross' if product == 'SWAP' else 'cash')
                or row['ordType'] != 'post_only' or row['state'] not in ('live', 'partially_filled', 'filled', 'canceled')
                or native['state'] == 'TERMINAL' and row['state'] not in ('filled', 'canceled')
                or (_amount(row['sz']) * ct, _amount(row['px'])) != (D(native['quantity']), D(native['price']))):
            raise StorageError('PG_JOINT_SOURCE_ORDER_CONFLICT')
        if product == 'SWAP':
            action = native['position_action']
            side = ('long' if native['side'] == 'BUY' else 'short') if action == 'OPEN' else ('short' if native['side'] == 'BUY' else 'long')
            if (row.get('posSide') != ('net' if policy['position_mode'] == 'ONEWAY' else side)
                    or action not in ('OPEN', 'CLOSE') or _amount(row.get('lever')) != policy['leverage']
                    or type(row.get('reduceOnly')) is not bool
                    or row['reduceOnly'] != (policy['position_mode'] == 'ONEWAY' and action == 'CLOSE')):
                raise StorageError('PG_JOINT_SOURCE_ORDER_CONFLICT')
        quantity = sum((D(v['fill']['quantity_base_life']) for v in fills.values() if v['intent_id'] == native['intent_id']), D('0'))
        if (_amount(row['accFillSz']) * ct != quantity or quantity > D(native['quantity'])
                or row['state'] == 'filled' and quantity != D(native['quantity'])
                or row['state'] == 'live' and quantity != 0 or row['state'] == 'partially_filled' and not 0 < quantity < D(native['quantity'])):
            raise StorageError('PG_JOINT_SOURCE_CUMULATIVE_CONFLICT')

    def _fills(self, rows, product, scope, policy):
        expected, found = scope[product + '_FILL'], set()
        ct = D(policy['contract_value_life']) if product == 'SWAP' else D('1')
        for row in rows:
            self._strings(row, 'instType', 'instId', 'tradeId', 'ordId', 'clOrdId', 'side', 'fillSz', 'fillPx', 'feeCcy', 'fee', 'ts')
            item = expected.get(row['tradeId'])
            if item is None or row['tradeId'] in found:
                raise StorageError('PG_JOINT_SOURCE_TRADE_SCOPE')
            found.add(row['tradeId'])
            native, fill = scope['ORDER'][item['intent_id']], item['fill']
            if (row['instType'] != product or row['instId'] != ('LIFE-USDT-SWAP' if product == 'SWAP' else 'LIFE-USDT')
                    or (row['ordId'], row['clOrdId'], row['side']) != (item['exchange_order_id'], native['wire_id'], native['side'].lower())
                    or not row['ts'].isascii() or not row['ts'].isdecimal()
                    or (_amount(row['fillSz']) * ct, _amount(row['fillPx']), row['feeCcy'], _amount(row['fee']), int(row['ts'])) !=
                    (D(fill['quantity_base_life']), D(fill['price_usdt']), fill['fee_currency'], D(fill['signed_fee']), fill['fill_at_ms'])):
                raise StorageError('PG_JOINT_SOURCE_TRADE_CONFLICT')
            if product == 'SWAP':
                self._strings(row, 'fillPnl')
                if _amount(row['fillPnl']) != D(fill['realized_pnl_usdt']):
                    raise StorageError('PG_JOINT_SOURCE_TRADE_CONFLICT')
        return found

    def _bills(self, bills, scope):
        seen, fees, changes, transfers, funding = set(), {}, {}, set(), set()
        for row in bills:
            self._strings(row, 'billId', 'type', 'subType', 'ccy', 'balChg', 'ts')
            if not row['ts'].isascii() or not row['ts'].isdecimal():
                raise StorageError('PG_JOINT_SOURCE_BILL_CONFLICT')
            amount, at = _amount(row['balChg']), int(row['ts'])
            if row['type'] == '1':
                expected = scope['TRANSFER'].get(row['billId'])
                if (expected is None or row.get('ordId') or row.get('tradeId')
                        or (row.get('subType'), row.get('from'), row.get('to')) != (('11', '6', '18') if amount > 0 else ('12', '18', '6'))
                        or (row['ccy'], amount, at) != (expected['currency'], D(expected['signed_amount']), expected['at_ms'])):
                    raise StorageError('PG_JOINT_SOURCE_TRANSFER_CONFLICT')
                transfers.add(row['billId'])
            elif row['type'] == '8':
                expected = scope['FUNDING'].get(row['billId'])
                if (expected is None or row.get('instType') != 'SWAP' or row.get('instId') != 'LIFE-USDT-SWAP'
                        or row.get('ordId') or row.get('tradeId') or row['ccy'] != 'USDT'
                        or row['subType'] != ('173' if amount < 0 else '174')
                        or _amount(row.get('fee')) != 0 or _amount(row.get('pnl')) != amount
                        or (amount, at) != (D(expected['signed_amount']), expected['at_ms'])):
                    raise StorageError('PG_JOINT_SOURCE_FUNDING_CONFLICT')
                funding.add(row['billId'])
            elif row['type'] == '2':
                self._strings(row, 'instType', 'instId', 'tradeId', 'ordId', 'fee')
                product, trade = row['instType'], row['tradeId']
                item = scope.get(product + '_FILL', {}).get(trade)
                if item is None:
                    raise StorageError('PG_JOINT_SOURCE_BILL_UNOWNED')
                native, fill = scope['ORDER'][item['intent_id']], item['fill']
                subtype = ('1' if native['side'] == 'BUY' else '2') if product == 'SPOT' else {
                    ('OPEN', 'BUY'): '3', ('OPEN', 'SELL'): '4', ('CLOSE', 'SELL'): '5', ('CLOSE', 'BUY'): '6'}[native['position_action'], native['side']]
                if (row['instId'] != ('LIFE-USDT' if product == 'SPOT' else 'LIFE-USDT-SWAP') or row['ordId'] != item['exchange_order_id']
                        or row.get('clOrdId') not in (None, '', native['wire_id']) or at != fill['fill_at_ms'] or row['subType'] != subtype
                        or row['ccy'] not in (('LIFE', 'USDT') if product == 'SPOT' else ('USDT',))
                        or product == 'SWAP' and _amount(row.get('pnl')) != D(fill['realized_pnl_usdt'])):
                    raise StorageError('PG_JOINT_SOURCE_BILL_CONFLICT')
                key = (product, trade, row['ccy'])
                seen.add((product, trade))
                fees[key] = fees.get(key, D('0')) + _amount(row['fee'])
                changes[key] = changes.get(key, D('0')) + amount
            else:
                raise StorageError('PG_JOINT_SOURCE_BILL_UNOWNED')
        all_trades = {(product, trade) for product in ('SPOT', 'SWAP') for trade in scope[product + '_FILL']}
        if seen != all_trades or transfers != set(scope['TRANSFER']) or funding != set(scope['FUNDING']):
            raise StorageError('PG_JOINT_SOURCE_BILL_SCOPE')
        for product, trade in all_trades:
            item = scope[product + '_FILL'][trade]
            native, fill = scope['ORDER'][item['intent_id']], item['fill']
            for ccy in (('LIFE', 'USDT') if product == 'SPOT' else ('USDT',)):
                fee = D(fill['signed_fee']) if fill['fee_currency'] == ccy else D('0')
                sign = D('1') if native['side'] == 'BUY' else D('-1')
                principal = (sign * D(fill['quantity_base_life']) if ccy == 'LIFE' else -sign * D(fill['quantity_base_life']) * D(fill['price_usdt'])) if product == 'SPOT' else D(fill['realized_pnl_usdt'])
                if fees.get((product, trade, ccy), D('0')) != fee or changes.get((product, trade, ccy), D('0')) != principal + fee:
                    raise StorageError('PG_JOINT_SOURCE_BILL_CONFLICT')

    async def _round(self, scope, policy, *, check_algos=True):
        anchor = scope['ANCHOR']['anchor']['bill_id']
        # The four fixed first pages still cross the same signed SDK guard and
        # quota individually. Persist them together before ANY parsing; retain
        # every received page even on a malformed response or unknown COMMIT.
        # Cursor continuations and per-order reads remain fully bounded below.
        initial = await self.reader.read_batch((
            ('/api/v5/account/bills-archive', {'limit': '100'}),
            ('/api/v5/trade/fills-history', {'instType': 'SPOT', 'limit': '100'}),
            ('/api/v5/trade/fills-history', {'instType': 'SWAP', 'limit': '100'}),
            ('/api/v5/trade/orders-pending', {'limit': '100'})))
        helper = SpotAccountBillReader(self.reader, anchor_bill_id=anchor, max_pages=self.max_pages, max_fill_pages=self.max_pages)
        bills = await helper.bills_since_anchor(first_page=initial[0])
        if len(bills) > self.swap.capital.max_rows:
            raise StorageError('PG_JOINT_SOURCE_ROW_BOUND')
        self._bills(bills, scope)
        orders, payloads = {}, {'bills': bills, 'fills': {}, 'orders': {}, 'pending': {}}
        for offset, product in enumerate(('SPOT', 'SWAP'), 1):
            read = self.reader.get_all_spot_fill_history_page if product == 'SPOT' else self.reader.get_all_swap_fill_history_page
            rows = await self._pages(read, 'billId', anchor=anchor, first_page=initial[offset])
            if self._fills(rows, product, scope, policy) != set(scope[product + '_FILL']):
                raise StorageError('PG_JOINT_SOURCE_TRADE_SCOPE')
            payloads['fills'][product] = rows
        for intent, native in scope['ORDER'].items():
            if native['state'] in ('PREPARED', 'ABORTED_BEFORE_SEND'):
                if native['exchange_order_id'] is not None or any(v['intent_id'] == intent for p in ('SPOT', 'SWAP') for v in scope[p + '_FILL'].values()):
                    raise StorageError('PG_JOINT_SOURCE_UNSENT_CONFLICT')
                orders[intent] = None
                continue
            product, wire = native['product'], native['wire_id']
            read = self.reader.get_order_by_client_id('LIFE-USDT', wire) if product == 'SPOT' else self.reader.get_swap_order_by_client_id(wire)
            data = (await read)['data']
            if len(data) != 1:
                raise StorageError('PG_JOINT_SOURCE_ORDER_SCOPE')
            row = data[0]
            self._order(row, native, scope[product + '_FILL'], policy)

            async def history(after, order_id=row['ordId'], kind=product):
                return await (self.reader.get_spot_fill_history_page('LIFE-USDT', order_id, after) if kind == 'SPOT'
                              else self.reader.get_swap_fill_history_page(order_id, after))
            rows = await self._pages(history, 'billId')
            if self._fills(rows, product, scope, policy) != {key for key, v in scope[product + '_FILL'].items() if v['intent_id'] == intent}:
                raise StorageError('PG_JOINT_SOURCE_ORDER_TRADE_SCOPE')
            orders[intent] = row
            payloads['orders'][intent] = (row, rows)
        pending = await self._pages(self.reader.get_all_open_orders_page, 'ordId', first_page=initial[3])
        expected = {scope['ORDER'][intent]['wire_id']: (scope['ORDER'][intent], row)
                    for intent, row in orders.items() if row is not None and row['state'] in ('live', 'partially_filled')}
        seen = set()
        for row in pending:
            self._strings(row, 'clOrdId')
            pair = expected.get(row['clOrdId'])
            if pair is None or row['clOrdId'] in seen:
                raise StorageError('PG_JOINT_SOURCE_PENDING_SCOPE')
            self._order(row, pair[0], scope[pair[0]['product'] + '_FILL'], policy)
            if row['ordId'] != pair[1]['ordId'] or row['state'] != pair[1]['state']:
                raise StorageError('PG_JOINT_SOURCE_PENDING_SCOPE')
            seen.add(row['clOrdId'])
        if seen != set(expected):
            raise StorageError('PG_JOINT_SOURCE_PENDING_SCOPE')
        payloads['pending'] = pending
        if check_algos:
            await self._algos()
        return orders, payloads

    async def _algos(self):
        algo_routes = tuple(('/api/v5/trade/orders-algo-pending', {'ordType': kind, 'limit': '100'})
                            for kind in self.reader.SWAP_ALGO_TYPES)
        for page in await self.reader.read_batch(algo_routes):
            if page['data']:
                raise StorageError('PG_JOINT_SOURCE_ALGO_UNOWNED')

    async def _runner(self, scope, preparing_quote=None):
        facts = await self.reader.source.journal._facts()
        now = utc(self.reader.source.journal.clock())
        allowed = set(scope['ORDER'])
        if preparing_quote is not None:
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_application import PgSpotQuoteApplication
            if (type(preparing_quote) is not PgSpotQuoteApplication or preparing_quote.journal is not self.reader.source.journal
                    or preparing_quote.repository is not self.financial or preparing_quote.runtime is not self.runtime):
                raise StorageError('PG_JOINT_SOURCE_PREPARING_CONTEXT')
            action = preparing_quote.preparing_action()
            intent = action.executor_config.id
            if intent in allowed or intent not in facts.executor_ids:
                raise StorageError('PG_JOINT_SOURCE_PREPARING_CONTEXT')
            allowed.add(intent)
        if (not 0 <= (now - facts.observed_at).total_seconds() * 1000 <= self.max_age_ms
                or not set(facts.executor_ids) <= allowed):
            raise StorageError('PG_JOINT_SOURCE_RUNNER_SCOPE')
        return tuple(sorted(facts.executor_ids))

    async def collect(self, *, include_hedge_market=False, include_spot_market=False, preparing_quote=None, quota_continuation=None):
        import json
        if type(include_hedge_market) is not bool or type(include_spot_market) is not bool:
            raise StorageError('PG_JOINT_SOURCE_CONFIG_INVALID')
        if self._lock.locked():
            raise StorageError('PG_JOINT_SOURCE_BACKPRESSURE')
        async with self._lock:
            self._report = None
            try:
                self._original()
                self.reader.source._credentials()
                async with asyncio.timeout(self.reader.timeout_ms / 1000), AsyncExitStack() as stack:
                    if self.reader.source._quota is not None:
                        await stack.enter_async_context(self.reader.source._quota.collection(continuation=quota_continuation))
                    elif quota_continuation is not None:
                        raise StorageError('PG_SHARED_QUOTA_CONTINUATION_INVALID')
                    # No native/provider proof has started before this quota
                    # wait. Pin the epoch before the first native read; any
                    # later ingress invalidates the complete collection.
                    expected_epoch = self.runtime._ingress_epoch + 1
                    with localcontext() as precision:
                        precision.prec = 160
                        original, view = await self._collection_native(include_spot_market)
                        scope = await self._scope(spot_policy=include_spot_market)
                        async with self.reader.collection(account_brackets=True, quota_continuation=quota_continuation):
                            started = self.reader.started_at
                            market_json, market_at = None, None
                            spot_json, spot_at, spot_monotonic, account_monotonic = None, None, None, None
                            first_runner = await self._runner(scope, preparing_quote)
                            rounds, accounts, account_times = [], [], []
                            for _ in range(2):
                                orders, payload = await self._round(scope, original['capital_policy'], check_algos=False)
                                account_times.append(utc(self.reader.source.journal.clock()))
                                account_monotonic = time.monotonic()
                                account, collateral = await self._sample(original['capital_policy'], account_brackets=True)
                                rounds.append({**payload, 'collateral': collateral})
                                accounts.append(account)
                            if first_runner != await self._runner(scope, preparing_quote):
                                raise StorageError('PG_JOINT_SOURCE_CHANGED')
                            if include_hedge_market:
                                # One authenticated collection owns account and
                                # market reads, including its original UID brackets.
                                # A second collection wastes the shared UID budget
                                # and invalidates the first collection's source age.
                                from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import ROUTES
                                market_at = utc(self.reader.source.journal.clock())
                                values = await self.reader.read_batch(ROUTES)
                                market_json = json.dumps([v['data'] for v in values], sort_keys=True)
                            if include_spot_market:
                                from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_market import (
                                    ROUTES as SPOT_ROUTES,
                                )
                                spot_at, spot_monotonic = utc(self.reader.source.journal.clock()), time.monotonic()
                                values = await self.reader.read_batch(SPOT_ROUTES)
                                spot_json = json.dumps([v['data'] for v in values], sort_keys=True)
                            # One complete, later sweep covers all seven algo
                            # types after both independent financial rounds.
                            # Never cache an empty response or omit a product.
                            await self._algos()
                        # Both original signed UID brackets also cover model
                        # agreement. In particular, never discard a closing
                        # account-mode change after the last financial sample.
                        payload = rounds[1]['collateral']
                        accounts[1], rounds[1]['collateral'] = self._parse_sample(
                            self.reader.account_configuration(closing=True), payload['balance'], payload['positions'], original['capital_policy'])
                        if rounds[0] != rounds[1] or accounts[0] != accounts[1]:
                            raise StorageError('PG_JOINT_SOURCE_CHANGED')
                        current, after = await self._collection_native(include_spot_market)
                        self._match(original, view, current, after, accounts[0])
                        if scope != await self._scope(spot_policy=include_spot_market):
                            raise StorageError('PG_JOINT_SOURCE_NATIVE_CHANGED')
                        if self.runtime._ingress_epoch != expected_epoch:
                            raise StorageError('PG_JOINT_SOURCE_INGRESS_CHANGED')
                        rows = tuple(JointOrderEvidence(json.dumps(v, sort_keys=True), None if orders[k] is None else json.dumps(orders[k], sort_keys=True),
                                                        tuple(sorted(t for t, f in scope[v['product'] + '_FILL'].items() if f['intent_id'] == k)),
                                                        json.dumps([f['fill'] for _, f in sorted(scope[v['product'] + '_FILL'].items()) if f['intent_id'] == k], sort_keys=True))
                                     for k, v in sorted(scope['ORDER'].items()))
                        qp = scope.get('QUOTE_POLICY')
                        policy = None if qp is None else QuoteAdmissionPolicy(
                            self.binding, qp['objective'], *(D(qp[k]) for k in (
                                'min_net_edge_bps', 'uncertainty_bps', 'tick_size', 'lot_size', 'min_size')),
                            *(qp[k] for k in ('max_fact_age_ms', 'max_reference_age_ms', 'max_fee_age_ms', 'max_capture_age_ms')))
                        report = JointSourceReport(view.cut, view.session.config_version, original['snapshot']['account']['risk_epoch'],
                                                   started, scope['ANCHOR']['anchor']['opened_at_ms'],
                                                   self.reader.collection_id, fingerprint([rounds, market_json] + ([spot_json] if include_spot_market else [])), accounts[1], account_times[1], rows,
                                                   tuple(sorted((p, key) for p in ('SPOT', 'SWAP') for key in scope[p + '_FILL'])),
                                                   tuple(sorted(scope['TRANSFER'])),
                                                   tuple((k, v['at_ms'], D(v['signed_amount'])) for k, v in sorted(scope['FUNDING'].items())), first_runner,
                                                   market_json, market_at, spot_json, spot_at, spot_monotonic,
                                                   account_monotonic if include_spot_market else None, policy,
                                                   utc(datetime.fromisoformat(current['snapshot']['observed_at'])) if include_spot_market else None,
                                                   fingerprint(scope['QUOTE_PRODUCER']) if include_spot_market else None)
                        self._report, self._epoch = report, expected_epoch
                        return report
            except BaseException as error:
                self.runtime._healthy = False
                if isinstance(error, (StorageError, asyncio.CancelledError)):
                    raise
                raise StorageError('PG_JOINT_SOURCE_INVALID') from None

    def check_report(self, report, *, epoch):
        self._original()
        self.reader.source._credentials()
        now = utc(self.reader.source.journal.clock())
        if (type(report) is not JointSourceReport or report is not self._report or self.reader.collection_id != report.collection_id
                or self.runtime._ingress_epoch != epoch or self.reader._active or not self.reader.source._uid_fresh()
                or not 0 <= (now - report.observed_at).total_seconds() * 1000 <= self.max_age_ms):
            self.runtime._healthy = False
            error = StorageError('PG_JOINT_SOURCE_REPORT_REVOKED')
            error.add_note(str(dict(same_report=report is self._report,
                                    same_collection=self.reader.collection_id == getattr(report, 'collection_id', None),
                                    same_epoch=self.runtime._ingress_epoch == epoch, reader_active=self.reader._active,
                                    uid_fresh=self.reader.source._uid_fresh(),
                                    age_ms=(now - report.observed_at).total_seconds() * 1000
                                    if type(report) is JointSourceReport and report is self._report else None)))
            raise error
