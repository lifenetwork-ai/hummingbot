"""Account/source-scoped native recorder admission; never import by filename."""
from dataclasses import asdict

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


async def assert_recorder_scope(app):
    # Retained explicit predecessor-schema tests use their frozen old bundle.
    # Current runtime admission requires the complete current migration ledger.
    if not any(m.version == 118 for m in app.financial._runtime._bundle):
        return

    async def read(c):
        return await c.fetchval('SELECT life_financial.recorder_scope_verified($1,$2,$3)',
                                app.financial._runtime.config.account_id, app.repository.controller_id,
                                [asdict(r.source) for r in app.events])
    if not await app.financial._runtime._admit(read, safety=True, operation='recorder_scope'):
        raise StorageError('PG_RECORDER_IMPORT_REQUIRED')
