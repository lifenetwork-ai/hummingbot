"""Exact durable order identities cannot become broad outage authority."""
import json
from dataclasses import replace
from datetime import datetime, timezone
from test.hummingbot.strategy_v2.life_liquidity.storage.test_generation_access import broker_fixture, snapshot
from uuid import uuid4

import pytest

from hummingbot.core.web_assistant.connections.data_types import RESTMethod
from hummingbot.strategy_v2.life_liquidity.storage.cancellation_manifest import (
    CancellationIdentity,
    CancellationManifest,
)
from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
from hummingbot.strategy_v2.life_liquidity.storage.generation_access import GenerationClient


def manifest_fixture(product='SPOT'):
    cfg, broker, client, identity, credentials, cert = broker_fixture()
    owner = identity.registration.identity.owner
    row = CancellationIdentity('intent1', product, 'LIFE-USDT' if product == 'SPOT' else 'LIFE-USDT-SWAP', 'wire1', '123456',
                               owner.process_id, owner.generation, owner.session_id, owner.epoch)
    manifest = CancellationManifest(cfg.binding, cfg.database_lineage, cfg.account_id, 1, 'a' * 64, datetime.now(timezone.utc), (row,))
    return cfg, broker, client, identity, row, manifest


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
async def test_outage_cancel_only_exact_pinned_order_and_no_local_db_ready_required(product):
    cfg, broker, client, identity, row, manifest = manifest_fixture(product)
    broker.enable(identity.registration, mode='RECOVERY_ONLY', expires_at=identity.registration.identity.owner.expires_at)
    broker.pin_manifest(identity.registration, manifest)
    client = GenerationClient(client.identity, client.rpc, public_key=client.public_key, local_ready=lambda: False, timeout_ms=client.timeout_ms)
    request = snapshot('/api/v5/trade/cancel-order', RESTMethod.POST, dict(instId=row.instrument, clOrdId=row.wire_id, ordId=row.exchange_order_id))
    guard = client.request_guard(financial_guard=False)
    assert await guard.authorize(request) and guard.check(request)
    for change in ({'ordId': '999'}, {'clOrdId': 'successor'}, {'instId': 'BTC-USDT'}, {'ordId': None}):
        body = dict(instId=row.instrument, clOrdId=row.wire_id, ordId=row.exchange_order_id)
        body.update(change)
        with pytest.raises(StorageError):
            await client.request_guard(financial_guard=False).authorize(snapshot('/api/v5/trade/cancel-order', RESTMethod.POST, body))


@pytest.mark.parametrize('fault', ['missing_id', 'duplicate_order', 'duplicate_wire', 'foreign_generation', 'foreign_database'])
def test_ambiguous_or_foreign_manifest_cannot_be_pinned(fault):
    cfg, broker, client, identity, row, manifest = manifest_fixture()
    with pytest.raises(StorageError):
        if fault == 'missing_id':
            manifest = replace(manifest, identities=(replace(row, exchange_order_id=''),))
        elif fault == 'duplicate_order':
            manifest = replace(manifest, identities=(row, replace(row, intent_id='another', wire_id='wire2')))
        elif fault == 'duplicate_wire':
            manifest = replace(manifest, identities=(row, replace(row, intent_id='another', exchange_order_id='99')))
        elif fault == 'foreign_generation':
            manifest = replace(manifest, identities=(replace(row, generation=uuid4()),))
        else:
            manifest = replace(manifest, database_lineage=uuid4())
        broker.pin_manifest(identity.registration, manifest)


def test_manifest_file_repeat_save_tamper_and_loss_are_fail_closed(tmp_path):
    cfg, broker, client, identity, row, manifest = manifest_fixture()
    path = tmp_path / 'cancel.json'
    manifest.save(path)
    manifest.save(path)
    assert CancellationManifest.load(path) == manifest
    data = json.loads(path.read_bytes())
    data['body']['identities'][0]['exchange_order_id'] = '999'
    path.write_text(json.dumps(data))
    with pytest.raises(StorageError):
        CancellationManifest.load(path)
    path.unlink()
    with pytest.raises(StorageError):
        CancellationManifest.load(path)
