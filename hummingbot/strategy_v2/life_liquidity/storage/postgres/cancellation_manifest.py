"""One primary MVCC cut exports committed native ACK identities, never a hold."""
from uuid import UUID

from hummingbot.strategy_v2.life_liquidity.storage.cancellation_manifest import (
    CancellationIdentity,
    CancellationManifest,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, StorageUnavailable
from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot import PgSpotRepository


class PgCancellationManifestRepository:
    def __init__(self, financial, *, max_rows, max_bytes):
        if (type(financial) is not PgSpotRepository or type(max_rows) is not int or not 0 < max_rows <= 4096
                or type(max_bytes) is not int or not 0 < max_bytes <= 1048576):
            raise StorageError('PG_CANCEL_MANIFEST_CONFIG_INVALID')
        self.financial, self.runtime, self.max_rows, self.max_bytes = financial, financial._runtime, max_rows, max_bytes

    async def capture(self, *, source_owner):
        if type(source_owner) is not OwnerToken:
            raise StorageError('PG_CANCEL_MANIFEST_OWNER_INVALID')
        cfg = self.runtime.config

        async def read(c):
            async with c.transaction(isolation='repeatable_read', readonly=True):
                state = await c.fetchval('SELECT life_financial.spot_state($1)', cfg.account_id)
                if state['unverified']:
                    raise StorageUnavailable('PG_CANCEL_MANIFEST_UNVERIFIED')
                rows = await c.fetch('''SELECT t.*,CASE WHEN t.product='SPOT' THEN p.request->>'market' ELSE s.permit->>'instrument' END AS instrument
                    FROM life_financial.send_attempts t
                    LEFT JOIN life_financial.spot_preparations p USING(account_id,intent_id)
                    LEFT JOIN life_financial.swap_preparations s USING(account_id,intent_id)
                    WHERE t.account_id=$1 AND t.process_id=$2 AND t.generation=$3 AND t.session_id=$4 AND t.epoch=$5
                    AND t.exchange_order_id IS NOT NULL AND t.state NOT IN ('TERMINAL','ABORTED_BEFORE_SEND')
                    ORDER BY t.intent_id LIMIT $6''', cfg.account_id, source_owner.process_id, source_owner.generation,
                                     source_owner.session_id, source_owner.epoch, self.max_rows + 1)
                if len(rows) > self.max_rows:
                    raise StorageUnavailable('PG_CANCEL_MANIFEST_READ_BOUND')
                entries = tuple(CancellationIdentity(r['intent_id'], r['product'], r['instrument'], r['wire_id'], r['exchange_order_id'],
                                                     UUID(str(r['process_id'])), UUID(str(r['generation'])), UUID(str(r['session_id'])), r['epoch']) for r in rows)
                from datetime import datetime
                manifest = CancellationManifest(cfg.binding, cfg.database_lineage, cfg.account_id, state['account']['version'],
                                                state['digest'], datetime.fromisoformat(state['observed_at']), entries)
                from hummingbot.strategy_v2.life_liquidity.storage.contracts import encode
                if len(encode(manifest.json())) > self.max_bytes:
                    raise StorageUnavailable('PG_CANCEL_MANIFEST_READ_BOUND')
                return manifest
        return await self.runtime._admit(read, safety=True, operation='cancel_manifest_capture')
