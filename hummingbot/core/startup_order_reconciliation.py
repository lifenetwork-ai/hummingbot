"""Narrow GET-only proof for canceled orders omitted by native cold restore."""
import asyncio
import json
import logging
import time
from decimal import Decimal

from sqlalchemy import select

from hummingbot.connector.okx_order_status import order_status_data
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.trade_fill import TradeFill

MAX_CANCEL_VISIBILITY_ORDERS = 10
CANCEL_VISIBILITY_READS = 5
CANCEL_VISIBILITY_INTERVAL = 1.0


class UntrackedOpenOrders(SubmissionBlocked):
    def __init__(self, evidence):
        self.evidence = evidence
        super().__init__('The exchange account has untracked open orders.')


def _canceled_without_local_fills(owner, manager, items):
    """A database history row is evidence, never permission to ignore an order."""
    ids = [item.get('clOrdId') for item in items]
    if not ids or len(ids) > MAX_CANCEL_VISIBILITY_ORDERS or any(not isinstance(x, str) or not x for x in ids):
        return None
    result = {}
    with manager.get_new_session() as session:
        for row in session.execute(select(Order).where(Order.id.in_(ids))).scalars():
            if (row.config_file_path != owner.config_file_path or row.market not in owner.connectors
                    or row.market not in ('okx', 'okx_perpetual') or row.creation_pending
                    or row.last_status != 'OrderCancelled' or not row.exchange_order_id
                    or row.order_type not in ('LIMIT', 'LIMIT_MAKER')):
                return None
            if any(not value.is_finite() or value <= 0 for value in (Decimal(row.amount), Decimal(row.price))):
                return None
            if session.execute(select(TradeFill.order_id).where(TradeFill.order_id == row.id).limit(1)).first():
                return None
            statuses = set(session.execute(select(OrderStatus.status).where(
                OrderStatus.order_id == row.id, OrderStatus.status.in_(
                    ('PendingBuy', 'PendingSell', 'BuyOrderCreated', 'SellOrderCreated')))).scalars())
            sides = {side for side, names in (('buy', {'PendingBuy', 'BuyOrderCreated'}),
                                              ('sell', {'PendingSell', 'SellOrderCreated'})) if statuses & names}
            if len(sides) != 1:
                return None
            result[row.id] = dict(id=row.id, exchange_id=row.exchange_order_id, market=row.market,
                                  symbol=row.symbol, instrument=row.symbol + ('-SWAP' if row.market == 'okx_perpetual' else ''),
                                  side=sides.pop(), amount=Decimal(row.amount), price=Decimal(row.price), order_type=row.order_type)
    if set(result) != set(ids):
        return None
    if any(item.get('ordId') != result[item['clOrdId']]['exchange_id']
           or item.get('instId') != result[item['clOrdId']]['instrument'] for item in items):
        return None
    return result


async def _venue_canceled_without_fills(owner, connector, proof):
    # Query using the SAME authenticated client that listed the pending orders.
    # Its account-wide list can include the other instrument type.
    if connector.name == 'okx':
        status_limit = '/api/v5/trade/order'
    else:
        from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_web_utils import (
            get_rest_api_limit_id_for_endpoint,
        )
        status_limit = get_rest_api_limit_id_for_endpoint('GET', '/api/v5/trade/order')
    for local in proof.values():
        owner.check_owner()
        response = await connector._api_get(path_url='/api/v5/trade/order', params={
            'instId': local['instrument'], 'clOrdId': local['id']}, is_auth_required=True, limit_id=status_limit)
        actual = order_status_data(response)
        size = Decimal(str(actual.get('sz')))
        if local['market'] == 'okx_perpetual':
            size = owner.connectors[local['market']]._format_size_to_amount(local['symbol'], size)
        if (actual.get('clOrdId') != local['id'] or actual.get('ordId') != local['exchange_id']
                or actual.get('instId') != local['instrument'] or actual.get('side') != local['side']
                or actual.get('state') != 'canceled' or Decimal(str(actual.get('accFillSz'))) != 0
                or size != local['amount'] or Decimal(str(actual.get('px'))) != local['price']
                or actual.get('ordType') != {'LIMIT': 'limit', 'LIMIT_MAKER': 'post_only'}.get(local['order_type'])):
            return False
        # Empty cumulative size alone is not sufficient if fill and status views
        # disagree. Any fill, malformed response or error requires recovery.
        response = await connector._api_get(path_url='/api/v5/trade/fills', params={
            'instType': 'SWAP' if local['market'] == 'okx_perpetual' else 'SPOT',
            'instId': local['instrument'], 'ordId': local['exchange_id'], 'limit': '100'}, is_auth_required=True)
        if not isinstance(response, dict) or str(response.get('code')) != '0' or response.get('data') != []:
            return False
    return True


async def reconcile_open_order_visibility(owner, manager, connector, open_orders, known):
    """Never restore/cancel/resubmit an untracked identity or clear a prior HALT.

    The enclosing startup still enforces its original 30-second deadline. Only
    locally acknowledged zero-fill cancellations get a bounded visibility wait;
    both their status and fill views must agree before AND after they disappear.
    """
    unknown = [item for item in open_orders if item.get('clOrdId') not in known]
    if not unknown:
        return
    original = unknown

    def observation(code):
        # Bounded identifiers only: no raw response, key, account payload or
        # arbitrary exception message. JSON encoding keeps log lines intact.
        return {
            'observed_at': time.time(), 'connector': connector.name, 'config_file_path': owner.config_file_path,
            'control_version': owner.version, 'owner_generation': owner.generation, 'reason_code': code,
            'untracked_count': len(unknown), 'orders': [{
                key: str(item.get(source, ''))[:128]
                for key, source in (('client_order_id', 'clOrdId'), ('exchange_order_id', 'ordId'),
                                    ('instrument', 'instId'))} for item in unknown[:MAX_CANCEL_VISIBILITY_ORDERS]],
        }

    def reject(code):
        evidence = observation(code)
        logging.getLogger(__name__).error('Startup untracked-order evidence=%s', json.dumps(evidence, sort_keys=True))
        raise UntrackedOpenOrders(evidence)

    logging.getLogger(__name__).warning('Startup untracked-order observation=%s',
                                        json.dumps(observation('untracked_order_observed'), sort_keys=True))
    proof = _canceled_without_local_fills(owner, manager, unknown)
    if proof is None:
        reject('untracked_order_identity_unproven')
    try:
        if not await _venue_canceled_without_fills(owner, connector, proof):
            reject('untracked_order_terminal_proof_mismatch')
        for attempt in range(CANCEL_VISIBILITY_READS):
            owner.check_owner()
            await asyncio.sleep(CANCEL_VISIBILITY_INTERVAL)
            open_orders = await owner._open_orders(connector)
            unknown = [item for item in open_orders if item.get('clOrdId') not in known]
            if not unknown:
                if (_canceled_without_local_fills(owner, manager, original) != proof
                        or not await _venue_canceled_without_fills(owner, connector, proof)):
                    unknown = original
                    reject('untracked_order_terminal_proof_changed')
                logging.getLogger(__name__).warning('Startup canceled-order visibility resolved; verified=%s reads=%s',
                                                    len(proof), attempt + 1)
                return
            # A new or conflicting identity never inherits the earlier proof.
            if _canceled_without_local_fills(owner, manager, unknown) != {item['clOrdId']: proof.get(item['clOrdId']) for item in unknown}:
                reject('untracked_order_identity_changed')
        reject('untracked_order_visibility_unconfirmed')
    except UntrackedOpenOrders:
        raise
    except Exception:
        unknown = unknown or original
        reject('untracked_order_read_unavailable')
