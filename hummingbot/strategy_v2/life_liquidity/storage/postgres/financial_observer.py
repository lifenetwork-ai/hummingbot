"""Authenticated standalone SPOT financial collection and original intake.

This application accounts observations only. It grants no completeness of SDK
runner/shared products, NAV, activation, terminal release, or send authority.
"""
import asyncio
from decimal import Decimal, localcontext

from hummingbot.strategy_v2.life_liquidity.account_bills import SpotAccountBillReader
from hummingbot.strategy_v2.life_liquidity.order_gateway import OkxSpotOrderGateway
from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, fingerprint, utc
from hummingbot.strategy_v2.life_liquidity.storage.financial import FillSpot, RecordedSpotFill
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_ingress import PgFinancialIngress
from hummingbot.strategy_v2.life_liquidity.storage.postgres.financial_source import PgSpotAccountReader
from hummingbot.strategy_v2.life_liquidity.storage.postgres.observation_context import PgSpotObservationReader
from hummingbot.strategy_v2.life_liquidity.storage.spot_financial_parser import SpotFinancialParser


class PgSpotFinancialObserver(SpotFinancialParser):
    def __init__(self, reader, *, independent_fill_value, independent_cashflow_value, max_pages, max_rows, max_bytes):
        if (type(reader) is not PgSpotAccountReader or not callable(independent_fill_value)
                or not callable(independent_cashflow_value) or type(max_pages) is not int or not 0 < max_pages <= 64):
            raise StorageError('PG_FINANCIAL_OBSERVER_CONFIG_INVALID')
        self.reader, self.repository, self.binding = reader, reader.repository, reader.binding
        self.runtime, self.owner = reader.runtime, reader.source.journal.owner
        self.context = PgSpotObservationReader(self.repository, max_rows=max_rows, max_bytes=max_bytes)
        self.intake = PgFinancialIngress(self.repository)
        self.fill_value, self.cashflow_value = independent_fill_value, independent_cashflow_value
        self.max_pages, self.max_rows, self.max_bytes = max_pages, max_rows, max_bytes
        if not hasattr(self.runtime, '_financial_observer_mutex'):
            self.runtime._financial_observer_mutex = asyncio.Lock()
        self._lock = self.runtime._financial_observer_mutex

    async def _pages(self, read, key):
        cursor, previous, result = None, None, []
        for _ in range(self.max_pages):
            response = await read(after=cursor)
            rows = response['data']
            if len(rows) > 100:
                raise StorageError('PG_FINANCIAL_OBSERVER_PAGE_INVALID')
            for row in rows:
                value = row[key]
                if (type(value) is not str or not value.isascii() or not value.isdecimal() or len(value) > 96
                        or previous is not None and int(value) >= previous):
                    raise StorageError('PG_FINANCIAL_OBSERVER_CURSOR_INVALID')
                previous = int(value)
                result.append(row)
            if len(rows) < 100:
                return tuple(result)
            cursor = rows[-1][key]
        raise StorageError('PG_FINANCIAL_OBSERVER_HISTORY_INCOMPLETE')

    @staticmethod
    def _strings(row, fields):
        if type(row) is not dict or any(type(row.get(key)) is not str for key in fields):
            raise StorageError('PG_FINANCIAL_OBSERVER_WIRE_TYPES_INVALID')

    def _qualify_pending_values(self, context):
        applied = {row.fill.trade_id for row in context.fills}
        for pending in context.pending:
            if pending.kind == 'FILL':
                for fill in pending.command.fills:
                    if fill.trade_id not in applied:
                        value = self._price_value(self.fill_value, fill.trade_id, fill.fill_at_ms)
                        if (value.price_usdt, value.value_at_ms, value.observed_at_ms, value.source_kind) != (
                                fill.independent_price_usdt, fill.independent_value_at_ms,
                                fill.independent_observed_at_ms, fill.source_kind):
                            raise StorageError('PG_FINANCIAL_OBSERVER_PENDING_VALUE_UNQUALIFIED')
            elif pending.kind == 'CASHFLOW' and pending.command.currency == 'LIFE':
                command = pending.command
                value = self._price_value(self.cashflow_value, command.bill_id, command.transfer_at_ms)
                if (value.price_usdt, value.value_at_ms, value.observed_at_ms, value.source_kind) != (
                        command.independent_price_usdt, command.value_at_ms, command.reference_observed_at_ms, command.source_kind):
                    raise StorageError('PG_FINANCIAL_OBSERVER_PENDING_VALUE_UNQUALIFIED')

    async def _collect(self, context):
        existing = {row.fill.trade_id: row for row in context.fills}
        for pending in context.pending:
            if pending.owner != self.owner or pending.kind not in ('FILL', 'CASHFLOW'):
                raise StorageError('PG_FINANCIAL_OBSERVER_PENDING_UNQUALIFIED')
            if pending.kind == 'FILL':
                for fill in pending.command.fills:
                    original = RecordedSpotFill(pending.command.prepare_operation_id, pending.command.intent_id,
                                                pending.command.exchange_order_id, fill)
                    if fill.trade_id in existing and existing[fill.trade_id] != original:
                        raise StorageError('PG_FINANCIAL_OBSERVER_PENDING_VALUE_CONFLICT')
                    existing[fill.trade_id] = original
        helper = SpotAccountBillReader(self.reader, anchor_bill_id=context.policy.anchor_bill_id,
                                       max_pages=self.max_pages, max_fill_pages=self.max_pages)
        bills, account_fills = await helper.bills_since_anchor(), await helper.spot_fills_since_anchor()
        for row in bills:
            self._strings(row, ('ccy', 'balChg', 'ts', 'type', 'subType'))
            if row['type'] == '2':
                self._strings(row, ('fee', 'tradeId', 'ordId', 'instId'))
        for row in account_fills.values():
            self._strings(row, ('tradeId', 'ordId', 'instId', 'fillSz', 'fillPx', 'feeCcy', 'fee', 'ts'))
        orders, combined = {}, {}
        for command in context.commands:
            attempt = await self.repository.attempt(command.intent_id, self.binding)
            if attempt is None:
                raise StorageError('PG_FINANCIAL_OBSERVER_ORIGIN_MISSING')
            if attempt.state.value in ('PREPARED', 'ABORTED_BEFORE_SEND'):
                continue
            if attempt.state.value == 'SEND_UNKNOWN':
                raise StorageError('PG_FINANCIAL_OBSERVER_ACK_RECOVERY_REQUIRED')
            response = await self.reader.get_order_by_client_id(command.market, command.client_order_id)
            order = OkxSpotOrderGateway._one_order(response, command.client_order_id)
            self._strings(order, ('sz', 'px', 'accFillSz', 'ordId', 'clOrdId'))
            if (order.get('instType') != 'SPOT' or order.get('instId') != 'LIFE-USDT'
                    or order.get('side') != command.side.lower() or order.get('tdMode') != 'cash'
                    or order.get('ordType') != 'post_only' or Decimal(order['sz']) != command.quantity_base
                    or Decimal(order['px']) != command.limit_price_usdt or attempt.exchange_order_id != order['ordId']
                    or order.get('state') not in ('live', 'partially_filled', 'filled', 'canceled')):
                raise StorageError('PG_FINANCIAL_OBSERVER_ORDER_CONFLICT')
            rows = await self._pages(lambda after=None: self.reader.get_spot_fill_history_page(
                command.market, order['ordId'], after=after), 'billId')
            for row in rows:
                self._strings(row, ('tradeId', 'ordId', 'instId', 'fillSz', 'fillPx', 'feeCcy', 'fee', 'ts'))
            fills = self._fills(command, order, rows, existing)
            orders[command.intent_id] = order
            for fill in fills:
                if fill.trade_id in combined:
                    raise StorageError('PG_FINANCIAL_OBSERVER_FILL_REUSED')
                combined[fill.trade_id] = command, order['ordId'], fill
        if set(account_fills) != set(combined):
            raise StorageError('PG_FINANCIAL_OBSERVER_ACCOUNT_FILL_SCOPE')
        for trade_id, row in account_fills.items():
            command, exchange_id, fill = combined[trade_id]
            if (row['instId'] != 'LIFE-USDT' or row['ordId'] != exchange_id
                    or row.get('clOrdId') not in (None, '', command.client_order_id)
                    or row.get('side', command.side.lower()) != command.side.lower()
                    or (Decimal(row['fillSz']), Decimal(row['fillPx']), row['feeCcy'], Decimal(row['fee']), int(row['ts']))
                    != (fill.quantity_base_life, fill.price_usdt, fill.fee_currency, fill.signed_fee, fill.fill_at_ms)):
                raise StorageError('PG_FINANCIAL_OBSERVER_ACCOUNT_FILL_CONFLICT')
        transfers = self._bills(context, bills, combined)
        pending = await self._pages(self.reader.get_all_open_spot_orders_page, 'ordId')
        expected = {row['clOrdId']: row['ordId'] for row in orders.values() if row['state'] in ('live', 'partially_filled')}
        if (len(pending) != len(expected) or any(row.get('instType') != 'SPOT' or row.get('instId') != 'LIFE-USDT'
                                                 or row.get('state') not in ('live', 'partially_filled') or expected.get(row.get('clOrdId')) != row.get('ordId') for row in pending)):
            raise StorageError('PG_FINANCIAL_OBSERVER_OPEN_ORDER_SCOPE')
        for kind in OkxSpotOrderGateway.SPOT_ALGO_TYPES:
            if (await self.reader.get_all_pending_spot_algo_orders_page(kind))['data'] != []:
                raise StorageError('PG_FINANCIAL_OBSERVER_ALGO_UNOWNED')
        account = (await self.reader.get_spot_cash_balances())['data']
        if len(account) != 1 or type(account[0].get('details')) is not list:
            raise StorageError('PG_FINANCIAL_OBSERVER_WALLET_INVALID')
        balances = {}
        for row in account[0]['details']:
            self._strings(row, ('ccy', 'cashBal'))
            if row.get('liab') is not None and type(row['liab']) is not str:
                raise StorageError('PG_FINANCIAL_OBSERVER_WIRE_TYPES_INVALID')
            asset, value, liability = row['ccy'], Decimal(row['cashBal']), Decimal(row.get('liab') or '0')
            if (asset in balances or not value.is_finite() or value < 0 or not liability.is_finite() or liability != 0
                    or asset not in ('LIFE', 'USDT') and value != 0):
                raise StorageError('PG_FINANCIAL_OBSERVER_WALLET_SCOPE')
            balances[asset] = value
        if not {'LIFE', 'USDT'} <= balances.keys():
            raise StorageError('PG_FINANCIAL_OBSERVER_WALLET_INCOMPLETE')
        return combined, transfers, balances

    def _commands(self, context, timeline, now):
        existing, known_cash = {row.fill.trade_id for row in context.fills}, {row.bill_id for row in context.cashflows}
        cumulative, result = {}, []
        for row in context.fills:
            cumulative.setdefault(row.intent_id, {})[row.fill.trade_id] = row.fill
        for _, kind, event_id, event in timeline:
            if kind == 0:
                if event_id not in known_cash or any(row.kind == 'CASHFLOW' and row.command.bill_id == event_id for row in context.pending):
                    result.append(event)
            else:
                command, exchange_id, fill = event
                members = cumulative.setdefault(command.intent_id, {})
                members[fill.trade_id] = fill
                fills = tuple(sorted(members.values(), key=lambda row: (row.fill_at_ms, row.trade_id)))
                pending_original = next((row.command for row in context.pending if row.kind == 'FILL'
                                         and row.command.intent_id == command.intent_id and row.command.fills == tuple(fills)), None)
                if event_id in existing and pending_original is None:
                    continue
                key = fingerprint({'intent': command.intent_id, 'fills': [value.json() for value in fills]})[:48]
                observed = FillSpot('observed-fill:' + key, command.operation_id, command.intent_id, command.client_order_id,
                                    command.market, command.side, command.session_id, command.epoch, exchange_id,
                                    sum((value.quantity_base_life for value in fills), Decimal('0')), tuple(fills), now)
                for pending in context.pending:
                    if (pending.kind == 'FILL' and pending.command.intent_id == observed.intent_id
                            and pending.command.fills == observed.fills):
                        observed = pending.command
                        break
                result.append(observed)
        return tuple(result)

    async def _cover_original_pending(self, commands, results):
        pending = await self.intake.pending(self.binding, max_rows=self.max_rows, max_bytes=self.max_bytes)
        for received in pending:
            for command, receipt in reversed(tuple(zip(commands, results))):
                original = received.command
                covered = (type(original) is FillSpot and type(command) is FillSpot
                           and original.intent_id == command.intent_id
                           and all(fill in command.fills for fill in original.fills)
                           or type(original) is CashflowSpot and type(command) is CashflowSpot and original.bill_id == command.bill_id)
                if covered:
                    # The fixed SQL marker independently verifies every original
                    # scope/value against the native APPLIED receipt and facts.
                    await self.intake.mark_applied(received, financial_operation_id=receipt.operation_id, owner=received.owner)
                    break
        if await self.intake.pending(self.binding, max_rows=self.max_rows, max_bytes=self.max_bytes):
            raise StorageError('PG_FINANCIAL_OBSERVER_PENDING_RESOLUTION_REQUIRED')

    async def observe(self):
        if self._lock.locked():
            raise StorageError('PG_FINANCIAL_OBSERVER_BACKPRESSURE')
        async with self._lock:
            self.runtime._observation_inflight += 1
            self.runtime._ingress_epoch += 1
            try:
                with localcontext() as precision:
                    precision.prec = 160
                    context = await self.context.observation_context(self.binding)
                    async with self.reader.collection():
                        combined, transfers, balances = await self._collect(context)
                    now = utc(self.reader.source.journal.clock())
                    self._qualify_pending_values(context)
                    timeline = self._financial_timeline(context, combined, transfers, balances, now)
                    if (await self.repository.snapshot(self.binding)).cut != context.cut:
                        raise StorageError('PG_FINANCIAL_OBSERVER_STATE_CHANGED')
                    commands = self._commands(context, timeline, now)
                    # Persist all chosen values before any monetary operation.
                    for command in commands:
                        await self.intake.capture(command, owner=self.owner)
                    results = []
                    for command in commands:
                        receipt = await self.intake.capture_and_apply(command, owner=self.owner,
                                                                      max_rows=self.max_rows, max_bytes=self.max_bytes)
                        results.append(receipt)
                        if receipt.state != 'APPLIED':
                            raise StorageError('PG_FINANCIAL_OBSERVER_RESOLUTION_REQUIRED')
                    await self._cover_original_pending(commands, results)
                    return tuple(results)
            except BaseException as error:
                self.runtime._healthy = False
                if isinstance(error, (StorageError, asyncio.CancelledError)):
                    raise
                raise StorageError('PG_FINANCIAL_OBSERVER_UNTRUSTED') from None
            finally:
                self.runtime._observation_inflight -= 1
