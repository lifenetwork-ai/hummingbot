"""A replacement worker cannot depend on its predecessor's local directory."""
import shutil
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.service_process import (
    cold_service_context,
    crash_service,
)
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_service_swap_process import joint_money

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('product', ['SPOT', 'SWAP'])
async def test_deleted_worker_scratch_preserves_native_money_and_unknown_send(database, tmp_path, product):
    scratch = tmp_path / 'original-worker'
    scratch.mkdir()
    (scratch / 'disposable-status.json').write_text('{"ready":true}')
    manifest = await crash_service(database, 'send', product=product, scratch_dir=scratch)
    assert manifest['working_directory'] == str(scratch)
    context = cold_service_context(manifest)
    cfg = context.config
    before = await joint_money(database[0], cfg.account_id)
    attempts = await database[0].fetch('SELECT * FROM life_financial.send_attempts WHERE account_id=$1', cfg.account_id)
    assert len(attempts) == 1 and attempts[0]['state'] == 'SEND_UNKNOWN'
    shutil.rmtree(scratch)

    service = await context.reopen(False)
    try:
        assert not scratch.exists()
        assert not service._trading_ready()
        assert await joint_money(database[0], cfg.account_id) == before
        assert await database[0].fetch('SELECT * FROM life_financial.send_attempts WHERE account_id=$1', cfg.account_id) == attempts
        assert len(await service.source.capital.claims(cfg.binding)) == 1
        assert not service.runner.executor_orchestrator.active_executors['main']
        assert context.calls and all(call['method'] == 'GET' for call in context.calls)
    finally:
        await service.close()
    assert not scratch.exists()
    assert await joint_money(database[0], cfg.account_id) == before
