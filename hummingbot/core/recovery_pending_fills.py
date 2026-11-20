"""Positive terminal order proof for a committed but unapplied fill tail."""
import asyncio

from hummingbot.core.automatic_recovery import decode_order_evidence
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.order_submission_guard import SubmissionBlocked
from hummingbot.core.recovery import RecoveryBlocked
from hummingbot.model.runtime_journal import credential_fingerprint


def fill_ids(snapshot):
    return sorted(fill['journal_id'] for item in snapshot.get('pending_fill_transfers', []) for fill in item['fills'])


async def verify_pending_fills(snapshot, readers):
    items = snapshot.get('pending_fill_transfers', [])
    if not items:
        return []
    reader = readers['okx']
    credential = credential_fingerprint(reader.connector)
    for item in items:
        if credential != item['credential_fingerprint']:
            raise RecoveryBlocked('exchange_account_mismatch')
        order = InFlightOrder.from_json(item['executor']['custom_info']['held_position_orders'][0])
        response = await asyncio.wait_for(reader._api_get(
            path_url='/api/v5/trade/order', params=dict(instId=item['pair'], clOrdId=item['id']),
            is_auth_required=True, return_err=True, limit_id='/api/v5/account/config'), 10)
        if isinstance(response, dict) and response.get('code') not in ('0', '51603'):
            raise RecoveryBlocked('recovery_exchange_unavailable')
        rows = response.get('data') if isinstance(response, dict) and response.get('code') == '0' else None
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise RecoveryBlocked('recovery_pending_fills_unproven')
        try:
            state, filled = decode_order_evidence(reader.connector, order, rows[0])
        except (SubmissionBlocked, ValueError, TypeError, KeyError, ArithmeticError):
            raise RecoveryBlocked('recovery_pending_fills_unproven') from None
        if state != OrderState.FILLED or filled != order.executed_amount_base or rows[0].get('tdMode') != 'cash':
            raise RecoveryBlocked('recovery_pending_fills_unproven')
    return fill_ids(snapshot)
