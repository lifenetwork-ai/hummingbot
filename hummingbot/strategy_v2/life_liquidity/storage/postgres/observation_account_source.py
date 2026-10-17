"""Signed observation-only identity when financial state cannot admit quotes.

The separate exact type is never accepted by the quote request guard. Private
GET collection uses the same pinned HMAC/UID transport verification, with current
primary owner checks. It neither resolves financial pending rows nor grants risk.
"""
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource
from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import sql_error


class PgObservationAccountSource(PgQuoteAccountSource):
    def __init__(self, journal, *, connector, max_uid_age_ms, max_auth_age_ms,
                 max_clock_skew_ms, max_pending, collection_timeout_ms):
        super().__init__(journal, connector=connector, admission_source=self._deny_admission,
                         max_uid_age_ms=max_uid_age_ms, max_auth_age_ms=max_auth_age_ms,
                         max_clock_skew_ms=max_clock_skew_ms, max_pending=max_pending,
                         collection_timeout_ms=collection_timeout_ms)
        self._original_repository, self._original_runtime = journal.repository, journal.repository._runtime
        self._original_binding, self._original_owner = journal.binding, journal.owner
        self._identity_checked = False

    @staticmethod
    async def _deny_admission():
        raise StorageError('PG_OBSERVATION_SOURCE_HAS_NO_SEND_AUTHORITY')

    @classmethod
    async def create(cls, journal, **kwargs):
        source = cls(journal, **kwargs)
        await source.validate_observation_identity()
        source._identity_checked = True
        return source

    def _context(self):
        self._credentials()
        if (self.journal.repository is not self._original_repository
                or self._original_repository._runtime is not self._original_runtime
                or self.journal.binding != self._original_binding or self.journal.owner != self._original_owner
                or self._original_repository.binding != self._original_binding or self._original_runtime._closing):
            raise StorageUnavailable('PG_OBSERVATION_SOURCE_CONTEXT_CHANGED')

    async def validate_observation_identity(self):
        import asyncpg
        self._context()

        async def read(c):
            try:
                row = await c.fetchrow('''SELECT life_financial.guard_spot_observation($1,$2,$3,a.version)
                    FROM life_financial.accounts a WHERE a.account_id=$1''', self._original_runtime.config.account_id,
                                       self._original_binding.json(), self._original_owner.json())
            except asyncpg.RaiseError as error:
                raise sql_error(error) from None
            if row is None:
                raise StorageUnavailable('PG_OBSERVATION_SOURCE_ACCOUNT_MISSING')
        await self._original_runtime._admit(read, safety=True)
        self._context()

    @property
    def age_limits(self):
        raise StorageError('PG_OBSERVATION_SOURCE_HAS_NO_SEND_AUTHORITY')

    def guard(self, **kwargs):
        raise StorageError('PG_OBSERVATION_SOURCE_HAS_NO_SEND_AUTHORITY')

    async def capture(self, request):
        raise StorageError('PG_OBSERVATION_SOURCE_HAS_NO_SEND_AUTHORITY')

    def check(self, request):
        return False
