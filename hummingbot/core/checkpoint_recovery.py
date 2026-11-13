"""Bounded account-wide exchange proof after a reviewed financial baseline."""
import asyncio
import hashlib
import json
from types import SimpleNamespace

from hummingbot.connector.credential_adapters import OKXCredentialAdapter
from hummingbot.connector.markets_recorder import MarketsRecorder
from hummingbot.connector.okx_environment import OKXEnvironment
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.data_type.common import TradeType
from hummingbot.core.recovery import RecoveryBlocked, decimal

PAGE_SIZE = 100
MAX_PAGES = 100


def venue_id(raw, field):
    value = raw.get(field)
    if (type(value) not in (str, int) or not 1 <= len(str(value)) <= 64
            or not str(value).isascii() or not str(value).isdigit() or int(value) <= 0):
        raise RecoveryBlocked('recovery_exchange_scope_incomplete')
    return str(value)


def position_scope(snapshot):
    positions, seen = {}, set()
    active = {row['connector'] for row in snapshot['bindings'] if row['state'] == 'active'}
    scopes = [value['mm_drawdown_policy']['scope']
              for value in (snapshot['state'].get('recovery_state', {}).get('strategy_checkpoints') or {}).values()
              if isinstance(value, dict) and isinstance(value.get('mm_drawdown_policy'), dict)]
    for row in snapshot['positions']:
        connector, pair, side = row['connector_name'], row['trading_pair'], row['side']
        key = connector, pair, side if connector == 'okx_perpetual' else 'NET'
        if (connector not in active or not pair.endswith('-USDT') or side not in ('BUY', 'SELL')
                or not row['controller_id'] or key in seen or decimal(row['amount']) < 0):
            raise RecoveryBlocked('recovery_position_scope_ambiguous')
        if any(scope.get(row['controller_id']) != dict(connector_name=connector, trading_pair=pair) for scope in scopes):
            raise RecoveryBlocked('recovery_position_scope_ambiguous')
        seen.add(key)
        if connector == 'okx_perpetual' and decimal(row['amount']):
            positions[pair, 'LONG' if side == 'BUY' else 'SHORT'] = decimal(row['amount'])
    return positions


async def verify_checkpoint_exchange(snapshot, readers, lookup):
    checkpoint = snapshot['checkpoint']; anchor = checkpoint['anchor']
    now = int(snapshot['state']['database_now'].timestamp() * 1000)
    since = anchor['verified_at_ms']
    if not 0 <= now - since < 89 * 86400000:
        raise RecoveryBlocked('recovery_history_window_exceeded')
    path = '/api/v5/trade/fills' if now - since < 2 * 86400000 else '/api/v5/trade/fills-history'
    environment = OKXEnvironment.load()
    active_rows = [row for row in snapshot['bindings'] if row['state'] == 'active']
    active = {row['connector']: row for row in active_rows}
    if (not active or not set(active) <= {'okx', 'okx_perpetual'} or not set(active) <= readers.keys()
            or len(active) != len(active_rows) or len({row['account_id'] for row in active_rows}) != 1
            or any(row.get('exchange') != 'okx' or row['environment'] != environment.mode
                   or not row['account_id'].startswith(environment.site + ':') for row in active_rows)):
        raise RecoveryBlocked('exchange_account_mismatch')
    positions = position_scope(snapshot)

    async def get(reader, route, params=None):
        # Recovery-only readers share a conservative registered 5/2s bucket,
        # including the slower archive endpoint, instead of the order bucket.
        limit_id = ('' if reader.name == 'okx' else 'GET-') + '/api/v5/account/config'
        result = await asyncio.wait_for(reader._api_get(
            path_url=route, params=params or {}, is_auth_required=True, return_err=True, limit_id=limit_id), 10)
        if not isinstance(result, dict) or result.get('code') != '0' or not isinstance(result.get('data'), list):
            raise RecoveryBlocked('recovery_exchange_unavailable')
        return result['data']

    for name in sorted(active):
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

    async def scan():
        digest, heads, observed = hashlib.sha256(), {}, set()
        for name in sorted(active):
            reader = readers[name]
            target = (anchor.get('venue_heads') or {}).get(name)
            params = dict(instType='SPOT' if name == 'okx' else 'SWAP', limit=str(PAGE_SIZE), end=str(now))
            if target is None:
                params['begin'] = str(since)
            cursors, found = set(), False
            for index in range(MAX_PAGES):
                page = await get(reader, path, params)
                if len(page) > PAGE_SIZE:
                    raise RecoveryBlocked('recovery_exchange_scope_incomplete')
                if index == 0:
                    heads[name] = venue_id(page[0], 'billId') if page else None
                selected = []
                for raw in page:
                    bill = venue_id(raw, 'billId')
                    if target is not None and bill == target:
                        found = True
                        break
                    stamp = int(raw['ts'])
                    if target is None and stamp < since:
                        found = True
                        break
                    if stamp > now:
                        raise RecoveryBlocked('recovery_exchange_changed')
                    key = name, venue_id(raw, 'ordId'), venue_id(raw, 'tradeId')
                    if not all(key) or key in observed:
                        raise RecoveryBlocked('recovery_unrecorded_or_duplicate_venue_fill')
                    observed.add(key); selected.append((key, raw))
                if selected:
                    rows = await lookup([key for key, _ in selected], anchor['journal_watermark'])
                    local = {(r['fill']['market'], str(r['order']['exchange_order_id']), r['fill']['exchange_trade_id']): r for r in rows}
                    if len(local) != len(rows) or local.keys() != {key for key, _ in selected}:
                        raise RecoveryBlocked('recovery_unrecorded_or_duplicate_venue_fill')
                    for key, raw in selected:
                        row = local[key]; fill, order, journal = row['fill'], row['order'], row['journal']
                        if (raw.get('instId') != fill['symbol'] + ('-SWAP' if name == 'okx_perpetual' else '')
                                or raw.get('side') != fill['trade_type'].lower()
                                or raw.get('clOrdId') not in (None, '', order['id']) or order['creation_pending']):
                            raise RecoveryBlocked('recovery_order_identity_unproven')
                        if name == 'okx_perpetual':
                            leg = 'long' if (fill['trade_type'] == 'BUY') == (fill['position'] == 'OPEN') else 'short'
                            if fill['position'] not in ('OPEN', 'CLOSE') or raw.get('posSide') != leg:
                                raise RecoveryBlocked('recovery_position_scope_ambiguous')
                        tracked = SimpleNamespace(client_order_id=order['id'], trading_pair=fill['symbol'], trade_type=TradeType[fill['trade_type']])
                        trade = (reader.connector._trade_update_from_fill(tracked, raw) if name == 'okx'
                                 else reader.connector._parse_trade_update(raw, tracked))
                        if (decimal(fill['amount']) <= 0 or decimal(fill['price']) <= 0
                                or trade.fill_base_amount != fill['amount'] or trade.fill_price != fill['price']):
                            raise RecoveryBlocked('recovery_fill_mismatch')
                        if not MarketsRecorder._same_trade_fee(fill['trade_fee'], trade.fee.to_json()):
                            raise RecoveryBlocked('recovery_original_fee_mismatch')
                        fee = -decimal(raw['fee'])
                        if raw['feeCcy'] == 'USDT':
                            expected_fee = fee
                        elif name == 'okx' and raw['feeCcy'] == fill['symbol'].removesuffix('-USDT'):
                            expected_fee = fee * decimal(fill['price'])
                        else:
                            raise RecoveryBlocked('recovery_fee_basis_unsupported')
                        if expected_fee != decimal(fill['trade_fee_in_quote']):
                            raise RecoveryBlocked('recovery_original_fee_mismatch')
                        if (any(str(journal.get(k)) != str(fill[k]) for k in ('market', 'symbol', 'trade_type', 'exchange_trade_id', 'timestamp'))
                                or any(decimal(journal.get(k)) != decimal(fill[k]) for k in ('amount', 'price', 'trade_fee_in_quote'))
                                or not MarketsRecorder._same_trade_fee(fill['trade_fee'], journal.get('trade_fee'))):
                            raise RecoveryBlocked('recovery_financial_data_invalid')
                        # Event-clock timestamps are retained as metadata. Native
                        # identity/economics and complete journal coverage prove fills.
                        digest.update(json.dumps([key, str(raw['billId']), raw], sort_keys=True, default=str).encode() + b'\n')
                if found or len(page) < PAGE_SIZE:
                    if target is not None and not found:
                        raise RecoveryBlocked('recovery_exchange_scope_incomplete')
                    break
                cursor = venue_id(page[-1], 'billId')
                if not cursor or cursor in cursors or index + 1 == MAX_PAGES:
                    raise RecoveryBlocked('recovery_exchange_scope_incomplete')
                cursors.add(cursor); params['after'] = cursor
        if len(observed) != checkpoint['expected_fills']:
            raise RecoveryBlocked('recovery_fill_coverage_incomplete')
        return digest.hexdigest(), heads, len(observed)

    async def exposure():
        if 'okx_perpetual' in active:
            if await OKXCredentialAdapter().cleanup_positions(readers['okx_perpetual']) != positions:
                raise RecoveryBlocked('recovery_position_mismatch')
        else:
            rows = await get(readers['okx'], '/api/v5/account/positions', {'instType': 'SWAP'})
            if any(decimal(r['pos']) for r in rows):
                raise RecoveryBlocked('recovery_position_mismatch')

    first = await scan()
    await exposure()
    for name in active:
        if await BotOwnership._open_orders(readers[name], max_pages=MAX_PAGES):
            raise RecoveryBlocked('recovery_exchange_changed')
    if await scan() != first:
        raise RecoveryBlocked('recovery_exchange_changed')
    await exposure()
    return dict(verified=True, scope='verified_checkpoint', fills_checked=first[2], orders_checked=0,
                position_legs_checked=len(positions), pending_orders=0, venue_heads=first[1],
                baseline_journal_watermark=anchor['journal_watermark'], venue_history_days=3 if path.endswith('/fills') else 89)
