"""Apply a verified Spot fill tail to a reviewed position checkpoint."""
import json
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import exists, func, select, update

from hummingbot.core.automatic_recovery import eligible
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState, TradeUpdate
from hummingbot.core.data_type.trade_fee import TradeFeeBase
from hummingbot.core.recovery import FINANCIAL_FIELDS, RecoveryBlocked, decimal
from hummingbot.model.checkpoint_recovery import fill_identity
from hummingbot.model.executors import Executors
from hummingbot.model.order import Order
from hummingbot.model.runtime_journal import (
    RuntimeJournal,
    SubmissionAttempt,
    advance_checkpoints,
    digest,
    held_order_snapshot,
)
from hummingbot.model.trade_fill import TradeFill
from hummingbot.strategy_v2.executors.executor_orchestrator import PositionHold
from hummingbot.strategy_v2.executors.position_executor.data_types import PositionExecutorConfig
from hummingbot.strategy_v2.models.base import RunnableStatus
from hummingbot.strategy_v2.models.executors import CloseType
from hummingbot.strategy_v2.models.executors_info import ExecutorInfo

MAX_PENDING_FILLS = 64
POSITION_FIELDS = (*FINANCIAL_FIELDS, 'side', 'unrealized_pnl_quote')


def candidates(connection, state, positions, bindings, checkpoint, anchor):
    """Return native hold transfers and their preview, or None for unsupported work."""
    rows = connection.execute(select(RuntimeJournal).where(RuntimeJournal.applied.is_(False))
                              .order_by(RuntimeJournal.id).limit(MAX_PENDING_FILLS + 1)).mappings().all()
    if not rows:
        return [], []
    watermark = max(anchor['journal_watermark'], checkpoint.watermark)
    if len(rows) > MAX_PENDING_FILLS or any(
            row['kind'] != 'fill' or not row['order_id'] or row['id'] <= watermark for row in rows):
        return None
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['order_id']].append(row)
    active = sorted((r['connector'], r['environment'], r['account_id'], r['version'])
                    for r in bindings if r['state'] == 'active')
    transfers, scopes = [], defaultdict(list)
    try:
        for identity, pending in grouped.items():
            item = _transfer(connection, state, active, checkpoint.payload, identity, pending)
            transfers.append(item)
            scopes[item['pair']].append(item)
        plan = []
        for pair, items in scopes.items():
            matching = [p for p in positions if p['connector_name'] == 'okx' and p['trading_pair'] == pair]
            if len(matching) != 1 or any(item['executor']['controller_id'] != matching[0]['controller_id'] for item in items):
                return None
            earliest = min(fill['journal_id'] for item in items for fill in item['fills'])
            # A later same-market transfer would make the stored checkpoint
            # a non-prefix. Application IDs also cover late-recorded holds.
            if connection.scalar(select(exists().where(
                    RuntimeJournal.id > earliest, RuntimeJournal.applied.is_(True),
                    Order.market == 'okx', Order.symbol == pair, RuntimeJournal.order_id == Order.id,
                    RuntimeJournal.kind.in_(('fill', 'applied_order'))))):
                return None
            plan.append(_position_change(matching[0], items))
        return transfers, plan
    except (ValueError, TypeError, ArithmeticError, KeyError, AttributeError, RecoveryBlocked):
        return None


def _transfer(connection, state, active, checkpoint, identity, pending):
    blocked = RecoveryBlocked('recovery_pending_fills_unproven')
    order = connection.execute(select(Order).where(Order.id == identity)).mappings().one_or_none()
    attempt = connection.execute(select(SubmissionAttempt).where(SubmissionAttempt.order_id == identity)).mappings().one_or_none()
    if (not order or not attempt or attempt['state'] != 'terminal' or order['creation_pending']
            or order['position_applied'] or order['cleanup_operation_id'] is not None
            or order['market'] != 'okx' or order['config_file_path'] != state['config_file_path']
            or order['order_type'] != 'LIMIT_MAKER' or order['position'] != 'NIL'
            or not order['exchange_order_id'] or order['quote_asset'] != 'USDT'
            or order['symbol'] != order['base_asset'] + '-USDT'):
        raise blocked
    context = attempt['context']
    original, receipt = context['order'], attempt['receipt'] or {}
    side = original.get('trade_type')
    credential = (checkpoint.get('credential_fingerprints') or {}).get('okx')
    if (not eligible(context) or not credential or context['credential_fingerprint'] != credential
            or context['bot_id'] != state['bot_id'] or context['environment'] != checkpoint['environment']
            or context['config_file_path'] != state['config_file_path']
            or sorted((r['connector'], r['environment'], r['account_id'], r['version']) for r in context['bindings']) != active
            or not any(r[0] == 'okx' for r in active)
            or original['client_order_id'] != identity or original['trading_pair'] != order['symbol']
            or original['position'] != 'NIL' or order['last_status'] != side.title() + 'OrderCompleted'
            or original['exchange_order_id'] is not None or original['order_fills']
            or decimal(original['executed_amount_base']) != 0 or decimal(original['executed_amount_quote']) != 0
            or decimal(original['amount']) != order['amount'] or decimal(original['price']) != order['price']
            or receipt.get('code') != '0' or receipt.get('http_status') != 200
            or (receipt.get('order') or {}).get('sCode') != '0'
            or receipt['order'].get('clOrdId') != identity or receipt['order'].get('ordId') != order['exchange_order_id']):
        raise blocked
    raw_config = context['executor']
    config = PositionExecutorConfig.model_validate(raw_config)
    if (not raw_config.get('id') or not config.mm_session_id or type(config.mm_generation) is not int
            or config.connector_name != 'okx' or config.trading_pair != order['symbol'] or not config.controller_id
            or config.side.name != side
            or connection.scalar(select(Executors.id).where(Executors.id == config.id)) is not None
            or connection.scalar(select(func.count()).select_from(SubmissionAttempt).where(
                SubmissionAttempt.context['executor']['id'].as_string() == config.id)) != 1
            or connection.scalar(select(exists().where(RuntimeJournal.event_key.in_(
                ('hold:' + config.id, 'applied-order:' + identity)))))):
        raise blocked
    fills = connection.execute(select(TradeFill).where(TradeFill.order_id == identity)
                               .limit(MAX_PENDING_FILLS + 1)).mappings().all()
    journal_ids = connection.execute(select(RuntimeJournal.id).join(TradeFill, fill_identity()).where(
        TradeFill.order_id == identity).limit(MAX_PENDING_FILLS + 1)).scalars().all()
    if len(fills) != len(pending) or sorted(journal_ids) != [row['id'] for row in pending]:
        raise blocked
    by_id = {fill['exchange_trade_id']: fill for fill in fills}
    tracked = InFlightOrder.from_json({**original, 'exchange_order_id': order['exchange_order_id']})
    refs, fees = [], Decimal(0)
    for row in pending:
        payload = row['payload']
        fill = by_id.pop(payload['exchange_trade_id'])
        if (payload.get('late_application') is not False or row['event_key'] != 'fill:' + digest(['okx', identity, fill['exchange_trade_id']])
                or any(fill[k] != order[k] for k in ('market', 'symbol', 'config_file_path', 'position', 'order_type'))
                or fill['trade_type'] != side or fill['amount'] <= 0 or fill['price'] <= 0):
            raise blocked
        fees += decimal(fill['trade_fee_in_quote'])
        tracked.update_with_trade_update(TradeUpdate(
            trade_id=fill['exchange_trade_id'], client_order_id=identity, exchange_order_id=order['exchange_order_id'],
            trading_pair=order['symbol'], fill_timestamp=fill['timestamp'] / 1000, fill_price=fill['price'],
            fill_base_amount=fill['amount'], fill_quote_amount=fill['amount'] * fill['price'],
            fee=TradeFeeBase.from_json(fill['trade_fee']), is_taker=False))
        refs.append(dict(journal_id=row['id'], trade_id=fill['exchange_trade_id']))
    if tracked.executed_amount_base != tracked.amount:
        raise blocked
    tracked.current_state = OrderState.FILLED
    held = held_order_snapshot(tracked)
    if decimal(held['cumulative_fee_paid_quote']) != fees:
        raise blocked
    info = ExecutorInfo(id=config.id, timestamp=config.timestamp, type=config.type, status=RunnableStatus.TERMINATED,
                        close_type=CloseType.POSITION_HOLD, close_timestamp=order['last_update_timestamp'] // 1000,
                        config=config, controller_id=config.controller_id, net_pnl_pct=Decimal(0), net_pnl_quote=Decimal(0),
                        cum_fees_quote=fees, filled_amount_quote=tracked.executed_amount_quote, is_active=False,
                        is_trading=False, custom_info={'held_position_orders': [held], 'recovered': True})
    return dict(id=identity, exchange_order_id=order['exchange_order_id'], pair=order['symbol'],
                credential_fingerprint=credential, fills=refs, executor=json.loads(info.model_dump_json()))


def _position_change(stored, items):
    hold = PositionHold('okx', stored['trading_pair'], TradeType[stored['side']])
    amount = decimal(stored['amount'])
    hold.net_amount_base = amount if stored['side'] == 'BUY' else -amount
    hold.avg_entry_price = decimal(stored['breakeven_price'])
    hold.volume_traded_quote = decimal(stored['volume_traded_quote'])
    hold.realized_pnl_quote = decimal(stored['realized_pnl_quote'])
    hold.cum_fees_quote = decimal(stored['cum_fees_quote'])
    infos = [ExecutorInfo.model_validate(item['executor']) for item in items]
    sides = {info.config.side for info in infos}
    if (amount < 0 or len(sides) != 1
            or (next(iter(sides)) != hold.side and amount and sum(
                decimal(info.custom_info['held_position_orders'][0]['executed_amount_base']) for info in infos) > amount)):
        raise RecoveryBlocked('recovery_pending_fills_unproven')
    for info in infos:
        hold.add_orders_from_executor(info)
    view = hold.get_position_summary(hold.avg_entry_price)
    after = {field: str(getattr(view, field)) for field in FINANCIAL_FIELDS}
    after.update(side=view.side.name, unrealized_pnl_quote='0')
    return dict(id=stored['id'], controller_id=stored['controller_id'], connector='okx', pair=stored['trading_pair'],
                before={field: str(stored[field]) for field in POSITION_FIELDS}, after=after,
                valuation_basis='reconstructed_average_entry_not_live_pnl')


def apply(session, checkpoint, snapshot, operation_id):
    """Write the native deduplication receipts alongside the reviewed position update."""
    items = snapshot.get('pending_fill_transfers', [])
    if not items:
        return
    for item in items:
        order_id, info = item['id'], item['executor']
        if session.execute(update(Order).where(Order.id == order_id, Order.position_applied.is_(False))
                           .values(position_applied=True)).rowcount != 1:
            raise RecoveryBlocked('recovery_state_changed')
        ids = [fill['journal_id'] for fill in item['fills']]
        if session.execute(update(RuntimeJournal).where(RuntimeJournal.id.in_(ids), RuntimeJournal.applied.is_(False))
                           .values(applied=True)).rowcount != len(ids):
            raise RecoveryBlocked('recovery_state_changed')
        session.add(Executors(**info))
        session.add(RuntimeJournal(event_key='hold:' + info['id'], kind='hold', applied=True,
                                   payload=dict(executor=info, recovery_operation_id=operation_id)))
        session.add(RuntimeJournal(event_key='applied-order:' + order_id, kind='applied_order', order_id=order_id, applied=True,
                                   payload=dict(executor_id=info['id'], order=info['custom_info']['held_position_orders'][0],
                                                recovery_operation_id=operation_id)))
    from hummingbot.strategy_v2.utils.perpetual_reduction import basis_checkpoint, restore_basis_checkpoint
    previous_hash = checkpoint.financial_hash
    advance_checkpoints(session, [checkpoint])
    if 'reduction_basis' in checkpoint.payload:
        try:
            costs = restore_basis_checkpoint(checkpoint.payload['reduction_basis'], previous_hash)
        except (ValueError, TypeError, ArithmeticError):
            return  # The old sidecar cannot acquire authority from a Spot repair.
        checkpoint.payload = {**checkpoint.payload, 'reduction_basis': basis_checkpoint(checkpoint.financial_hash, costs)}
