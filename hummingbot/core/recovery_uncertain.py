"""GET-only checks for an expired request, independent of the fill proof."""
import asyncio

from hummingbot.core.checkpoint_recovery import venue_id
from hummingbot.core.recovery import RecoveryBlocked
from hummingbot.model.recovery_uncertain import SETTLEMENT_GRACE_MS, change_for
from hummingbot.model.runtime_journal import credential_fingerprint

PAGE_SIZE = 100
MAX_PAGES = 20


async def verify_uncertain_order(candidate, readers):
    reader = readers['okx']
    if credential_fingerprint(reader.connector) != candidate['credential_fingerprint']:
        raise RecoveryBlocked('exchange_account_mismatch')
    identity, pair = candidate['id'], candidate['pair']

    async def get(route, params):
        try:
            result = await asyncio.wait_for(reader._api_get(
                path_url=route, params=params, is_auth_required=True, return_err=True,
                limit_id='/api/v5/account/config'), 10)
        except OSError as error:
            raise RecoveryBlocked('recovery_exchange_unavailable') from error
        if not isinstance(result, dict) or not isinstance(result.get('data'), list):
            raise RecoveryBlocked('recovery_exchange_unavailable')
        return result

    async def require_missing():
        result = await get('/api/v5/trade/order', {'instId': pair, 'clOrdId': identity})
        if result.get('code') == '51603' and not result['data']:
            return
        if result.get('code') == '0':
            raise RecoveryBlocked('recovery_uncertain_order_found')
        raise RecoveryBlocked('recovery_exchange_unavailable')

    await require_missing()
    start = max(0, candidate['created_ms'] - SETTLEMENT_GRACE_MS)
    end = candidate['deadline_ms'] + SETTLEMENT_GRACE_MS
    params = dict(instType='SPOT', instId=pair, begin=str(start), end=str(end), limit=str(PAGE_SIZE))
    seen = set()
    for _ in range(MAX_PAGES):
        result = await get('/api/v5/trade/orders-history', params)
        if result.get('code') != '0':
            raise RecoveryBlocked('recovery_exchange_unavailable')
        page = result['data']
        if len(page) > PAGE_SIZE:
            raise RecoveryBlocked('recovery_exchange_scope_incomplete')
        for row in page:
            if not isinstance(row, dict) or row.get('instId') != pair:
                raise RecoveryBlocked('recovery_exchange_scope_incomplete')
            if row.get('clOrdId') == identity:
                raise RecoveryBlocked('recovery_uncertain_order_found')
            order_id = venue_id(row, 'ordId')
            stamp = row.get('cTime')
            if order_id in seen or not isinstance(stamp, str) or not stamp.isdecimal() or not start <= int(stamp) <= end:
                raise RecoveryBlocked('recovery_exchange_scope_incomplete')
            seen.add(order_id)
        if len(page) < PAGE_SIZE:
            await require_missing()
            return change_for(candidate)
        params['after'] = order_id
    raise RecoveryBlocked('recovery_exchange_scope_incomplete')
