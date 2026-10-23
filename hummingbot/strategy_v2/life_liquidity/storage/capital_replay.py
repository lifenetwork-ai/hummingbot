"""Causal capital replay shared by File storage and cold migration projection.

The caller supplies the original policy, complete histories and its cashflow
approval validator. This pure function never reads paths or creates authority.
"""
from datetime import datetime
from decimal import Decimal

from hummingbot.strategy_v2.life_liquidity.accounting import CapitalLedger
from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.financial import PrepareSpot, SpotFillObservation
from hummingbot.strategy_v2.life_liquidity.storage.joint_settlement import JointCashEvent


def cashflow_from_json(data):
    values = dict(data)
    values['signed_amount'] = Decimal(values['signed_amount'])
    values['independent_price_usdt'] = Decimal(values['independent_price_usdt'])
    values['observed_at'] = datetime.fromisoformat(values['observed_at'])
    return CashflowSpot(**values)


def replay_capital(policy, commands, fills, cashflows, joint_cashflows, *, validate_cashflow):
    capital = CapitalLedger(opening_life=policy.opening_base_life, opening_usdt=policy.opening_quote_usdt,
                            opening_independent_price_usdt=policy.opening_independent_price_usdt)
    timeline = []
    for trade_id, data in fills.items():
        row = SpotFillObservation.from_json(data['fill'])
        if row.fill_at_ms < int(policy.opened_at.timestamp() * 1000):
            raise StorageError('SPOT_CAPITAL_PREOPEN_FILL')
        side = PrepareSpot.from_json(commands[data['prepare_operation_id']]['command']).side
        timeline.append((row.fill_at_ms, 1, trade_id, (side, row)))
    for bill_id, data in cashflows.items():
        row = cashflow_from_json(data)
        if bill_id != row.bill_id:
            raise StorageError('SPOT_CAPITAL_CASHFLOW_IDENTITY_INVALID')
        validate_cashflow(row)
        timeline.append((row.transfer_at_ms, 0, bill_id, row))
    for event_id, data in joint_cashflows.items():
        event = JointCashEvent.from_json(data['event'])
        if event.event_id != event_id or event.at_ms < int(policy.opened_at.timestamp() * 1000):
            raise StorageError('JOINT_CASH_EVENT_INVALID')
        # Native joint wallet ordering is spot fill before settled cash at
        # equal time. A later settlement cannot finance an earlier fill.
        timeline.append((event.at_ms, 2, event_id, event))
    try:
        # Preserve existing attributor ordering: cashflow before fill at an
        # equal timestamp; lexical ID within kind. No invented clock order.
        for _, kind, event_id, event in sorted(timeline, key=lambda item: item[:3]):
            if kind == 0:
                capital.record_asset_cashflow(event_id, event.currency, event.signed_amount,
                                              independent_value_usdt=event.independent_price_usdt)
            elif kind == 1:
                side, row = event
                fee_cost = -row.signed_fee * (row.independent_price_usdt if row.fee_currency == 'LIFE' else Decimal('1'))
                capital.record_fill(event_id, side, row.quantity_base_life, row.price_usdt, fee_cost,
                                    independent_value_usdt=row.independent_price_usdt,
                                    fee_currency=row.fee_currency, signed_fee=row.signed_fee)
            else:
                if capital.usdt_balance + event.signed_amount_usdt < 0:
                    raise ValueError('settled cash exceeds wallet balance')
                capital.record_funding(event_id, -event.signed_amount_usdt)
                capital.execution_loss_quote += event.execution_loss_usdt
        return capital
    except (ValueError, ArithmeticError):
        raise StorageError('SPOT_CAPITAL_CAUSAL_REPLAY_INVALID') from None
