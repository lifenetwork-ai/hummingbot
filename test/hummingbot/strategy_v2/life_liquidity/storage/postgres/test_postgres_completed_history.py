"""Prior completed atomic SPOT/cashflow/funding commands retain original money."""
import json
from dataclasses import replace
from decimal import Decimal as D
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_applying import setup
from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.test_postgres_legacy_spot import recover_case

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError, encode, fingerprint
from hummingbot.strategy_v2.life_liquidity.storage.postgres.legacy_applying import funding_profile


@pytest.mark.asyncio
@pytest.mark.parametrize('funding_history', [(), (D('-.03'), D('.02'))])
@pytest.mark.parametrize('native_prefix', [False, True])
@pytest.mark.parametrize('side,fee_currency', [('BUY', 'LIFE'), ('SELL', 'USDT')])
@pytest.mark.parametrize('stage', ['wallet_before', 'wallet_after', 'receipt'])
async def test_completed_spot_transfer_funding_commands_recover_with_full_file_parity(database, tmp_path, monkeypatch, side, fee_currency, stage, native_prefix, funding_history):
    await recover_case(database, tmp_path, monkeypatch, stage, side, fee_currency,
                       cashflow=('USDT', D('2'), D('1')), prior_funding=funding_history,
                       prior_after_accounting=True, prior_embedded_spot=True, spot_prefix=native_prefix, book_native_prefix=native_prefix)


@pytest.mark.asyncio
async def test_completed_mixed_history_rejects_rehashed_changed_economics_and_incomplete_snapshots(database, tmp_path, monkeypatch):
    values, _, command, _, _, _, _, store, source = await setup(
        database, tmp_path, monkeypatch, spot_fill=('BUY', 'LIFE'), cashflow=('USDT', D('2'), D('1')),
        prior_funding=(D('-.03'), D('.02')), prior_after_accounting=True, prior_embedded_spot=True, spot_prefix=True)
    try:
        funding_profile(source, command)
        for fault in ('fill_application_id', 'missing_transfer_receipt', 'cash', 'base', 'fee', 'reference', 'missing_fill', 'cumulative', 'time', 'transfer', 'funding', 'regression'):
            envelope = json.loads(source.checkpoint_bytes)
            old = envelope['state']['resources']['joint_cash_operations']['file-prior-funding-1' if fault == 'regression' else 'file-prior-funding-0']
            fill = old['command']['spot_fills'][0]
            if fault == 'fill_application_id':
                fill['operation_id'] = 'missing-original-receipt'
            elif fault == 'missing_transfer_receipt':
                envelope['state']['resources']['cashflow_operations'].clear()
            elif fault == 'cash':
                old['command']['account_quote_usdt'] = '999'
            elif fault == 'base':
                old['command']['account_base_life'] = '999'
            elif fault == 'fee':
                fill['fills'][0]['signed_fee'] = '0'
            elif fault == 'reference':
                fill['fills'][0]['independent_price_usdt'] = '2'
            elif fault == 'missing_fill':
                old['command']['spot_fills'] = []
            elif fault == 'cumulative':
                fill['cumulative_base_life'] = '2'
            elif fault == 'time':
                fill['fills'][0]['fill_at_ms'] += 1
            elif fault == 'transfer':
                old['command']['spot_cashflows'][0]['signed_amount'] = '1'
            elif fault == 'funding':
                old['command']['events'][0]['signed_amount_usdt'] = '1'
            elif fault == 'regression':
                fill['fills'] = fill['fills'][:1]
                fill['cumulative_base_life'] = '.5'
            old['fingerprint'] = fingerprint({'command': old['command'], 'cut': old['cut'], 'owner': old['owner']})
            # Read-only capsule rehash exercises economic replay, not authority
            # to replace the actual retired source or its signed approval.
            changed = replace(source, checkpoint_bytes=encode(envelope), cut=replace(source.cut,
                              checkpoint=replace(source.cut.checkpoint, state_digest=fingerprint(envelope['state']))))
            with pytest.raises(StorageError):
                funding_profile(changed, command)
        assert funding_profile(source, command)['opening_quote_usdt'] == '10'
    finally:
        await store.close()
        await values[0].close()
