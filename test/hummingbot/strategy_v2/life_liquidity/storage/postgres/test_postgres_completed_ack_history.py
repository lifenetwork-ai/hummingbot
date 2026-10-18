"""Completed original core ACK commands remain zero-money recovery history."""
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_spot import recover_case

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('side', ['BUY', 'SELL'])
@pytest.mark.parametrize('stage', ['wallet_before', 'receipt'])
@pytest.mark.parametrize('funding', [(), (D('-.03'), D('.02'))])
async def test_completed_core_ack_command_recovers_original_receipts_cash_and_holds(database, tmp_path, monkeypatch, side, stage, funding):
    expected = {}
    prepared = await setup(database, tmp_path, monkeypatch, stage, spot_fill=(side, 'USDT'), file_result=expected,
                           prior_spot_order=True, prior_core_ack=True, prior_after_accounting=True, prior_funding=funding)
    await recover_case(database, tmp_path, monkeypatch, stage, side, 'USDT', prepared=prepared, expected=expected,
                       prior_spot_order=True, prior_after_accounting=True, prior_funding=funding)


@pytest.mark.asyncio
async def test_completed_core_ack_requires_actual_original_receipt_owner_and_callback(database, tmp_path, monkeypatch):
    import json
    from dataclasses import replace

    from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint
    from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import funding_profile

    values, _, command, _, _, _, _, store, source = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'USDT'), prior_spot_order=True,
        prior_core_ack=True, prior_after_accounting=True, prior_funding=(D('-.03'), D('.02')))
    try:
        funding_profile(source, command)
        for fault in ('missing_receipt', 'invented_id', 'state', 'callback', 'owner'):
            envelope = json.loads(source.checkpoint_bytes)
            resources = envelope['state']['resources']
            row = resources['joint_cash_operations']['file-prior-funding-0']
            ack = row['command']['spot_acks'][0]
            if fault == 'missing_receipt':
                del resources['acknowledgements'][ack['operation_id']]
            elif fault == 'invented_id':
                ack['operation_id'] = 'invented-original'
            elif fault == 'state':
                resources['acknowledgements'][ack['operation_id']]['state'] = 'ACKING'
            elif fault == 'callback':
                ack['observed_at'] = row['owner']['valid_from']
            elif fault == 'owner':
                row['owner']['epoch'] += 1
            row['fingerprint'] = fingerprint({'command': row['command'], 'cut': row['cut'], 'owner': row['owner']})
            changed = replace(source, checkpoint_bytes=encode(envelope), cut=replace(source.cut,
                              checkpoint=replace(source.cut.checkpoint, state_digest=fingerprint(envelope['state']))))
            with pytest.raises(StorageError):
                funding_profile(changed, command)
        assert funding_profile(source, command)['opening_quote_usdt'] == '10'
    finally:
        await store.close()
        await values[0].close()
