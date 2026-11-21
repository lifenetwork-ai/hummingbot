"""Review zero-fill terminal metadata without replaying lifetime history."""
from copy import deepcopy
from decimal import Decimal
from test.integration.postgres.test_checkpoint_recovery import publish_heartbeat, seed_checkpoint
from test.integration.postgres.test_recovery_store import ADMIN, recovering as _recovering
from types import SimpleNamespace

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from hummingbot.core.data_type.common import OrderType, PositionAction, TradeType
from hummingbot.core.data_type.in_flight_order import InFlightOrder, OrderState
from hummingbot.core.recovery import RecoveryBlocked
from hummingbot.core.recovery_terminal import verify_terminal_orders
from hummingbot.core.segmented_recovery import verify_segment
from hummingbot.model.admin_access import AdminAuditEvent
from hummingbot.model.bot_state import BotState
from hummingbot.model.bot_state_store import ControlConflict
from hummingbot.model.exchange_credential import ExchangeCredential
from hummingbot.model.market_state import MarketState
from hummingbot.model.order import Order
from hummingbot.model.order_status import OrderStatus
from hummingbot.model.position import Position
from hummingbot.model.runtime_journal import (
    RuntimeCheckpoint,
    SubmissionAttempt,
    binding_hash,
    bindings,
    credential_fingerprint,
    financial_hash,
)

recovering = _recovering


def credential(api_key='offline', name='okx'):
    return SimpleNamespace(name=name, authenticator=SimpleNamespace(api_key=api_key))


def seed_terminal_work(store, count=5, *, fills=0, existing_binding=False, api_key='offline', perpetual=0):
    seed_checkpoint(store, orders=5002 + count, fills=fills, existing_binding=existing_binding)
    states, identities = {}, []
    with store.engine.begin() as db:
        if perpetual:
            from datetime import datetime, timezone
            db.execute(ExchangeCredential.__table__.insert().values(bot_id=store.bot_id, exchange='okx', connector='okx_perpetual',
                       environment='demo', version=1, account_id='www:42', state='active', envelope={'test': True},
                       changed_by='test', changed_at=datetime.now(timezone.utc)))
            for side, amount in [('BUY', '.02'), ('SELL', '.03')]:
                db.execute(Position.__table__.insert().values(id='perpetual-' + side, controller_id='perpetual',
                                                              connector_name='okx_perpetual', side=side, trading_pair='BTC-USDT', timestamp=1700000000000,
                                                              amount=amount, volume_traded_quote=Decimal(amount) * 100, breakeven_price='100.2',
                                                              realized_pnl_quote='.3', unrealized_pnl_quote='-.1', cum_fees_quote='.01'))
        with Session(bind=db) as session:
            bound = bindings(session, store.bot_id)
        for index in range(count):
            identity = f'o{5002 + index:05}'
            venue = str(5002 + index)
            name = 'okx_perpetual' if index >= count - perpetual else 'okx'
            side = TradeType.SELL if name == 'okx_perpetual' else TradeType.BUY
            position = PositionAction.CLOSE if name == 'okx_perpetual' else PositionAction.NIL
            amount = Decimal('.0002') if name == 'okx_perpetual' else Decimal('1')
            created = db.scalar(select(Order.creation_timestamp).where(Order.id == identity))
            tracked = InFlightOrder(client_order_id=identity, exchange_order_id=venue,
                                    trading_pair='BTC-USDT', order_type=OrderType.LIMIT_MAKER,
                                    trade_type=side, position=position, amount=amount, price=Decimal('100'),
                                    creation_timestamp=created / 1000, initial_state=OrderState.OPEN)
            original = tracked.to_json()
            original['exchange_order_id'] = None
            original['last_state'] = str(OrderState.PENDING_CREATE.value)
            context = dict(bot_id=store.bot_id, config_file_path='life_mm_profiles.yaml', cleanup=False,
                           environment=dict(mode='demo', site='www'), bindings=bound,
                           credential_fingerprint=credential_fingerprint(credential(api_key, name)), session=['old-session', 0], order=original,
                           executor=dict(id='exec-' + identity, type='order_executor' if name == 'okx_perpetual' else 'position_executor', connector_name=name,
                                         controller_id='spot', mm_session_id='old-session', mm_generation=0,
                                         side=side.value, position_action=position.name, execution_strategy='LIMIT_MAKER',
                                         level_id='close-long', triple_barrier_config={}))
            db.execute(update(Order).where(Order.id == identity).values(
                market=name, position=position.name, amount=amount, last_status='BuyOrderCreated' if side == TradeType.BUY else 'SellOrderCreated'))
            db.execute(SubmissionAttempt.__table__.insert().values(
                order_id=identity, state='acknowledged', context=context,
                receipt=dict(code='0', http_status=200, order=dict(ordId=venue, clOrdId=identity, sCode='0'))))
            states.setdefault(name, {})[identity] = tracked.to_json()
            identities.append(identity)
        for name, saved in states.items():
            db.execute(MarketState.__table__.insert().values(config_file_path='life_mm_profiles.yaml', market=name,
                                                             timestamp=1700000000000, saved_state=saved))
        with Session(bind=db) as session:
            checkpoint = session.get(RuntimeCheckpoint, 'life_mm_profiles.yaml')
            checkpoint.payload = {**checkpoint.payload, 'bindings': bound,
                                  'credential_fingerprints': {name: credential_fingerprint(credential(api_key, name)) for name in states}}
            checkpoint.bindings_hash = binding_hash(session, store.bot_id)
            checkpoint.financial_hash = financial_hash(session)
            session.flush()
    return identities


def test_acknowledged_zero_fill_work_keeps_bounded_checkpoint_route(recovering, monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    identities = seed_terminal_work(store)
    before = deepcopy(owner.check_owner()['recovery_state'])
    snapshot = store.snapshot()
    assert snapshot.get('mode') == 'verified_checkpoint'
    assert snapshot['orders'] == snapshot['fills'] == [] and not snapshot['truncated']
    assert [row['id'] for row in snapshot['terminal_orders']] == identities
    with store.engine.connect() as db:
        assert set(db.execute(select(SubmissionAttempt.state)).scalars()) == {'acknowledged'}
        assert set(db.execute(select(Order.last_status).where(Order.id.in_(identities))).scalars()) == {'BuyOrderCreated'}
    assert owner.check_owner()['recovery_state'] == before


class TerminalVenue:
    def __init__(self, snapshot, name='okx'):
        from hummingbot.connector.exchange.okx.okx_exchange import OkxExchange
        self.name = name
        if name == 'okx':
            self.connector = OkxExchange(okx_api_key='offline', okx_secret_key='offline', okx_passphrase='offline', trading_required=False)
        else:
            from hummingbot.connector.derivative.okx_perpetual.okx_perpetual_derivative import OkxPerpetualDerivative
            self.connector = OkxPerpetualDerivative(okx_perpetual_api_key='offline', okx_perpetual_secret_key='offline',
                                                    okx_perpetual_passphrase='offline', trading_required=False)
            self.connector._contract_sizes = {'BTC-USDT': Decimal('.01')}
        self.snapshot, self.fault = snapshot, None
        self.calls = []

    async def _api_get(self, *, path_url, params=None, **kwargs):
        self.calls.append((path_url, params))
        if path_url.endswith('/config'):
            data = [dict(uid='42', acctLv='2', posMode='long_short_mode')]
        elif path_url.endswith('/fills') and 'ordId' not in (params or {}):
            expected = self.snapshot['checkpoint']['expected_fills'] if self.name == 'okx' else 0
            high = min(expected, int((params or {}).get('after', str(expected + 2))) - 2)
            stamp = str(self.snapshot['terminal_orders'][0]['creation_timestamp'])
            data = [dict(ordId=str(i), clOrdId=f'o{i:05}', tradeId=str(i), billId=str(i + 1),
                         ts=stamp, fillTime=stamp, fillSz='1', fillPx='100', feeCcy='USDT', fee='-.1',
                         instId='BTC-USDT', side='buy') for i in range(high, max(0, high - int((params or {}).get('limit', 100))), -1)]
        elif path_url.endswith('/positions') and self.name == 'okx_perpetual':
            data = [dict(instId=row['trading_pair'] + '-SWAP', pos=str(Decimal(row['amount']) / Decimal('.01')),
                         posSide='long' if row['side'] == 'BUY' else 'short', mgnMode='cross')
                    for row in self.snapshot['positions'] if row['connector_name'] == self.name]
        elif path_url.endswith(('/orders-pending', '/positions', '/fills')):
            data = []
        elif path_url.endswith('/order'):
            row = next(r for r in self.snapshot['terminal_orders'] if r['id'] == params['clOrdId'])
            actual = dict(clOrdId=row['id'], ordId=row['exchange_order_id'], instId=row['pair'] + ('-SWAP' if self.name != 'okx' else ''),
                          side=row['side'].lower(), ordType='post_only', state='canceled', tdMode='cash' if self.name == 'okx' else 'cross',
                          posSide='long', sz=row['amount'] if self.name == 'okx' else str(Decimal(row['amount']) / Decimal('.01')),
                          px=row['price'], accFillSz='0', uTime=str(row['last_update_timestamp'] + 1))
            if self.fault:
                self.fault(actual)
            data = [actual]
        else:
            raise AssertionError(path_url)
        return dict(code='0', data=data)

    async def _initialize_trading_pair_symbol_map(self):
        pass

    def _format_size_to_amount(self, pair, size):
        return self.connector._format_size_to_amount(pair, size)


async def proof_for(store, owner, row, snapshot, venue):
    readers = venue if isinstance(venue, dict) else {'okx': venue}

    async def lookup(keys, watermark):
        return store.checkpoint_fill_rows(keys, watermark)

    async def save(before, after, entries):
        store.save_proof_progress(owner, row, snapshot, before, after, entries)

    proof = await verify_segment(snapshot, readers, lookup, None, save)
    assert proof and proof['verified']
    changes = await verify_terminal_orders(snapshot, readers)
    completed = store.get(row['id'])['result']['_private']['proof']
    proof = await verify_segment(snapshot, readers, lookup, completed, save)
    proof['terminal_order_changes'] = changes
    proof['orders_checked'] = len(proof['terminal_order_changes'])
    return proof


@pytest.mark.asyncio
@pytest.mark.parametrize('perpetual', [0, 2])
async def test_review_then_fresh_reset_repairs_only_terminal_metadata(recovering, monkeypatch, perpetual):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    identities = seed_terminal_work(store, fills=3, perpetual=perpetual)
    publish_heartbeat(store, owner)
    store.request('recovery_check', 'check', dict(expected_version=0), ADMIN, 'check')
    row, snapshot = store.claim(owner), store.snapshot()
    venue = {name: TerminalVenue(snapshot, name) for name in ('okx', 'okx_perpetual')[:2 if perpetual else 1]}
    before_receipts = {}
    with store.engine.connect() as db:
        before_receipts = {r['order_id']: dict(r) for r in db.execute(select(SubmissionAttempt)).mappings()}
    proof = await proof_for(store, owner, row, snapshot, venue)
    store.finish_check(owner, row, snapshot, [], proof, blockers=[])
    check = store.get(row['id'])
    assert check['result']['proposed_changes'] == []
    assert len(check['result']['proposed_order_changes']) == 5 and check['result']['can_recover']
    assert store.snapshot()['ledger_hash'] == snapshot['ledger_hash']
    assert owner.check_owner()['recovery_state']['halt']
    reset_payload = dict(expected_version=0, check_id=row['id'], confirmation_token=check['result']['confirmation_token'])
    publish_heartbeat(store, owner)
    with pytest.raises(ControlConflict, match='recovery_order_review_required'):
        store.request('recovery_reset', 'unreviewed', reset_payload, ADMIN, 'unreviewed')
    publish_heartbeat(store, owner)
    store.request('recovery_reset', 'reset', {**reset_payload, 'confirm_order_changes': True}, ADMIN, 'reset')
    row = store.claim(owner)
    fresh = store.snapshot()
    proof = await proof_for(store, owner, row, fresh, venue)
    assert store.finish_reset(owner, row, fresh, [], proof)
    result = store.get(row['id'])['result']
    assert result['repaired_positions'] == [] and len(result['repaired_orders']) == 5
    after = store.snapshot()
    assert after['mode'] == 'verified_checkpoint' and after['terminal_orders'] == []
    assert after['positions'] == snapshot['positions'] and after['checkpoint']['financial_hash'] == snapshot['checkpoint']['financial_hash']
    assert after['checkpoint']['payload']['reduction_basis'] == snapshot['checkpoint']['payload']['reduction_basis']
    with store.engine.connect() as db:
        for saved in db.execute(select(SubmissionAttempt)).mappings():
            assert dict(saved) == {**before_receipts[saved['order_id']], 'state': 'terminal'}
        assert set(db.execute(select(Order.last_status).where(Order.id.in_(identities))).scalars()) == {'OrderCancelled'}
        assert all(saved == {} for saved in db.execute(select(MarketState.saved_state)).scalars())
        audits = db.execute(select(AdminAuditEvent).where(AdminAuditEvent.action == 'recovery_order_repaired')).mappings().all()
        assert len(audits) == 5 and {r['operation_id'] for r in audits} == {row['id']}
    state = owner.check_owner()
    assert state['desired_state'] == 'stopped' and not state['recovery_state'].get('halt')


@pytest.mark.parametrize('mutation', ['pending', 'possibly_sent', 'uncertain', 'prepared', 'missing_receipt',
                         'wrong_receipt', 'context', 'credential', 'binding', 'tracker_fill', 'orphan_tracker',
                                      'duplicate_tracker', 'malformed_tracker', 'missing_tracker', 'amount', 'terminal_history'])
def test_unsupported_work_cannot_enter_terminal_checkpoint_route(recovering, monkeypatch, mutation):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    identity = seed_terminal_work(store, count=1)[0]
    with store.engine.begin() as db:
        if mutation == 'pending':
            db.execute(update(Order).where(Order.id == identity).values(creation_pending=True))
        elif mutation in ('possibly_sent', 'uncertain', 'prepared'):
            db.execute(update(SubmissionAttempt).values(state=mutation))
        elif mutation in ('missing_receipt', 'wrong_receipt'):
            db.execute(update(SubmissionAttempt).values(receipt=None if mutation == 'missing_receipt' else
                       dict(code='0', http_status=200, order=dict(sCode='0', ordId='foreign', clOrdId=identity))))
        elif mutation in ('context', 'credential', 'binding'):
            context = deepcopy(db.scalar(select(SubmissionAttempt.context)))
            if mutation == 'context':
                context['config_file_path'] = 'foreign.yml'
            elif mutation == 'credential':
                context['credential_fingerprint'] = 'changed'
            else:
                context['bindings'][0]['version'] += 1
            db.execute(update(SubmissionAttempt).values(context=context))
        elif mutation == 'amount':
            db.execute(update(Order).where(Order.id == identity).values(amount=2))
        elif mutation == 'terminal_history':
            db.execute(OrderStatus.__table__.insert().values(order_id=identity, timestamp=1700000000000, status='OrderFilled'))
        else:
            saved = deepcopy(db.scalar(select(MarketState.saved_state)))
            if mutation == 'tracker_fill':
                saved[identity]['executed_amount_base'] = '.1'
            elif mutation == 'orphan_tracker':
                raw = deepcopy(saved[identity]); raw['client_order_id'] = 'foreign'
                saved['foreign'] = raw
            elif mutation == 'duplicate_tracker':
                db.execute(MarketState.__table__.insert().values(config_file_path='foreign.yml', market='okx',
                                                                 timestamp=1700000000000, saved_state=saved))
            elif mutation == 'malformed_tracker':
                saved[identity]['last_state'] = 'invalid'
            else:
                saved = {}
            db.execute(update(MarketState).where(MarketState.config_file_path == 'life_mm_profiles.yaml').values(saved_state=saved))
    assert store.snapshot().get('mode') != 'verified_checkpoint'
    assert owner.check_owner()['recovery_state']['halt']


@pytest.mark.parametrize('count,supported', [(64, True), (65, False)])
def test_candidate_budget_is_bounded(recovering, monkeypatch, count, supported):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, _ = recovering
    seed_terminal_work(store, count=count)
    snapshot = store.snapshot()
    assert (snapshot.get('mode') == 'verified_checkpoint') is supported
    if supported:
        assert len(snapshot['terminal_orders']) == count


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['receipt', 'tracker', 'risk', 'control', 'reviewed_plan'])
async def test_changes_after_reset_acceptance_cannot_escape_final_fence(recovering, monkeypatch, mutation):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    identities = seed_terminal_work(store, count=2)
    store.request('recovery_check', 'check', dict(expected_version=0), ADMIN, 'check')
    row, snapshot = store.claim(owner), store.snapshot()
    venue = TerminalVenue(snapshot)
    proof = await proof_for(store, owner, row, snapshot, venue)
    store.finish_check(owner, row, snapshot, [], proof, blockers=[])
    check = store.get(row['id'])
    store.request('recovery_reset', 'reset', dict(expected_version=0, check_id=row['id'],
                  confirmation_token=check['result']['confirmation_token'], confirm_order_changes=True), ADMIN, 'reset')
    row, snapshot = store.claim(owner), store.snapshot()
    proof = await proof_for(store, owner, row, snapshot, venue)
    if mutation == 'reviewed_plan':
        proof['terminal_order_changes'][0]['terminal_timestamp'] += 1
    else:
        with store.engine.begin() as db:
            if mutation == 'receipt':
                db.execute(update(SubmissionAttempt).where(SubmissionAttempt.order_id == identities[0]).values(receipt={}))
            elif mutation == 'tracker':
                db.execute(update(MarketState).values(saved_state={}))
            elif mutation == 'control':
                db.execute(update(BotState).values(control_version=1))
            else:
                state = owner.check_owner()
                db.execute(update(BotState).values(recovery_state={**state['recovery_state'], 'unreviewed_risk': True}))
    with pytest.raises((ControlConflict, RecoveryBlocked)):
        store.finish_reset(owner, row, snapshot, [], proof)
    with store.engine.connect() as db:
        assert set(db.execute(select(SubmissionAttempt.state)).scalars()) == {'acknowledged'}
        assert set(db.execute(select(Order.last_status).where(Order.id.in_(identities))).scalars()) == {'BuyOrderCreated'}
        assert not db.execute(select(AdminAuditEvent.id).where(AdminAuditEvent.action == 'recovery_order_repaired')).first()
    assert owner.check_owner()['recovery_state']['halt']


@pytest.mark.asyncio
async def test_late_failure_rolls_back_all_order_repairs_and_audits(recovering, monkeypatch):
    monkeypatch.setenv('OKX_TRADING_ENV', 'demo')
    monkeypatch.setenv('OKX_SITE', 'www')
    store, owner = recovering
    identities = seed_terminal_work(store, count=2)
    store.request('recovery_check', 'check', dict(expected_version=0), ADMIN, 'check')
    row, snapshot = store.claim(owner), store.snapshot()
    venue = TerminalVenue(snapshot)
    proof = await proof_for(store, owner, row, snapshot, venue)
    store.finish_check(owner, row, snapshot, [], proof, blockers=[])
    check = store.get(row['id'])
    store.request('recovery_reset', 'reset', dict(expected_version=0, check_id=row['id'],
                  confirmation_token=check['result']['confirmation_token'], confirm_order_changes=True), ADMIN, 'reset')
    row, snapshot = store.claim(owner), store.snapshot()
    proof = await proof_for(store, owner, row, snapshot, venue)
    original = store.access.audit

    def fail_second(connection, **kwargs):
        original(connection, **kwargs)
        if kwargs.get('action') == 'recovery_order_repaired' and kwargs.get('target_id') == identities[1]:
            raise RuntimeError('Injected failure after all metadata writes and audit inserts')

    monkeypatch.setattr(store.access, 'audit', fail_second)
    with pytest.raises(RuntimeError, match='Injected failure'):
        store.finish_reset(owner, row, snapshot, [], proof)
    assert store.snapshot()['ledger_hash'] == snapshot['ledger_hash']
    assert owner.check_owner()['recovery_state']['halt']
    with store.engine.connect() as db:
        assert not db.execute(select(AdminAuditEvent.id).where(AdminAuditEvent.action == 'recovery_order_repaired')).first()
