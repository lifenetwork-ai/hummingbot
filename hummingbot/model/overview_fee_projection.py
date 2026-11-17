"""Durable, replayable reporting contributions; independent of financial commits."""
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext

from sqlalchemy import Column, MetaData, Text, select, text, update
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import declarative_base

from hummingbot.model import HummingbotBase
from hummingbot.model.decimal_type_decorator import SqliteDecimal

BATCH_SIZE = 500
VERSION = 1
# Optional reporting schema must not become a prerequisite of the financial
# startup validator while an old owner defers the additive migration.
ReportingBase = declarative_base(metadata=MetaData(naming_convention=HummingbotBase.metadata.naming_convention))


class OverviewFeeScope(ReportingBase):
    __tablename__ = 'overview_fee_scopes'
    scope_key = Column(Text, primary_key=True, nullable=False)
    payload = Column(JSONB, nullable=False)


class OverviewFeeContribution(ReportingBase):
    __tablename__ = 'overview_fee_contributions'
    scope_key = Column(Text, primary_key=True, nullable=False)
    source_market = Column(Text, primary_key=True, nullable=False)
    order_id = Column(Text, primary_key=True, nullable=False)
    exchange_trade_id = Column(Text, primary_key=True, nullable=False)
    market = Column(Text, nullable=False)
    quality = Column(Text, nullable=False)
    fee_usdt = Column(SqliteDecimal(6))


ATTEMPT_BATCH = text("""
    SELECT order_id, context->'bindings' AS bindings FROM submission_attempts
    WHERE context->>'bot_id' = :bot_id AND context->'session'->>0 = :session_id
      AND order_id >= :cursor_order AND (order_id <> :cursor_order OR :include_cursor)
    ORDER BY order_id LIMIT :batch_limit
""")
SOURCE_BATCH = text("""
    /* overview_fee_source_batch */
    SELECT f.order_id, f.exchange_trade_id, f.timestamp, f.market, f.base_asset,
           f.quote_asset, f.trade_fee, f.trade_fee_in_quote
    FROM trade_fills f
    WHERE f.order_id = ANY(CAST(:orders AS TEXT[]))
      AND (f.order_id, f.market, f.exchange_trade_id) > (:cursor_order, :cursor_market, :cursor_trade)
    ORDER BY f.order_id, f.market, f.exchange_trade_id LIMIT :batch_limit
""")


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _add_exact(left, right):
    with localcontext() as context:
        context.prec = max(left.adjusted(), right.adjusted(), 0) - min(
            left.as_tuple().exponent, right.as_tuple().exponent, 0) + 3
        return left + right


def _view(payload):
    converted = payload.get('converted', 0)
    reason = ('session_fee_conversion_incomplete' if payload.get('unconverted') else
              'session_fee_binding_incomplete' if payload.get('unattributed') else 'session_fee_history_incomplete')
    if converted == 0:
        return dict(value=None, observed_at=payload.get('observed_at'), stale=False,
                    unavailable_reason='session_fees_catching_up' if payload.get('processing_state') == 'catching_up' else
                    'session_fee_conversion_unavailable' if payload.get('unconverted') else
                    'session_fee_binding_unavailable' if payload.get('unattributed') else 'session_fee_history_unavailable')
    return dict(value=dict(total_usdt=Decimal(payload['total']),
                           by_market=[dict(market=name, total_usdt=Decimal(amount))
                                      for name, amount in sorted(payload['by_market'].items())],
                           conversion_basis='at_fill', history_complete=False,
                           through_at=payload['through_at'], processing_state=payload['processing_state'],
                           processed_fill_count=payload['processed'], caught_up_at=payload.get('caught_up_at'),
                           reported_projection_version=payload['identity']['version'],
                           processing_projection_version=payload.get('processing_version', payload['identity']['version']),
                           backfill_processed_fill_count=payload.get('backfill_processed')),
                observed_at=payload['observed_at'], stale=False, unavailable_reason=reason)


def project_session_fees(engine, **scope):
    """One bounded batch and atomic progress update. A fresh sweep finds late keys.

    Scope identity excludes the moving window end. No trade-time/sequence watermark
    is used as proof that earlier transactions committed. Every subsequent sweep
    revisits source identities and uniqueness prevents double application.
    """
    from hummingbot.model.overview_fees import fee_in_usdt

    scope = {**scope, 'account_ids': sorted(set(scope['account_ids']))}
    identity = {key: value for key, value in scope.items() if key != 'end_ms'}
    identity['version'] = VERSION
    key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    publication_identity = {name: value for name, value in identity.items() if name != 'version'}
    publication_key = 'published:' + hashlib.sha256(json.dumps(publication_identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    with engine.begin() as connection:
        connection.exec_driver_sql("SET LOCAL statement_timeout = '1500ms'")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '250ms'")
        connection.exec_driver_sql('SET LOCAL jit = off')
        connection.exec_driver_sql('SET LOCAL max_parallel_workers_per_gather = 0')
        now = connection.execute(text('SELECT clock_timestamp()')).scalar_one()
        stamp = _iso(now)
        initial = dict(identity=identity, cursor=None, total='0', by_market={}, processed=0, converted=0,
                       unconverted=0, unattributed=0, processing_state='catching_up', observed_at=stamp,
                       through_at=_iso(datetime.fromtimestamp(scope['start_ms'] / 1000, timezone.utc)))
        connection.execute(insert(OverviewFeeScope).values(scope_key=key, payload=initial).on_conflict_do_nothing())
        payload = dict(connection.execute(select(OverviewFeeScope.payload).where(
            OverviewFeeScope.scope_key == key).with_for_update()).scalar_one())
        published = connection.execute(select(OverviewFeeScope.payload).where(
            OverviewFeeScope.scope_key == publication_key)).scalar_one_or_none()
        if published and published['identity']['version'] > VERSION:
            return _view(published)  # An overlapping older reader cannot downgrade publication.
        before_processed = payload['processed']
        cursor = payload.get('cursor') or ['', '', '']
        end_ms = payload['sweep_end_ms'] if payload.get('cursor') else scope['end_ms']
        candidates = connection.execute(ATTEMPT_BATCH, dict(bot_id=scope['bot_id'], session_id=scope['session_id'],
                                                            cursor_order=cursor[0], include_cursor=bool(cursor[1]), batch_limit=BATCH_SIZE + 1)).mappings().all()
        contexts = {item['order_id']: item['bindings'] for item in candidates[:BATCH_SIZE]}
        rows = connection.execute(SOURCE_BATCH, dict(orders=list(contexts),
                                                     cursor_order=cursor[0], cursor_market=cursor[1] or '', cursor_trade=cursor[2] or '',
                                                     batch_limit=BATCH_SIZE + 1)).mappings().all() if contexts else []
        more_fills = len(rows) > BATCH_SIZE
        more = more_fills or len(candidates) > BATCH_SIZE
        accepted = []
        for row in rows[:BATCH_SIZE]:
            if not scope['start_ms'] <= row['timestamp'] < end_ms:
                continue
            bindings = contexts[row['order_id']]
            bound = isinstance(bindings, list) and any(isinstance(binding, dict)
                                                       and binding.get('connector') == row['market'] and binding.get('account_id') in scope['account_ids']
                                                       and binding.get('environment') == scope['environment'] for binding in bindings)
            fee, safe = fee_in_usdt(row) if bound else (None, False)
            market = {'okx': 'spot', 'okx_perpetual': 'perpetual'}.get(row['market'])
            quality = 'converted' if bound and safe and market else 'unconverted' if bound else 'unattributed'
            accepted.append(dict(scope_key=key, source_market=row['market'], order_id=row['order_id'], exchange_trade_id=row['exchange_trade_id'],
                                 market=market or row['market'], quality=quality,
                                 fee_usdt=fee if quality == 'converted' else None))
        if accepted:
            applied = connection.execute(insert(OverviewFeeContribution).values(accepted).on_conflict_do_nothing().returning(
                OverviewFeeContribution.market, OverviewFeeContribution.quality, OverviewFeeContribution.fee_usdt)).all()
            markets = dict(payload['by_market'])
            total = Decimal(payload['total'])
            for market, quality, fee in applied:
                payload['processed'] += 1
                payload[quality] += 1
                if quality == 'converted':
                    total = _add_exact(total, fee)
                    markets[market] = format(_add_exact(Decimal(markets.get(market, '0')), fee), 'f')
            payload.update(total=format(total, 'f'), by_market=markets)
        next_cursor = ([rows[BATCH_SIZE - 1]['order_id'], rows[BATCH_SIZE - 1]['market'], rows[BATCH_SIZE - 1]['exchange_trade_id']]
                       if more_fills else [candidates[BATCH_SIZE - 1]['order_id'], None, None] if more else None)
        payload.update(cursor=next_cursor,
                       sweep_end_ms=end_ms, processing_state='catching_up' if more else 'ready', observed_at=stamp)
        if not more:
            if payload['processed'] != before_processed or payload.get('completed_end_ms') != end_ms or not payload.get('caught_up_at'):
                payload['caught_up_at'] = stamp
            payload.update(completed_end_ms=end_ms, through_at=_iso(datetime.fromtimestamp(end_ms / 1000, timezone.utc)))
        connection.execute(update(OverviewFeeScope).where(OverviewFeeScope.scope_key == key).values(payload=payload))
        rebuilding = published and published['identity']['version'] < VERSION
        if rebuilding and (more or payload['processed'] < published['processed']):
            result = _view({**published, 'processing_state': 'catching_up', 'processing_version': VERSION,
                            'backfill_processed': payload['processed']})
            result['stale'] = (now - datetime.fromisoformat(published['observed_at'].replace('Z', '+00:00'))).total_seconds() > 15
            result['unavailable_reason'] = 'session_fee_projection_rebuilding'
            if not more:
                result.update(stale=True, unavailable_reason='session_fee_rebuild_incomplete')
        else:
            if not more:
                publication = connection.execute(insert(OverviewFeeScope).values(scope_key=publication_key, payload=payload)
                                                 .on_conflict_do_update(index_elements=[OverviewFeeScope.scope_key], set_={'payload': payload},
                                                                        where=OverviewFeeScope.payload['identity']['version'].as_integer() <= VERSION)
                                                 .returning(OverviewFeeScope.payload)).scalar_one_or_none()
                if publication is None:
                    payload = connection.execute(select(OverviewFeeScope.payload).where(
                        OverviewFeeScope.scope_key == publication_key)).scalar_one()
            result = _view(payload)
    return result
