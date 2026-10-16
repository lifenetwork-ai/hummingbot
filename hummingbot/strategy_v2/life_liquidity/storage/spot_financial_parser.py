"""Pure exact SPOT bill/fill valuation and causal account projection checks."""
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.accounting import CapitalLedger
from hummingbot.strategy_v2.life_liquidity.fill_attribution import IndependentFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.financial import SpotFillObservation, signed_decimal


class SpotFinancialParser:
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

    def _financial_timeline(self, context, combined, transfers, balances, now):
        cashflows = {row.bill_id: row for row in context.cashflows}
        cashflows.update({row.command.bill_id: row.command for row in context.pending if row.kind == 'CASHFLOW'})
        for batch in context.recovery_batches:
            for command in batch.cashflows:
                if command.bill_id in cashflows and cashflows[command.bill_id] != command:
                    raise StorageError('SPOT_OBSERVATION_PENDING_CASHFLOW_CONFLICT')
                cashflows[command.bill_id] = command
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
