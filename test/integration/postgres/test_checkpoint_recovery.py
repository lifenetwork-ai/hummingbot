"""Reviewed clean checkpoints must not replay a lifetime of financial history."""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from test.integration.postgres.test_recovery_store import ADMIN, recovering as _recovering
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from hummingbot.model.admin_access import AdminAuditEvent
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.exchange_credential import ExchangeCredential
from hummingbot.model.order import Order
from hummingbot.model.position import Position
from hummingbot.model.runtime_journal import RuntimeCheckpoint, RuntimeJournal, binding_hash, bindings, financial_hash
from hummingbot.model.trade_fill import TradeFill

recovering = _recovering


def seed_checkpoint(store, *, orders=5001, fills=2001, existing_binding=False):
    now = datetime.now(timezone.utc)
    anchor = now - timedelta(hours=2)
    old_stamp, stamp = int((anchor - timedelta(minutes=10)).timestamp() * 1000), int((anchor + timedelta(minutes=10)).timestamp() * 1000)
    fee = dict(fee_type='AddedToCost', percent='0', percent_token='USDT', flat_fees=[dict(token='USDT', amount='.1')])
    with store.engine.begin() as db:
        db.execute(update(BotState).where(BotState.bot_id == store.bot_id).values(config_type='mm-session', config_file_path='life_mm_profiles.yaml'))
        if not existing_binding:
            db.execute(ExchangeCredential.__table__.insert().values(bot_id=store.bot_id, exchange='okx', connector='okx',
                       environment='demo', version=1, account_id='www:42', state='active', envelope={'test': True}, changed_by='test', changed_at=now))
        db.execute(Order.__table__.insert(), [dict(id=f'o{i:05}', config_file_path='life_mm_profiles.yaml', strategy='life_mm',
                   market='okx', symbol='BTC-USDT', base_asset='BTC', quote_asset='USDT', creation_timestamp=old_stamp if i == 0 else stamp,
                   order_type='LIMIT_MAKER', amount=1, price=100, last_status='OrderFilled' if i < fills else 'OrderFailure',
                   last_update_timestamp=stamp, exchange_order_id=str(i), position='NIL', creation_pending=False, position_applied=True) for i in range(orders)])
        if fills:
            db.execute(TradeFill.__table__.insert(), [dict(config_file_path='life_mm_profiles.yaml', strategy='life_mm',
                       market='okx', symbol='BTC-USDT', base_asset='BTC', quote_asset='USDT', timestamp=old_stamp if i == 0 else stamp,
                       order_id=f'o{i:05}', trade_type='BUY', order_type='LIMIT_MAKER', price=100, amount=1,
                       trade_fee=fee, trade_fee_in_quote=Decimal('.1'), exchange_trade_id=str(i), position='NIL') for i in range(fills)])
        if fills > 1:
            db.execute(RuntimeJournal.__table__.insert(), [dict(event_key=f'fill:{i}', kind='fill', order_id=f'o{i:05}', applied=True,
                       payload=dict(market='okx', symbol='BTC-USDT', exchange_trade_id=str(i), price='100', amount='1',
                                    timestamp=stamp, trade_type='BUY', trade_fee=fee, trade_fee_in_quote='.1')) for i in range(1, fills)])
        if fills:
            db.execute(Position.__table__.insert().values(id='spot', controller_id='spot', connector_name='okx', side='BUY',
                       trading_pair='BTC-USDT', timestamp=stamp, volume_traded_quote=100 * fills, amount=fills,
                       breakeven_price=100, realized_pnl_quote=0, unrealized_pnl_quote=0, cum_fees_quote=Decimal('.1') * fills))
        db.execute(AdminAuditEvent.__table__.insert().values(bot_id=store.bot_id, event_key='legacy-baseline:' + 'a' * 64,
                   request_id='baseline', actor_kind='maintenance', actor_id='operator', actor_role='admin', action='runtime_baseline_bound',
                   target_id='life_mm_profiles.yaml', outcome='succeeded', details=dict(proof_sha256='a' * 64, verified_fills=min(fills, 1), rehearsal=False), created_at=anchor))
        with Session(bind=db) as session:
            session.add(RuntimeCheckpoint(config_file_path='life_mm_profiles.yaml', financial_hash=financial_hash(session),
                        bindings_hash=binding_hash(session, store.bot_id), watermark=db.scalar(select(func.max(RuntimeJournal.id))) or 0,
                        payload=dict(baseline='reviewed_legacy_maintenance', proof_sha256='a' * 64,
                                     environment={'mode': 'demo', 'site': 'www'}, bindings=bindings(session, store.bot_id),
                                     reduction_basis={'preserve': True})))
            session.flush()
    return anchor


def test_verified_checkpoint_avoids_lifetime_order_and_fill_caps(recovering, monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo'); monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    seed_checkpoint(store)
    data = store.snapshot()
    assert data.get('mode') == 'verified_checkpoint'
    assert not data['truncated'] and data['orders'] == data['fills'] == []
    assert data['checkpoint']['expected_fills'] == 2000
    assert data['positions'][0]['amount'] == Decimal(2001)
    assert owner.check_owner()['recovery_state']['halt']


@pytest.mark.parametrize('mutation', ['missing_audit', 'unauthorized_audit', 'financial', 'unjournaled', 'unapplied', 'binding'])
def test_unproven_or_changed_checkpoint_cannot_skip_complete_history(recovering, monkeypatch, mutation):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo'); monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    seed_checkpoint(store, orders=12, fills=3)
    with store.engine.begin() as db:
        if mutation == 'missing_audit':
            db.execute(AdminAuditEvent.__table__.delete())
        elif mutation == 'unauthorized_audit':
            db.execute(update(AdminAuditEvent).values(actor_role='read'))
        elif mutation == 'financial':
            db.execute(update(Position).values(amount=99))
        elif mutation == 'unapplied':
            db.execute(update(RuntimeJournal).values(applied=False))
        elif mutation == 'binding':
            db.execute(update(ExchangeCredential).values(account_id='www:other'))
        else:
            db.execute(RuntimeJournal.__table__.delete().where(RuntimeJournal.order_id == 'o00001'))
    assert store.snapshot().get('mode') != 'verified_checkpoint'
    assert owner.check_owner()['recovery_state']['halt']


def test_checkpoint_review_confirm_preserves_all_ledger_rows_and_remaining_costs(recovering, monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo'); monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    seed_checkpoint(store)
    request = store.request('recovery_check', 'checkpoint-check', {'expected_version': 0}, ADMIN, 'req')
    claimed = store.claim(owner); snapshot = store.snapshot()
    proof = dict(verified=True, scope='verified_checkpoint', venue_heads={'okx': '2001'})
    store.finish_check(owner, claimed, snapshot, [], proof, blockers=[])
    check = store.get(request['id'])
    payload = dict(expected_version=0, check_id=check['id'], confirmation_token=check['result']['confirmation_token'])
    reset = store.request('recovery_reset', 'checkpoint-reset', payload, ADMIN, 'reset')
    claimed = store.claim(owner)
    assert store.finish_reset(owner, claimed, store.snapshot(), [], proof)
    state = owner.check_owner()
    assert state['desired_state'] == 'stopped' and not state['recovery_state'].get('halt')
    assert store.get(reset['id'])['result']['stage'] == 'recovered'
    after = store.snapshot()
    assert after['mode'] == 'verified_checkpoint' and after['ledger_hash'] == snapshot['ledger_hash']
    assert after['positions'] == snapshot['positions']
    assert after['checkpoint']['expected_fills'] == 0
    assert after['checkpoint']['payload']['reduction_basis'] == {'preserve': True}
    assert after['checkpoint']['anchor']['operation_id'] == reset['id']
    assert after['checkpoint']['anchor']['venue_heads'] == {'okx': '2001'}
    payload = after['checkpoint']['payload']
    with store.engine.begin() as db:
        db.execute(update(RuntimeCheckpoint).values(payload={**payload, 'recovery_anchor': {
            **payload['recovery_anchor'], 'verified_at_ms': payload['recovery_anchor']['verified_at_ms'] + 1}}))
    assert store.snapshot().get('mode') != 'verified_checkpoint'


@pytest.mark.parametrize('mutation', ['old_order', 'binding', 'risk', 'checkpoint'])
def test_changed_proof_invalidates_preview(recovering, monkeypatch, mutation):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo'); monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    seed_checkpoint(store)
    row = store.request('recovery_check', 'check', {'expected_version': 0}, ADMIN, 'req')
    claimed = store.claim(owner); snapshot = store.snapshot()
    store.finish_check(owner, claimed, snapshot, [], dict(verified=True, scope='verified_checkpoint', venue_heads={'okx': '2'}), blockers=[])
    check = store.get(row['id'])
    with store.engine.begin() as db:
        if mutation == 'old_order':
            db.execute(update(Order).where(Order.id == 'o00000').values(price=101))
        elif mutation == 'binding':
            db.execute(update(ExchangeCredential).values(version=2))
        elif mutation == 'checkpoint':
            db.execute(update(RuntimeCheckpoint).values(watermark=0))
        else:
            state = owner.check_owner()
            db.execute(update(BotState).values(recovery_state={**state['recovery_state'], 'policy_changed': True}))
    with pytest.raises(ControlConflict, match='recovery_state_changed'):
        store.request('recovery_reset', 'reset', dict(expected_version=0, check_id=check['id'], confirmation_token=check['result']['confirmation_token']), ADMIN, 'reset')
    assert owner.check_owner()['recovery_state']['halt']


def test_bounded_native_lookup_joins_exact_order_trade_and_applied_journal(recovering, monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, _ = recovering
    seed_checkpoint(store, orders=12, fills=3)
    rows = store.checkpoint_fill_rows([('okx', '0', '0'), ('okx', '1', '1'), ('okx', '2', '2')], 0)
    assert {row['fill']['exchange_trade_id'] for row in rows} == {'1', '2'}
    assert all(row['journal']['exchange_trade_id'] == row['fill']['exchange_trade_id'] for row in rows)
    assert store.checkpoint_fill_rows([('okx', 'wrong-order', '1')], 0) == []
    assert store.checkpoint_fill_rows([('okx', '1', '1')], max(row['journal_id'] for row in rows)) == []


def test_pending_intent_beyond_legacy_page_is_still_visible(recovering, monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    seed_checkpoint(store, orders=5002, fills=0)
    with store.engine.begin() as db:
        db.execute(update(Order).where(Order.id == 'o05001').values(creation_pending=True))
    snapshot = store.snapshot()
    assert snapshot.get('mode') != 'verified_checkpoint'
    assert snapshot['truncated'] and snapshot['pending_intents']
    assert not any(row['creation_pending'] for row in snapshot['orders'])
    assert owner.check_owner()['recovery_state']['halt']


@pytest.mark.asyncio
async def test_large_postgres_ledger_verifies_complete_paged_native_venue_suffix(recovering, monkeypatch):
    from hummingbot.cli.recovery_runtime import RecoveryReader
    from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
    from hummingbot.core.checkpoint_recovery import verify_checkpoint_exchange
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    anchor = seed_checkpoint(store)
    request = store.request('recovery_check', 'paged-check', dict(expected_version=0), ADMIN, 'check')
    claimed = store.claim(owner)
    snapshot = store.snapshot()
    calls = []
    stamp = str(int((anchor + timedelta(minutes=10)).timestamp() * 1000))
    connector = OkxExchange(okx_api_key='test', okx_secret_key='test', okx_passphrase='test', trading_required=False)

    async def venue(*, path_url, params=None, **kwargs):
        params = params or {}
        calls.append(path_url)
        if path_url.endswith('/config'):
            data = [dict(uid='42')]
        elif path_url.endswith(('/orders-pending', '/positions')):
            data = []
        elif path_url.endswith('/fills'):
            last = int(params.get('after', '2002')) - 2
            data = [dict(ordId=str(i), clOrdId=f'o{i:05}', tradeId=str(i), billId=str(i + 1),
                         ts=stamp, fillTime=stamp, fillSz='1', fillPx='100', feeCcy='USDT', fee='-.1',
                         instId='BTC-USDT', side='buy') for i in range(last, max(0, last - 100), -1)]
        else:
            pytest.fail('Historical per-order REST is forbidden for the checkpoint route')
        return dict(code='0', data=data)

    connector._api_get = AsyncMock(side_effect=venue)
    reader = RecoveryReader(connector)

    async def lookup(keys, watermark):
        return await asyncio.to_thread(store.checkpoint_fill_rows, keys, watermark)

    try:
        proof = await verify_checkpoint_exchange(snapshot, {'okx': reader}, lookup)
        assert proof['verified'] and proof['fills_checked'] == 2000 and proof['orders_checked'] == 0
        assert calls.count('/api/v5/trade/fills') == 42  # Two complete scans, including empty final pages.
        assert owner.check_owner()['recovery_state']['halt']
        store.finish_check(owner, claimed, snapshot, [], proof, blockers=[])
        check = store.get(request['id'])
        reset = store.request('recovery_reset', 'paged-reset', dict(expected_version=0, check_id=check['id'],
                              confirmation_token=check['result']['confirmation_token']), ADMIN, 'reset')
        claimed = store.claim(owner)
        current = store.snapshot()
        fresh_proof = await verify_checkpoint_exchange(current, {'okx': reader}, lookup)
        assert store.finish_reset(owner, claimed, current, [], fresh_proof)
        after = store.snapshot()
        assert after['positions'] == snapshot['positions'] and after['ledger_hash'] == snapshot['ledger_hash']
        assert after['checkpoint']['anchor']['operation_id'] == reset['id']
        assert after['checkpoint']['expected_fills'] == 0
        assert owner.check_owner()['desired_state'] == 'stopped'
        assert not owner.check_owner()['recovery_state'].get('halt')
    finally:
        await reader.close()
