"""Positive GET-only cancellation proofs; never infer absence or submit orders."""
import asyncio

from hummingbot.core.recovery import RecoveryBlocked, decimal
from hummingbot.model.runtime_journal import credential_fingerprint


def change_for(candidate, timestamp):
    return dict(id=candidate['id'], connector=candidate['connector'], pair=candidate['pair'],
                side=candidate['side'], position=candidate['position'], amount=candidate['amount'], price=candidate['price'],
                exchange_order_id=candidate['exchange_order_id'], terminal_timestamp=timestamp,
                before=dict(status=candidate['last_status'], attempt_state='acknowledged', tracked=True),
                after=dict(status='OrderCancelled', attempt_state='terminal', tracked=False),
                evidence='native_canceled_zero_fill')


def validate_changes(snapshot, changes):
    candidates = snapshot.get('terminal_orders', [])
    if not isinstance(changes, list) or len(changes) != len(candidates):
        raise RecoveryBlocked('recovery_terminal_order_unproven')
    if not candidates:
        return
    now = int(snapshot['state']['database_now'].timestamp() * 1000)
    for candidate, change in zip(candidates, changes):
        stamp = change.get('terminal_timestamp') if isinstance(change, dict) else None
        if (type(stamp) is not int or not max(candidate['creation_timestamp'], candidate['last_update_timestamp']) <= stamp <= now
                or change != change_for(candidate, stamp)):
            raise RecoveryBlocked('recovery_terminal_order_unproven')


async def verify_terminal_orders(snapshot, readers):
    candidates = snapshot.get('terminal_orders', [])
    if not candidates:
        return []
    semaphore = asyncio.Semaphore(4)

    async def check(candidate):
        async with semaphore:
            name, pair = candidate['connector'], candidate['pair']
            reader = readers[name]
            if credential_fingerprint(reader.connector) != candidate['credential_fingerprint']:
                raise RecoveryBlocked('exchange_account_mismatch')
            limit = ('' if name == 'okx' else 'GET-') + '/api/v5/account/config'

            async def get(path, params):
                response = await asyncio.wait_for(
                    reader._api_get(path_url=path, params=params, is_auth_required=True, return_err=True, limit_id=limit), 10)
                if isinstance(response, dict) and response.get('code') not in ('0', '51603'):
                    raise RecoveryBlocked('recovery_exchange_unavailable')
                if not isinstance(response, dict) or response.get('code') != '0' or not isinstance(response.get('data'), list):
                    raise RecoveryBlocked('recovery_terminal_order_unproven')
                return response['data']

            async def status():
                symbol = pair + ('-SWAP' if name == 'okx_perpetual' else '')
                rows = await get('/api/v5/trade/order', dict(instId=symbol, clOrdId=candidate['id']))
                if len(rows) != 1:
                    raise RecoveryBlocked('recovery_terminal_order_unproven')
                actual = rows[0]
                amount = decimal(actual.get('sz'))
                if name == 'okx_perpetual':
                    amount = reader._format_size_to_amount(pair, amount)
                if (actual.get('clOrdId') != candidate['id'] or actual.get('ordId') != candidate['exchange_order_id']
                        or actual.get('instId') != symbol or actual.get('side') != candidate['side'].lower()
                        or actual.get('ordType') != 'post_only' or actual.get('state') != 'canceled'
                        or decimal(actual.get('accFillSz')) != 0 or amount != decimal(candidate['amount'])
                        or decimal(actual.get('px')) != decimal(candidate['price'])):
                    raise RecoveryBlocked('recovery_terminal_order_unproven')
                if name == 'okx_perpetual':
                    leg = 'long' if (candidate['side'] == 'BUY') == (candidate['position'] == 'OPEN') else 'short'
                    if actual.get('posSide') != leg or actual.get('tdMode') != 'cross':
                        raise RecoveryBlocked('recovery_terminal_order_unproven')
                elif actual.get('tdMode') != 'cash':
                    raise RecoveryBlocked('recovery_terminal_order_unproven')
                stamp = actual.get('uTime')
                if not isinstance(stamp, str) or not stamp.isdecimal():
                    raise RecoveryBlocked('recovery_terminal_order_unproven')
                return change_for(candidate, int(stamp))

            first = await status()
            fills = await get('/api/v5/trade/fills', dict(instType='SPOT' if name == 'okx' else 'SWAP',
                                                          ordId=candidate['exchange_order_id'], limit='100'))
            if fills or await status() != first:
                raise RecoveryBlocked('recovery_terminal_order_unproven')
            return first

    tasks = [asyncio.create_task(check(candidate)) for candidate in candidates]
    try:
        changes = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    validate_changes(snapshot, changes)
    return changes
