"""Bounded immutable decoded provider response diagnostics, never authority.

Headers, credentials and arbitrary URLs are not accepted. The original signed
reader writes before parsing. Database rows are explicitly untrusted: a caller
can submit diagnostic JSON, never a financial fact or send/completeness grant.
"""
import json
from dataclasses import dataclass, field
from datetime import datetime

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, StorageUnavailable, identifier
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


@dataclass(frozen=True)
class ProviderPage:
    body: dict
    captured_at: datetime
    source_hash: str
    replayed: bool
    trading_permission: bool = field(default=False, init=False)


@dataclass(frozen=True)
class ProviderCollection:
    collection_id: str
    page_count: int
    body_bytes: int
    # Listing metadata is a cursor, never proof of complete verified history.
    trading_permission: bool = field(default=False, init=False)


class PgProviderJournal:
    def __init__(self, repository):
        if type(repository) is not PgSpotRepository:
            raise StorageError('PG_PROVIDER_JOURNAL_CONFIG_INVALID')
        self.repository, self.runtime = repository, repository._runtime

    @staticmethod
    def _receipt(value):
        if value.get('kind') != 'PROVIDER_DECODED_PAGE' or value.get('trading_permission') is not False:
            raise StorageUnavailable('PG_PROVIDER_JOURNAL_RECEIPT_INVALID')
        return ProviderPage(value['body'], datetime.fromisoformat(value['captured_at']), value['source_hash'], value['replayed'])

    async def capture(self, body):
        if not self.runtime.config.write:
            raise StorageError('PG_PROVIDER_JOURNAL_WRITE_REQUIRED')
        # Make a detached wire snapshot before the first await. SQL is the
        # structural authority too; direct function calls cannot bypass it.
        try:
            wire = json.dumps(body, ensure_ascii=False, allow_nan=False)
            if len(wire.encode('utf-8')) > 16777216:
                raise StorageError('PG_PROVIDER_JOURNAL_CAPTURE_BOUND')
            value = json.loads(wire)
        except (TypeError, ValueError):
            raise StorageError('PG_PROVIDER_JOURNAL_CAPTURE_INVALID') from None
        try:
            return await self.runtime._transaction('SELECT life_financial.spot_capture_provider_page($1,$2)',
                                                   (self.runtime.config.account_id, value), None, self._receipt,
                                                   observation=True, untrusted_ingress=True)
        except BaseException:
            self.runtime._healthy = False
            raise

    async def read_collection(self, collection_id, *, max_rows, max_bytes):
        import asyncpg
        identifier(collection_id)
        if (type(max_rows) is not int or not 0 < max_rows <= 512
                or type(max_bytes) is not int or not 0 < max_bytes <= 16777216):
            raise StorageError('PG_PROVIDER_JOURNAL_READ_BOUND_INVALID')

        async def read(c):
            try:
                rows = await c.fetchval('SELECT life_financial.spot_read_provider_collection($1,$2,$3,$4)',
                                        self.runtime.config.account_id, collection_id, max_rows, max_bytes)
            except asyncpg.RaiseError as error:
                raise sql_error(error) from None
            return tuple(self._receipt(row) for row in rows)
        return await self.runtime._admit(read, safety=True)

    async def collections(self, *, after=None, limit):
        """Bounded indexed cold discovery; verify each selected collection separately.

        Concurrent appends may sort before an existing cursor. A listing is not
        an account completeness statement or a reconciliation checkpoint.
        """
        if after is not None:
            identifier(after)
        if type(limit) is not int or not 0 < limit <= 512:
            raise StorageError('PG_PROVIDER_JOURNAL_READ_BOUND_INVALID')

        async def read(c):
            rows = await c.fetch('''SELECT collection_id,page_count,body_bytes FROM life_financial.spot_provider_heads
                WHERE account_id=$1 AND collection_id>$2 ORDER BY collection_id LIMIT $3''',
                                 self.runtime.config.account_id, '' if after is None else after, limit)
            return tuple(ProviderCollection(**dict(row)) for row in rows)
        return await self.runtime._admit(read, safety=True)
