"""Restartable GET-only checkpoint proof; every incomplete segment stays fenced."""
import asyncio
import hashlib
import time
from copy import deepcopy

from hummingbot.connector.credential_adapters import OKXCredentialAdapter
from hummingbot.connector.okx_environment import OKXEnvironment
from hummingbot.core.bot_ownership import BotOwnership
from hummingbot.core.checkpoint_recovery import checked_trades, position_scope, venue_id
from hummingbot.core.recovery import RecoveryBlocked, decimal, fingerprint

PAGE_SIZE = 100
PAGE_BUDGET = 100
SEGMENT_SECONDS = 90  # The unchanged 120s job retains a finalization margin.
MAX_PROOF_FILLS = 250000
MAX_PROOF_AGE_MS = 30 * 60 * 1000
EMPTY_DIGEST = hashlib.sha256(b'checkpoint-proof-v1').hexdigest()


def public_progress(progress):
    return dict(verified=False, scope='verified_checkpoint', progress=dict(
        scan=min(progress['scan'] + 1, 2), scans_required=2,
        pages_checked=progress['pages'], fills_checked=progress['count'],
        expected_fills=progress['expected'], complete=False))


def validate_progress(progress, snapshot, *, names=None):
    """Reject corrupted private receipts rather than infer missing evidence."""
    expected = snapshot['checkpoint']['expected_fills']
    names = sorted(names or [r['connector'] for r in snapshot['bindings'] if r['state'] == 'active'])
    if (not isinstance(progress, dict) or progress.get('version') != 1
            or progress.get('fingerprint') != fingerprint(snapshot) or progress.get('expected') != expected
            or any(type(progress.get(k)) is not int for k in ('scan', 'seq', 'count', 'now_ms', 'pages', 'connector_index'))
            or progress['scan'] not in (0, 1, 2) or progress['seq'] < 0
            or not 0 <= progress['count'] <= expected
            or not 0 <= progress['pages'] <= 2 * (MAX_PROOF_FILLS // PAGE_SIZE + len(names))
            or not 0 <= progress['connector_index'] <= len(names) or progress.get('verified') is not False
            or not isinstance(progress.get('heads'), dict) or not set(progress['heads']) <= set(names)
            or not isinstance(progress.get('venue_heads'), dict) or set(progress['venue_heads']) != set(names)):
        raise RecoveryBlocked('recovery_state_changed')
    try:
        if len(progress['digest']) != 64 or len(bytes.fromhex(progress['digest'])) != 32:
            raise ValueError()
        for value in (*progress['heads'].values(), *progress['venue_heads'].values(), progress.get('cursor')):
            if value is not None:
                venue_id({'billId': value}, 'billId')
    except (KeyError, TypeError, ValueError):
        raise RecoveryBlocked('recovery_state_changed')
    if progress['scan'] >= 1:
        first = progress.get('first')
        if (not isinstance(first, dict) or first.get('count') != expected
                or not isinstance(first.get('heads'), dict) or set(first['heads']) != set(names)
                or not isinstance(first.get('digest'), str) or len(first['digest']) != 64):
            raise RecoveryBlocked('recovery_state_changed')
    clock = int(snapshot['state']['database_now'].timestamp() * 1000)
    if not 0 <= clock - progress['now_ms'] <= MAX_PROOF_AGE_MS:
        raise RecoveryBlocked('recovery_proof_expired')


async def verify_segment(snapshot, readers, lookup, progress, save, *, page_budget=PAGE_BUDGET):
    """Return complete proof or None; save atomically persists progress + IDs.

    A lost/cancelled worker resumes from the last durable page. Callers must
    bind saved progress to the current owner/control/actor and private receipt.
    """
    if type(page_budget) is not int or not 1 <= page_budget <= PAGE_BUDGET:
        raise ValueError('Invalid recovery page budget')
    checkpoint, scope = snapshot['checkpoint'], fingerprint(snapshot)
    clock = int(snapshot['state']['database_now'].timestamp() * 1000)
    expected = checkpoint['expected_fills']
    if type(expected) is not int or not 0 <= expected <= MAX_PROOF_FILLS:
        raise RecoveryBlocked('recovery_scope_limit')
    if progress is not None:
        validate_progress(progress, snapshot)
    end = progress['now_ms'] if progress else clock
    anchor = checkpoint['anchor']
    since = anchor['verified_at_ms']
    if not 0 <= end - since < 89 * 86400000:
        raise RecoveryBlocked('recovery_history_window_exceeded')
    route = '/api/v5/trade/fills' if end - since < 2 * 86400000 else '/api/v5/trade/fills-history'
    environment = OKXEnvironment.load()
    rows = [r for r in snapshot['bindings'] if r['state'] == 'active']
    active = {r['connector']: r for r in rows}
    if (not active or not set(active) <= {'okx', 'okx_perpetual'} or not set(active) <= readers.keys()
            or len(active) != len(rows) or len({r['account_id'] for r in rows}) != 1
            or any(r.get('exchange') != 'okx' or r['environment'] != environment.mode
                   or not r['account_id'].startswith(environment.site + ':') for r in rows)):
        raise RecoveryBlocked('exchange_account_mismatch')
    names, positions = sorted(active), position_scope(snapshot)

    async def get(name, path, params=None):
        reader = readers[name]
        limit = ('' if name == 'okx' else 'GET-') + '/api/v5/account/config'
        result = await asyncio.wait_for(reader._api_get(path_url=path, params=params or {},
                                                        is_auth_required=True, return_err=True, limit_id=limit), 10)
        if not isinstance(result, dict) or result.get('code') != '0' or not isinstance(result.get('data'), list):
            raise RecoveryBlocked('recovery_exchange_unavailable')
        return result['data']

    async def open_orders():
        for name in names:
            if await BotOwnership._open_orders(readers[name], max_pages=100):
                raise RecoveryBlocked('recovery_open_orders_present')

    async def exposure():
        if 'okx_perpetual' in active:
            if await OKXCredentialAdapter().cleanup_positions(readers['okx_perpetual']) != positions:
                raise RecoveryBlocked('recovery_position_mismatch')
        elif any(decimal(r['pos']) for r in await get('okx', '/api/v5/account/positions', {'instType': 'SWAP'})):
            raise RecoveryBlocked('recovery_position_mismatch')

    async def heads():
        value = {}
        for name in names:
            page = await get(name, route, {'instType': 'SPOT' if name == 'okx' else 'SWAP', 'limit': '1'})
            if len(page) > 1:
                raise RecoveryBlocked('recovery_exchange_scope_incomplete')
            value[name] = venue_id(page[0], 'billId') if page else None
            if page and int(page[0]['ts']) > end:
                raise RecoveryBlocked('recovery_exchange_changed')
        return value

    started = time.monotonic()
    for name in names:
        account = await get(name, '/api/v5/account/config')
        if len(account) != 1 or str(account[0].get('uid')) != active[name]['account_id'].split(':', 1)[-1]:
            raise RecoveryBlocked('exchange_account_mismatch')
        if name == 'okx_perpetual':
            if account[0].get('posMode') != 'long_short_mode' or account[0].get('acctLv') not in ('2', '3'):
                raise RecoveryBlocked('unsupported_cleanup_mode')
            await readers[name]._initialize_trading_pair_symbol_map()
    await open_orders()
    current_heads = await heads()
    if progress is None:
        after = dict(version=1, fingerprint=scope, now_ms=end, scan=0, connector_index=0, cursor=None,
                     digest=EMPTY_DIGEST, heads={}, venue_heads=current_heads, count=0, expected=expected,
                     pages=0, seq=0, verified=False, first=None)
        await save(None, after, [])
        progress = after
    elif current_heads != progress['venue_heads']:
        raise RecoveryBlocked('recovery_exchange_changed')
    budget = 0
    while progress['scan'] < 2:
        # A worker may stop after the final page commits but before the pass
        # transition commits. Resume that boundary before indexing a reader.
        if progress['connector_index'] == len(names):
            if progress['count'] != expected:
                raise RecoveryBlocked('recovery_fill_coverage_incomplete')
            await exposure()
            await open_orders()
            if await heads() != progress['venue_heads']:
                raise RecoveryBlocked('recovery_exchange_changed')
            summary = dict(digest=progress['digest'], heads=progress['heads'], count=progress['count'])
            if progress['scan'] == 1 and summary != progress['first']:
                raise RecoveryBlocked('recovery_exchange_changed')
            after = deepcopy(progress)
            after.update(scan=progress['scan'] + 1, connector_index=0, cursor=None, seq=progress['seq'] + 1)
            if progress['scan'] == 0:
                after.update(first=summary, digest=EMPTY_DIGEST, heads={}, count=0)
            await save(progress, after, [])
            progress = after
            continue
        if budget >= page_budget or time.monotonic() - started >= SEGMENT_SECONDS:
            if await heads() != progress['venue_heads']:
                raise RecoveryBlocked('recovery_exchange_changed')
            return None
        name = names[progress['connector_index']]
        target = (anchor.get('venue_heads') or {}).get(name)
        params = dict(instType='SPOT' if name == 'okx' else 'SWAP', limit=str(PAGE_SIZE), end=str(end))
        if target is None:
            params['begin'] = str(since)
        if progress['cursor'] is not None:
            params['after'] = progress['cursor']
        page = await get(name, route, params)
        if len(page) > PAGE_SIZE:
            raise RecoveryBlocked('recovery_exchange_scope_incomplete')
        after = deepcopy(progress)
        if progress['cursor'] is None:
            after['heads'][name] = venue_id(page[0], 'billId') if page else None
        selected, found = [], False
        for raw in page:
            bill = venue_id(raw, 'billId')
            if target is not None and bill == target or target is None and int(raw['ts']) < since:
                found = True
                break
            if int(raw['ts']) > end:
                raise RecoveryBlocked('recovery_exchange_changed')
            selected.append(((name, venue_id(raw, 'ordId'), venue_id(raw, 'tradeId')), raw))
        keys = [key for key, _ in selected]
        if len(set(keys)) != len(keys):
            raise RecoveryBlocked('recovery_unrecorded_or_duplicate_venue_fill')
        local = await lookup(keys, anchor['journal_watermark']) if keys else []
        entries = []
        for key, bill, encoded in checked_trades(readers[name], name, selected, local):
            after['digest'] = hashlib.sha256(bytes.fromhex(after['digest']) + encoded).hexdigest()
            entries.append((*key, bill))
        after['count'] += len(entries)
        after['pages'] += 1
        after['seq'] += 1
        if after['count'] > expected or after['pages'] > 2 * (MAX_PROOF_FILLS // PAGE_SIZE + len(names)):
            raise RecoveryBlocked('recovery_scope_limit')
        finished = found or len(page) < PAGE_SIZE
        if finished:
            if target is not None and not found:
                raise RecoveryBlocked('recovery_exchange_scope_incomplete')
            after['connector_index'] += 1
            after['cursor'] = None
        else:
            after['cursor'] = venue_id(page[-1], 'billId')
            if after['cursor'] == progress['cursor']:
                raise RecoveryBlocked('recovery_exchange_scope_incomplete')
        # Persist entries under the pass that read them, before advancing pass.
        await save(progress, after, entries)
        progress, budget = after, budget + 1
    if (dict(digest=progress['digest'], heads=progress['heads'], count=progress['count']) != progress['first']
            or await heads() != progress['venue_heads']):
        raise RecoveryBlocked('recovery_exchange_changed')
    await open_orders()
    await exposure()
    return dict(verified=True, segmented=True, proof_digest=progress['digest'],
                scope='verified_checkpoint', fills_checked=expected, orders_checked=0,
                position_legs_checked=len(positions), pending_orders=0, venue_heads=progress['heads'],
                baseline_journal_watermark=anchor['journal_watermark'], venue_history_days=3 if route.endswith('/fills') else 89)
