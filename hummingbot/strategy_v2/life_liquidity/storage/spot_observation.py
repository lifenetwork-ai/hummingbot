"""Bounded authenticated OKX spot observations applied through financial ports.

Collection does not mutate native journals. Complete order/fill/bill/account and
runner scope must agree before terminal release; missing history remains a gate.
"""
import asyncio
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from hummingbot.strategy_v2.life_liquidity.account_bills import SpotAccountBillReader
from hummingbot.strategy_v2.life_liquidity.accounting import CapitalLedger
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
from hummingbot.strategy_v2.life_liquidity.order_gateway import OkxSpotOrderGateway
from hummingbot.strategy_v2.life_liquidity.session import OrderReconciliation
from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot, SpotAccountingPolicy
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StateBinding, StorageError, fingerprint, utc
from hummingbot.strategy_v2.life_liquidity.storage.control_plane import ControlSnapshot
from hummingbot.strategy_v2.life_liquidity.storage.financial import (
    AckSpot,
    FillSpot,
    FinancialCut,
    PrepareSpot,
    RecordedSpotFill,
    SpotFillObservation,
    SpotFillPolicy,
    signed_decimal,
)
from hummingbot.strategy_v2.life_liquidity.storage.pending import PendingSpotCommand
from hummingbot.strategy_v2.life_liquidity.storage.recovery import SpotAckRecoveryFacts, SpotFinancialRecoveryFacts
from hummingbot.strategy_v2.life_liquidity.storage.terminal import SpotTerminalFacts, TerminalSpot


@dataclass(frozen=True)
class SpotObservationContext:
    binding: StateBinding
    cut: FinancialCut
    policy: SpotAccountingPolicy
    fill_policy: SpotFillPolicy
    commands: tuple[PrepareSpot, ...]
    fills: tuple[RecordedSpotFill, ...]
    cashflows: tuple[CashflowSpot, ...]
    pending: tuple[PendingSpotCommand, ...] = ()


class SpotObservationRepository(Protocol):
    async def observation_context(self, binding: StateBinding) -> SpotObservationContext:
        ...


class OkxSpotRepositoryObserver:
    def __init__(self, application, *, independent_fill_value, independent_cashflow_value, max_pages):
        if (not callable(independent_fill_value) or not callable(independent_cashflow_value)
                or type(max_pages) is not int or not 0 < max_pages <= 64):
            raise StorageError('SPOT_OBSERVER_CONFIG_INVALID')
        self.application = application
        self.store, self.binding, self.connector = application.store, application.binding, application.connector
        self.fill_value, self.cashflow_value, self.max_pages = independent_fill_value, independent_cashflow_value, max_pages
        self.reason_code = 'SPOT_OBSERVATION_NOT_COLLECTED'
        self._lock = asyncio.Lock()
        self.last_account_facts = None
        self.last_account_cut = None

    async def _pages(self, read_page, cursor_key):
        cursor, previous, result = None, None, []
        for _ in range(self.max_pages):
            response = await read_page(after=cursor)
            if (not isinstance(response, dict) or response.get('code') != '0'
                    or not isinstance(response.get('data'), list) or len(response['data']) > 100):
                raise StorageError('SPOT_OBSERVATION_PAGE_UNTRUSTED')
            rows = response['data']
            for row in rows:
                value = row.get(cursor_key)
                if (type(value) is not str or not value.isascii() or not value.isdecimal()
                        or previous is not None and int(value) >= previous):
                    raise StorageError('SPOT_OBSERVATION_CURSOR_INVALID')
                previous = int(value)
                result.append(row)
            if len(rows) < 100:
                return tuple(result)
            cursor = rows[-1][cursor_key]
        raise StorageError('SPOT_OBSERVATION_HISTORY_INCOMPLETE')

    @staticmethod
    def _price_value(provider, key, at_ms):
        value = provider(key, at_ms)
        if not isinstance(value, IndependentFillObservation):
            raise StorageError('SPOT_OBSERVATION_VALUE_UNQUALIFIED')
        return value

    def _fills(self, command, order, rows, existing):
        result, seen = [], set()
        exchange_id = order['ordId']
        for row in rows:
            trade_id = row['tradeId']
            if (trade_id in seen or row['instType'] != 'SPOT' or row['instId'] != 'LIFE-USDT'
                    or row['ordId'] != exchange_id or row.get('clOrdId') not in (None, '', command.client_order_id)
                    or row.get('side', command.side.lower()) != command.side.lower()):
                raise StorageError('SPOT_OBSERVATION_FILL_SCOPE_INVALID')
            seen.add(trade_id)
            quantity, price = Decimal(row['fillSz']), Decimal(row['fillPx'])
            fee, at = signed_decimal(Decimal(row['fee'])), int(row['ts'])
            known = existing.get(trade_id)
            if known is not None:
                fill = known.fill
                if (known.intent_id != command.intent_id or known.exchange_order_id != exchange_id
                        or (fill.quantity_base_life, fill.price_usdt, fill.fee_currency, fill.signed_fee, fill.fill_at_ms)
                        != (quantity, price, row['feeCcy'], fee, at)):
                    raise StorageError('SPOT_OBSERVATION_EXISTING_FILL_CONFLICT')
            else:
                value = self._price_value(self.fill_value, trade_id, at)
                fill = SpotFillObservation(trade_id, quantity, price, row['feeCcy'], fee, at,
                                           value.price_usdt, value.value_at_ms, value.observed_at_ms, value.source_kind)
            result.append(fill)
        result = tuple(sorted(result, key=lambda fill: (fill.fill_at_ms, fill.trade_id)))
        if sum((fill.quantity_base_life for fill in result), Decimal('0')) != Decimal(order['accFillSz']):
            raise StorageError('SPOT_OBSERVATION_CUMULATIVE_FILL_MISMATCH')
        return result

    def _bills(self, context, rows, fills):
        approved = {row.bill_id: row for row in context.policy.approved_cashflows}
        transfers, trades, fees, changes = {}, set(), {}, {}
        for row in rows:
            currency = row['ccy']
            if currency not in ('LIFE', 'USDT'):
                raise StorageError('SPOT_OBSERVATION_BILL_CURRENCY_UNKNOWN')
            amount = signed_decimal(Decimal(row['balChg']))
            if row['type'] == '1':
                approval = approved.get(row['billId'])
                if (approval is None or row.get('ordId') or row.get('tradeId')
                        or (row['subType'], row['from'], row['to']) not in (('11', '6', '18'), ('12', '18', '6'))
                        or row['subType'] == '11' and amount <= 0 or row['subType'] == '12' and amount >= 0
                        or (approval.currency, approval.signed_amount, approval.transfer_at_ms)
                        != (currency, amount, int(row['ts']))):
                    raise StorageError('SPOT_OBSERVATION_CASHFLOW_UNAPPROVED')
                transfers[row['billId']] = row
            elif row['type'] == '2':
                trade_id = row['tradeId']
                item = fills.get(trade_id)
                if (item is None or row['instId'] != 'LIFE-USDT' or row['ordId'] != item[1]
                        or row.get('clOrdId') not in (None, '', item[0].client_order_id)
                        or row['subType'] not in ('1', '2')):
                    raise StorageError('SPOT_OBSERVATION_TRADE_BILL_UNOWNED')
                trades.add(trade_id)
                fees[trade_id, currency] = fees.get((trade_id, currency), Decimal('0')) + signed_decimal(Decimal(row['fee']))
                changes[trade_id, currency] = changes.get((trade_id, currency), Decimal('0')) + amount
            else:
                raise StorageError('SPOT_OBSERVATION_BILL_TYPE_UNKNOWN')
        if set(transfers) != set(approved) or trades != set(fills):
            raise StorageError('SPOT_OBSERVATION_BILL_SCOPE_INCOMPLETE')
        for trade_id, (command, _, fill) in fills.items():
            sign = Decimal('1') if command.side == 'BUY' else Decimal('-1')
            for currency in ('LIFE', 'USDT'):
                fee = fill.signed_fee if fill.fee_currency == currency else Decimal('0')
                principal = (sign * fill.quantity_base_life if currency == 'LIFE'
                             else -sign * fill.quantity_base_life * fill.price_usdt)
                if fees.get((trade_id, currency), Decimal('0')) != fee or changes.get((trade_id, currency), Decimal('0')) != principal + fee:
                    raise StorageError('SPOT_OBSERVATION_BILL_AMOUNT_OR_FEE_MISMATCH')
        return transfers

    async def _runner_wires(self, combined, orders):
        c = self.application.controller
        # Source collection cannot require a newly reported fill to have been
        # booked already. Financial permission callers retain the default check.
        if await self.store._call(lambda: c._runner_executor_scope_complete(
                quiescent=False, verify_financial_events=False)) is not True:
            raise StorageError('SPOT_OBSERVATION_RUNNER_SCOPE_INCOMPLETE')

        def collect():
            for trade_id, value in dict(c._runner_fill_observations).items():
                source = combined.get(trade_id)
                if source is None:
                    raise StorageError('SPOT_OBSERVATION_RUNNER_FILL_CONFLICT')
                command, exchange_id, fill = source
                if value != (command.intent_id, command.client_order_id, exchange_id, fill.quantity_base_life,
                             fill.price_usdt, fill.fee_currency, fill.signed_fee):
                    raise StorageError('SPOT_OBSERVATION_RUNNER_FILL_CONFLICT')
            by_wire = {value['clOrdId']: value for value in orders.values()}
            for wire, exchange_id in dict(c._runner_cancel_observations).items():
                order = by_wire.get(wire)
                if order is None or exchange_id not in (None, order['ordId']):
                    raise StorageError('SPOT_OBSERVATION_RUNNER_CANCEL_CONFLICT')
            wires = set(c._runner_wire_owners)
            for executor in c._runner_executors():
                wires.update(executor.recovery_order_ids())
            return tuple(sorted(wires))
        return await self.store._call(collect)

    async def _collect(self):
        await self.application._uid()
        context = await self.store.observation_context(self.binding)
        existing = {row.fill.trade_id: row for row in context.fills}
        for row in context.pending:
            if row.kind == 'FILL':
                for fill in row.command.fills:
                    original = RecordedSpotFill(row.command.prepare_operation_id, row.command.intent_id,
                                                row.command.exchange_order_id, fill)
                    if fill.trade_id in existing and existing[fill.trade_id] != original:
                        raise StorageError('SPOT_OBSERVATION_PENDING_FILL_CONFLICT')
                    existing[fill.trade_id] = original
        helper = SpotAccountBillReader(self.connector, anchor_bill_id=context.policy.anchor_bill_id,
                                       max_pages=self.max_pages, max_fill_pages=self.max_pages)
        raw_bills = await helper.bills_since_anchor()
        account_fills = await helper.spot_fills_since_anchor()
        orders, fills, combined = {}, {}, {}
        for command in context.commands:
            attempt = await self.store.attempt(command.intent_id, self.binding)
            if (attempt is None or attempt.state.value in ('PREPARED', 'ABORTED_BEFORE_SEND')
                    or any(row.kind in ('PREPARE', 'ARM') and row.command.intent_id == command.intent_id
                           for row in context.pending)):
                continue
            response = await self.connector.get_order_by_client_id('LIFE-USDT', command.client_order_id)
            order = OkxSpotOrderGateway._one_order(response, command.client_order_id)
            if (order.get('instType') != 'SPOT' or order.get('instId') != 'LIFE-USDT'
                    or order.get('side') != command.side.lower() or order.get('tdMode') != 'cash'
                    or order.get('ordType') != 'post_only' or Decimal(order['sz']) != command.quantity_base
                    or Decimal(order['px']) != command.limit_price_usdt
                    or attempt.exchange_order_id not in (None, order['ordId'])):
                raise StorageError('SPOT_OBSERVATION_ORDER_PAYLOAD_MISMATCH')
            page = await self._pages(lambda after=None: self.connector.get_spot_fill_history_page(
                'LIFE-USDT', order['ordId'], after=after), 'billId')
            observed = self._fills(command, order, page, existing)
            orders[command.intent_id], fills[command.intent_id] = order, observed
            for fill in observed:
                if fill.trade_id in combined:
                    raise StorageError('SPOT_OBSERVATION_TRADE_ID_REUSED')
                combined[fill.trade_id] = command, order['ordId'], fill
        if set(account_fills) != set(combined):
            raise StorageError('SPOT_OBSERVATION_ACCOUNT_FILL_SCOPE_MISMATCH')
        for trade_id, row in account_fills.items():
            command, exchange_id, fill = combined[trade_id]
            if (row['instId'] != 'LIFE-USDT' or row['ordId'] != exchange_id
                    or row.get('clOrdId') not in (None, '', command.client_order_id)
                    or (Decimal(row['fillSz']), Decimal(row['fillPx']), row['feeCcy'], Decimal(row['fee']), int(row['ts']))
                    != (fill.quantity_base_life, fill.price_usdt, fill.fee_currency, fill.signed_fee, fill.fill_at_ms)):
                raise StorageError('SPOT_OBSERVATION_ACCOUNT_FILL_CONFLICT')
        transfers = self._bills(context, raw_bills, combined)
        pending = await self._pages(self.connector.get_all_open_spot_orders_page, 'ordId')
        by_wire = {command.client_order_id: command for command in context.commands}
        for row in pending:
            command = by_wire.get(row.get('clOrdId'))
            if (command is None or row.get('instType') != 'SPOT' or row.get('instId') != 'LIFE-USDT'
                    or row.get('state') not in ('live', 'partially_filled')
                    or row['ordId'] != orders.get(command.intent_id, {}).get('ordId')
                    or orders[command.intent_id]['state'] not in ('live', 'partially_filled')):
                raise StorageError('SPOT_OBSERVATION_PENDING_ORDER_UNOWNED')
        if {row['clOrdId'] for row in pending} != {
                command.client_order_id for command in context.commands
                if orders.get(command.intent_id, {}).get('state') in ('live', 'partially_filled')}:
            raise StorageError('SPOT_OBSERVATION_PENDING_ORDER_SCOPE_INCOMPLETE')
        for kind in OkxSpotOrderGateway.SPOT_ALGO_TYPES:
            page = await self.connector.get_all_pending_spot_algo_orders_page(kind)
            if page.get('code') != '0' or page.get('data') != []:
                raise StorageError('SPOT_OBSERVATION_PENDING_ALGO_UNOWNED')
        account = await self.connector.get_spot_cash_balances()
        if account.get('code') != '0' or len(account.get('data', [])) != 1:
            raise StorageError('SPOT_OBSERVATION_ACCOUNT_UNTRUSTED')
        balances = {}
        for row in account['data'][0]['details']:
            currency = row['ccy']
            if currency in balances:
                raise StorageError('SPOT_OBSERVATION_ACCOUNT_DUPLICATE_CURRENCY')
            value, liability = Decimal(row['cashBal']), Decimal(row.get('liab') or '0')
            if not value.is_finite() or value < 0 or not liability.is_finite() or liability != 0:
                raise StorageError('SPOT_OBSERVATION_ACCOUNT_LIABILITY_OR_VALUE')
            balances[currency] = value
        if not {'LIFE', 'USDT'} <= balances.keys():
            raise StorageError('SPOT_OBSERVATION_ACCOUNT_CURRENCY_MISSING')
        runner_wires = await self._runner_wires(combined, orders)
        await self.application._uid()
        return context, orders, fills, combined, transfers, pending, balances, runner_wires

    async def reconcile(self, session_id, epoch):
        async with self._lock:
            try:
                return await self._reconcile(session_id, epoch)
            except StorageError as exc:
                self.reason_code = exc.code
                raise
            except (KeyError, TypeError, ValueError, ArithmeticError, TimeoutError, OSError):
                self.reason_code = 'SPOT_OBSERVATION_UNTRUSTED'
                raise StorageError(self.reason_code) from None

    def _financial_timeline(self, context, combined, transfers, balances, now):
        cashflows = {row.bill_id: row for row in context.cashflows}
        cashflows.update({row.command.bill_id: row.command for row in context.pending if row.kind == 'CASHFLOW'})
        timeline = []
        for bill_id, raw in sorted(transfers.items(), key=lambda item: (int(item[1]['ts']), int(item[0]))):
            at, currency, amount = int(raw['ts']), raw['ccy'], Decimal(raw['balChg'])
            value = (None if currency == 'USDT' or bill_id in cashflows
                     else self._price_value(self.cashflow_value, bill_id, at))
            command = cashflows.get(bill_id) or CashflowSpot('bill:' + bill_id, bill_id, currency, amount, at,
                                                             Decimal('1') if value is None else value.price_usdt,
                                                             at if value is None else value.value_at_ms,
                                                             at if value is None else value.observed_at_ms,
                                                             'quote_unit' if value is None else value.source_kind, now)
            if (command.reference_observed_at_ms > int(now.timestamp() * 1000)
                    or abs(command.value_at_ms - at) > context.fill_policy.max_reference_skew_ms
                    or abs(command.reference_observed_at_ms - at) > context.fill_policy.max_reference_skew_ms):
                raise StorageError('SPOT_OBSERVATION_CASHFLOW_VALUE_UNQUALIFIED')
            timeline.append((at, 0, bill_id, command))
        for trade_id, (prepared, exchange_id, fill) in combined.items():
            if (fill.fill_at_ms < int(context.policy.opened_at.timestamp() * 1000)
                    or fill.independent_observed_at_ms > int(now.timestamp() * 1000)
                    or fill.fill_at_ms > int(now.timestamp() * 1000)
                    or abs(fill.independent_value_at_ms - fill.fill_at_ms) > context.fill_policy.max_reference_skew_ms
                    or abs(fill.independent_observed_at_ms - fill.fill_at_ms) > context.fill_policy.max_reference_skew_ms):
                raise StorageError('SPOT_OBSERVATION_FILL_VALUE_UNQUALIFIED')
            timeline.append((fill.fill_at_ms, 1, trade_id, (prepared, exchange_id, fill)))
        timeline.sort(key=lambda event: event[:3])
        policy = context.policy
        preview = CapitalLedger(opening_life=policy.opening_base_life, opening_usdt=policy.opening_quote_usdt,
                                opening_independent_price_usdt=policy.opening_independent_price_usdt)
        for _, kind, event_id, event in timeline:
            if kind == 0:
                preview.record_asset_cashflow(event_id, event.currency, event.signed_amount,
                                              independent_value_usdt=event.independent_price_usdt)
            else:
                command, _, fill = event
                fee = -fill.signed_fee * (fill.independent_price_usdt if fill.fee_currency == 'LIFE' else Decimal('1'))
                preview.record_fill(event_id, command.side, fill.quantity_base_life, fill.price_usdt, fee,
                                    independent_value_usdt=fill.independent_price_usdt,
                                    fee_currency=fill.fee_currency, signed_fee=fill.signed_fee)
        if (preview.life_balance, preview.usdt_balance) != (balances['LIFE'], balances['USDT']):
            raise StorageError('SPOT_OBSERVATION_ACCOUNT_PROJECTION_MISMATCH')
        return timeline

    async def recover_pending(self):
        """Complete exact journal commands under an explicit observation grant.

        Newer external activity that the stored command does not cover remains
        a recovery gate; original prices/fees/identities are never replaced.
        """
        async with self._lock:
            context, orders, fills, combined, transfers, pending, balances, runner_wires = await self._collect()
            now = utc(self.store._clock())
            self._financial_timeline(context, combined, transfers, balances, now)
            if (await self.store.snapshot(self.binding)).cut != context.cut:
                raise StorageError('SPOT_OBSERVATION_STATE_CHANGED_DURING_COLLECTION')
            if not context.pending:
                return ()
            if len(context.pending) != 1:
                raise StorageError('SPOT_RECOVERY_BATCH_REQUIRED')
            step = context.pending[0]
            if step.owner != self.application.owner:
                raise StorageError('SPOT_RECOVERY_ORIGINAL_OWNER_MISMATCH')
            facts = SpotFinancialRecoveryFacts(self.binding, now, balances['LIFE'], balances['USDT'],
                                               Decimal('0'), Decimal('0'), tuple(combined), tuple(transfers),
                                               tuple(row['clOrdId'] for row in orders.values()), runner_wires,
                                               True, True, True)
            command = step.command
            args = dict(facts=facts, cut=context.cut, owner=step.owner)
            if step.kind == 'PREPARE':
                receipt = await self.store.resume_prepare(command, **args)
            elif step.kind == 'ARM':
                receipt = await self.store.resume_arm(command.operation_id, **args)
            elif step.kind == 'FILL':
                receipt = await self.store.resume_fill(command, **args)
            elif step.kind == 'CASHFLOW':
                receipt = await self.store.resume_cashflow(command, **args)
            elif step.kind == 'ACK':
                order = orders[command.intent_id]
                args['facts'] = SpotAckRecoveryFacts(facts, command.client_order_id, order['ordId'], command.market,
                                                     command.side, Decimal(order['sz']), Decimal(order['px']),
                                                     Decimal(order['accFillSz']), order['state'].upper())
                receipt = await self.store.resume_ack(command, **args)
            else:
                receipt = await self.store.apply_lifecycle(command, cut=context.cut, owner=step.owner)
            return (receipt,)

    async def control_snapshot(self):
        """API-qualified facts for an external signer, without accounting writes."""
        async with self._lock:
            context, orders, fills, combined, transfers, pending, balances, runner_wires = await self._collect()
            now = utc(self.store._clock())
            self._financial_timeline(context, combined, transfers, balances, now)
            if (await self.store.snapshot(self.binding)).cut != context.cut:
                raise StorageError('SPOT_OBSERVATION_STATE_CHANGED_DURING_COLLECTION')
            quiescent = await self.store._call(lambda: self.application.controller._runner_executor_scope_complete(
                quiescent=True, verify_financial_events=False))
            active = set()
            for command in context.commands:
                attempt = await self.store.attempt(command.intent_id, self.binding)
                if attempt is not None and attempt.state.value not in ('TERMINAL', 'ABORTED_BEFORE_SEND'):
                    active.add(command.client_order_id)
            facts = SpotFinancialRecoveryFacts(self.binding, now, balances['LIFE'], balances['USDT'],
                                               Decimal('0'), Decimal('0'), tuple(combined), tuple(transfers),
                                               tuple(row['clOrdId'] for row in pending),
                                               tuple(sorted(active & set(runner_wires))), True, True, quiescent is True)
            return ControlSnapshot(self.binding, context.cut, facts)

    async def _reconcile(self, session_id, epoch):
        self.last_account_facts = self.last_account_cut = None
        context, orders, fills, combined, transfers, pending, balances, runner_wires = await self._collect()
        existing = {row.fill.trade_id for row in context.fills}
        cashflows = {row.bill_id: row for row in context.cashflows}
        now = utc(self.store._clock())
        timeline = self._financial_timeline(context, combined, transfers, balances, now)
        if (await self.store.snapshot(self.binding)).cut != context.cut:
            raise StorageError('SPOT_OBSERVATION_STATE_CHANGED_DURING_COLLECTION')
        runner_wires = await self._runner_wires(combined, orders)
        # ACK every known accepted attempt first. This publishes no money and
        # allows the complete financial timeline to be applied in causal order.
        for command in context.commands:
            order = orders.get(command.intent_id)
            if order is None:
                continue
            attempt = await self.store.attempt(command.intent_id, self.binding)
            if attempt.state.value == 'TERMINAL':
                continue
            if attempt.state.value == 'SEND_UNKNOWN':
                ack = AckSpot('observed-ack:' + hashlib.sha256(command.operation_id.encode()).hexdigest()[:48],
                              command.operation_id, command.intent_id, command.client_order_id, 'LIFE-USDT',
                              command.side, command.session_id, command.epoch, order['ordId'], now)
                await self.store.acknowledge(ack, cut=(await self.store.snapshot(self.binding)).cut, owner=self.application.owner)
        cumulative = {command.intent_id: [] for command in context.commands}
        for _, kind, event_id, event in timeline:
            if kind == 0:
                if event_id not in cashflows:
                    await self.store.apply_cashflow(event, cut=(await self.store.snapshot(self.binding)).cut,
                                                    owner=self.application.owner)
            else:
                command, exchange_id, fill = event
                cumulative[command.intent_id].append(fill)
                if event_id in existing:
                    continue
                observed = tuple(cumulative[command.intent_id])
                key = fingerprint({'intent': command.intent_id, 'fills': [value.json() for value in observed]})[:48]
                observation = FillSpot('observed-fill:' + key, command.operation_id, command.intent_id,
                                       command.client_order_id, 'LIFE-USDT', command.side, command.session_id,
                                       command.epoch, exchange_id, sum((value.quantity_base_life for value in observed), Decimal('0')),
                                       observed, now)
                await self.store.apply_fills(observation, cut=(await self.store.snapshot(self.binding)).cut,
                                             owner=self.application.owner)
        runner_wires = await self._runner_wires(combined, orders)
        if await self.store._call(self.application.controller.verify_runner_fill_events) is not True:
            raise StorageError('SPOT_OBSERVATION_RUNNER_FINANCIAL_PROJECTION_INCOMPLETE')
        for command in context.commands:
            order = orders.get(command.intent_id)
            if order is None:
                continue
            attempt = await self.store.attempt(command.intent_id, self.binding)
            if attempt.state.value == 'TERMINAL':
                continue
            observed = fills[command.intent_id]
            if order['state'] in ('filled', 'canceled'):
                facts = SpotTerminalFacts(self.binding, now, 'FILLED' if order['state'] == 'filled' else 'CANCELED',
                                          Decimal(order['accFillSz']), command.quantity_base, command.limit_price_usdt,
                                          observed, balances['LIFE'], balances['USDT'], Decimal('0'), Decimal('0'),
                                          tuple(combined), tuple(transfers), tuple(row['clOrdId'] for row in pending),
                                          tuple(row['ordId'] for row in pending), runner_wires, True, True, True)
                terminal = TerminalSpot('observed-terminal:' + hashlib.sha256(command.operation_id.encode()).hexdigest()[:48],
                                        command.operation_id, command.intent_id, command.client_order_id, 'LIFE-USDT',
                                        command.side, command.session_id, command.epoch, order['ordId'], facts)
                previous = await self.store.lookup_terminal(terminal.operation_id, self.binding)
                operation = self.store.resume_terminal if previous is not None else self.store.confirm_terminal
                await operation(terminal, cut=(await self.store.snapshot(self.binding)).cut, owner=self.application.owner)
        self.reason_code = 'SPOT_REPOSITORY_RECONCILED'
        active_wires = set()
        for command in context.commands:
            attempt = await self.store.attempt(command.intent_id, self.binding)
            if attempt.state.value not in ('TERMINAL', 'ABORTED_BEFORE_SEND'):
                active_wires.add(command.client_order_id)
        quiescent = await self.store._call(self.application.controller._runner_executor_scope_complete)
        self.last_account_facts = SpotFinancialRecoveryFacts(
            self.binding, now, balances['LIFE'], balances['USDT'], Decimal('0'), Decimal('0'),
            tuple(combined), tuple(transfers), tuple(row['clOrdId'] for row in pending),
            tuple(sorted(active_wires & set(runner_wires))), True, True, quiescent is True)
        self.last_account_cut = (await self.store.snapshot(self.binding)).cut
        return OrderReconciliation(session_id=session_id, epoch=epoch, observed_at=now,
                                   open_order_ids=tuple(row['clOrdId'] for row in pending), unknown_order_ids=(),
                                   pending_cancel_ids=(), trade_events_reconciled=True, scope_complete=True)
