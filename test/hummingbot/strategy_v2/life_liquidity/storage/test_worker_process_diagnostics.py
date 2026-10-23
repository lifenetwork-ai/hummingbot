import json
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres import worker_financial_process

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError


@pytest.mark.asyncio
@pytest.mark.parametrize('error,kind,code', [
    (StorageError('PG_SOURCE_EXPIRED'), 'StorageError', 'PG_SOURCE_EXPIRED'),
    (TimeoutError('private-test-password'), 'TimeoutError', None),
    (StorageError('postgresql://private-test-password'), 'StorageError', None),
    (ValueError('private-test-password'), 'OtherError', None),
])
async def test_fixture_failure_retains_reason_without_exception_payload(monkeypatch, capsys, error, kind, code):
    async def fail(**kwargs):
        raise error
    monkeypatch.setattr(worker_financial_process, 'launch', fail)
    with pytest.raises(type(error)) as caught:
        await worker_financial_process.diagnostic_launch()
    assert caught.value is error
    output = capsys.readouterr().out
    assert 'private-test-password' not in output
    assert json.loads(output) == dict(event='FIXTURE_SETUP_FAILED', error_kind=kind, reason_code=code)
