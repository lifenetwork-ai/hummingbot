"""Bounded, GET-only venue proof for a stopped dedicated OKX account."""
import asyncio
from collections import defaultdict
from decimal import Decimal

from hummingbot.connector.okx_environment import OKXEnvironment
from hummingbot.connector.okx_order_status import OKXOrderNotFoundError, order_status_data
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.recovery import MAX_FILLS, RecoveryBlocked, decimal

PAGE_LIMIT = 100
MAX_PAGES = MAX_FILLS // PAGE_LIMIT + 1
TERMINAL = {'BuyOrderCompleted': 'filled', 'SellOrderCompleted': 'filled', 'OrderCancelled': 'canceled',
            'OrderExpired': 'canceled', 'OrderFailure': 'failed'}


async def verify_exchange(snapshot, plan, readers):
    """Readers are non-trading connector instances. Never start their networks."""
    orders = {row['id']: row for row in snapshot['orders']}
    if any(row['creation_pending'] for row in orders.values()):
        raise RecoveryBlocked('recovery_pending_intents')
    names = {row['market'] for row in orders.values()} | {row['connector_name'] for row in snapshot['positions']}
    environment = OKXEnvironment.load()
    active_rows = [row for row in snapshot['bindings'] if row['state'] == 'active']
    if any(row['exchange'] != 'okx' or row['environment'] != environment.mode
           or not row['account_id'].startswith(environment.site + ':') for row in active_rows):
        raise RecoveryBlocked('exchange_account_mismatch')
    active = {row['connector']: row for row in active_rows}
    if len(active) != len(active_rows):
        raise RecoveryBlocked('exchange_account_mismatch')
    names.update(active)
    if not names or names - {'okx', 'okx_perpetual'} or names - set(readers) or names - set(active):
        raise RecoveryBlocked('recovery_credentials_unavailable')
    if len({row['account_id'] for row in active.values()}) != 1:
        raise RecoveryBlocked('exchange_account_mismatch')
    now_ms = int(snapshot['state']['database_now'].timestamp() * 1000)
    earliest = min((row['creation_timestamp'] for row in orders.values()), default=now_ms)
    if earliest < now_ms - 3 * 86400 * 1000:
        raise RecoveryBlocked('recovery_history_window_exceeded')
    fills = defaultdict(list)
    for fill in snapshot['fills']:
        if fill['order_id'] not in orders:
            raise RecoveryBlocked('recovery_order_identity_unproven')
        fills[fill['order_id']].append(fill)
    expected = {}
    for fill in snapshot['fills']:
        order = orders[fill['order_id']]
        if order['exchange_order_id'] is None:
            raise RecoveryBlocked('recovery_order_identity_unproven')
        key = fill['market'], str(order['exchange_order_id']), str(fill['exchange_trade_id'])
        expected[key] = fill
    if len(expected) != len(snapshot['fills']):
        raise RecoveryBlocked('recovery_duplicate_fill')

    async def get(reader, path, params=None, *, auth=True):
        result = await asyncio.wait_for(reader._api_get(path_url=path, params=params or {}, is_auth_required=auth,
                                                        return_err=True), timeout=10)
        if not isinstance(result, dict) or str(result.get('code')) != '0' or not isinstance(result.get('data'), list):
            raise RecoveryBlocked('recovery_exchange_unavailable')
        return result['data']

    for name in sorted(names):
        reader = readers[name]
        account = await get(reader, '/api/v5/account/config')
        if len(account) != 1 or str(account[0].get('uid')) != active[name]['account_id'].split(':', 1)[-1]:
            raise RecoveryBlocked('exchange_account_mismatch')
        if name == 'okx_perpetual':
            if account[0].get('posMode') != 'long_short_mode' or account[0].get('acctLv') not in ('2', '3'):
                raise RecoveryBlocked('unsupported_cleanup_mode')
            await reader._initialize_trading_pair_symbol_map()
        if await BotOwnership._open_orders(reader, max_pages=MAX_PAGES):
            raise RecoveryBlocked('recovery_open_orders_present')

    async def check_order(order):
        reader = readers[order['market']]
        symbol = order['symbol'] + ('-SWAP' if order['market'] == 'okx_perpetual' else '')
        result = await asyncio.wait_for(reader._api_get(path_url='/api/v5/trade/order', params={
            'instId': symbol, 'clOrdId': order['id']}, is_auth_required=True, return_err=True), 10)
        try:
            actual = order_status_data(result)
        except OKXOrderNotFoundError:
            if order['exchange_order_id'] is None and order['last_status'] == 'OrderFailure' and not fills[order['id']]:
                return
            raise RecoveryBlocked('recovery_order_identity_unproven') from None
        if (actual.get('clOrdId') != order['id'] or str(actual.get('ordId')) != str(order['exchange_order_id'])
                or actual.get('instId') != symbol):
            raise RecoveryBlocked('recovery_order_identity_unproven')
        total = sum((decimal(row['amount']) for row in fills[order['id']]), Decimal(0))
        cumulative = decimal(actual.get('accFillSz'))
        amount = decimal(actual.get('sz'))
        if order['market'] == 'okx_perpetual':
            cumulative = reader._format_size_to_amount(order['symbol'], cumulative)
            amount = reader._format_size_to_amount(order['symbol'], amount)
        status = TERMINAL.get(order['last_status'])
        if order['last_status'] == 'OrderFilled' and total == decimal(order['amount']):
            status = 'filled'
        if status is None or actual.get('state') != status or cumulative != total or amount != decimal(order['amount']):
            raise RecoveryBlocked('recovery_order_state_mismatch')
        side = order.get('side') or (fills[order['id']][0]['trade_type'] if fills[order['id']] else None)
        if side not in ('BUY', 'SELL') or actual.get('side') != side.lower():
            raise RecoveryBlocked('recovery_order_identity_unproven')
        expected_type = {'LIMIT': {'limit'}, 'LIMIT_MAKER': {'post_only'}, 'MARKET': {'market'}}.get(order['order_type'], set())
        if actual.get('ordType') not in expected_type:
            raise RecoveryBlocked('recovery_order_state_mismatch')
        if order['market'] == 'okx_perpetual':
            legs = {('long' if (fill['trade_type'] == 'BUY') == (fill['position'] == 'OPEN') else 'short')
                    for fill in fills[order['id']]}
            if actual.get('tdMode') != 'cross' or actual.get('posSide') not in ('long', 'short') or (legs and legs != {actual['posSide']}):
                raise RecoveryBlocked('recovery_position_scope_ambiguous')
        if order['order_type'] in ('LIMIT', 'LIMIT_MAKER') and decimal(actual.get('px')) != decimal(order['price']):
            raise RecoveryBlocked('recovery_order_state_mismatch')

    ordered = list(orders.values())
    for offset in range(0, len(ordered), 4):
        tasks = [asyncio.create_task(check_order(row)) for row in ordered[offset:offset + 4]]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def venue_fills():
        observed = {}
        for name in sorted(names):
            reader = readers[name]
            params = {'instType': 'SWAP' if name == 'okx_perpetual' else 'SPOT', 'limit': str(PAGE_LIMIT)}
            cursors = set()
            for page_index in range(MAX_PAGES):
                page = await get(reader, '/api/v5/trade/fills', params)
                times = []
                for row in page:
                    timestamp = int(row['ts'])
                    times.append(timestamp)
                    if timestamp < earliest:
                        continue
                    key = name, str(row['ordId']), str(row['tradeId'])
                    if key in observed or key not in expected:
                        raise RecoveryBlocked('recovery_unrecorded_or_duplicate_venue_fill')
                    fill = expected[key]
                    amount = decimal(row['fillSz'])
                    if name == 'okx_perpetual':
                        amount = reader._format_size_to_amount(fill['symbol'], amount)
                    symbol = fill['symbol'] + ('-SWAP' if name == 'okx_perpetual' else '')
                    if row.get('instId') != symbol or row.get('side') != fill['trade_type'].lower():
                        raise RecoveryBlocked('recovery_order_identity_unproven')
                    if name == 'okx_perpetual':
                        leg = 'long' if (fill['trade_type'] == 'BUY') == (fill['position'] == 'OPEN') else 'short'
                        if row.get('posSide') != leg:
                            raise RecoveryBlocked('recovery_position_scope_ambiguous')
                    if timestamp > now_ms or timestamp != fill['timestamp']:
                        raise RecoveryBlocked('recovery_exchange_changed')
                    if amount != decimal(fill['amount']) or decimal(row['fillPx']) != decimal(fill['price']):
                        raise RecoveryBlocked('recovery_fill_mismatch')
                    fees = defaultdict(Decimal)
                    for fee in fill['trade_fee'].get('flat_fees', []):
                        fees[fee['token']] += decimal(fee['amount'])
                    recorded_fees = {token: value for token, value in fees.items() if value}
                    actual_fees = {row['feeCcy']: -decimal(row['fee'])} if decimal(row['fee']) else {}
                    if decimal(fill['trade_fee'].get('percent', '0')) or recorded_fees != actual_fees:
                        raise RecoveryBlocked('recovery_original_fee_mismatch')
                    observed[key] = (amount, decimal(row['fillPx']), str(row['feeCcy']), decimal(row['fee']))
                if len(page) < PAGE_LIMIT or times and min(times) < earliest:
                    break
                cursor = str(page[-1].get('billId', '')) if page else ''
                if not cursor or cursor in cursors or page_index + 1 == MAX_PAGES:
                    raise RecoveryBlocked('recovery_exchange_scope_incomplete')
                cursors.add(cursor)
                params['after'] = cursor
        if set(observed) != set(expected):
            raise RecoveryBlocked('recovery_fill_coverage_incomplete')
        return observed

    first_fills = await venue_fills()
    proposed = {row['id']: row['after'] for row in plan}
    positions = {}
    for row in snapshot['positions']:
        if row['connector_name'] == 'okx_perpetual':
            value = proposed.get(row['id'], row)
            amount = decimal(value['amount'])
            if amount:
                positions[row['trading_pair'], 'LONG' if value['side'] == 'BUY' else 'SHORT'] = amount

    async def check_positions():
        if 'okx_perpetual' in names:
            from hummingbot.connector.credential_adapters import OKXCredentialAdapter
            actual = await OKXCredentialAdapter().cleanup_positions(readers['okx_perpetual'])
            if actual != positions:
                raise RecoveryBlocked('recovery_position_mismatch')
        else:
            rows = await get(readers['okx'], '/api/v5/account/positions', {'instType': 'SWAP'})
            if any(decimal(row['pos']) for row in rows):
                raise RecoveryBlocked('recovery_position_mismatch')

    await check_positions()
    for name in names:
        if await BotOwnership._open_orders(readers[name], max_pages=MAX_PAGES):
            raise RecoveryBlocked('recovery_exchange_changed')
    if await venue_fills() != first_fills:
        raise RecoveryBlocked('recovery_exchange_changed')
    await check_positions()
    return dict(verified=True, orders_checked=len(orders), fills_checked=len(expected),
                position_legs_checked=len(positions), pending_orders=0,
                scope='bounded_complete_local_history_and_dedicated_account', venue_history_days=3)
