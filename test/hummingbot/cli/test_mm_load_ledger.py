from scripts.mm_load_ledger import ledger_violations


def test_flat_venue_hedge_leg_matches_absent_api_and_active_exposure_bucket():
    from decimal import Decimal

    from scripts.mm_load_ledger import venue_position_exposure

    long = dict(instId='LIFE-USDT-SWAP', posSide='long', pos='.1')
    short = dict(instId='LIFE-USDT-SWAP', posSide='short', pos='0.0')
    assert venue_position_exposure([long, short]) == {('LIFE-USDT', 'LONG'): Decimal('.001')}
    assert venue_position_exposure([short]) == {}


def test_venue_position_normalization_rejects_bad_or_duplicate_leg_evidence():
    import pytest

    from scripts.mm_load_ledger import venue_position_exposure

    row = dict(instId='LIFE-USDT-SWAP', posSide='short', pos='0')
    for positions in ([row, row], [{**row, 'pos': 'NaN'}], [{**row, 'pos': '-1'}],
                      [{**row, 'posSide': 'net'}], [{**row, 'instId': 'LIFE-USDT'}]):
        with pytest.raises(ValueError):
            venue_position_exposure(positions)


def test_load_ledger_detects_duplicates_missing_events_and_wrong_contract_units():
    ledger = {"fills": [dict(instId="LIFE-USDT-SWAP", clOrdId="one", tradeId="1", fillSz="0.1", fillPx="0.1", fee="-0.000001")]}
    row = dict(market="okx_perpetual", order_id="one", exchange_trade_id="1", amount="0.001", price="0.1", trade_fee_in_quote="0.000001")
    assert ledger_violations(ledger, [row]) == []
    assert ledger_violations(ledger, [])
    assert ledger_violations(ledger, [row, row])
    assert ledger_violations(ledger, [{**row, "amount": 100000}])
    assert ledger_violations(ledger, [{**row, "trade_fee_in_quote": 0}])


def test_funding_ledger_compares_identity_currency_and_exact_decimal_amount():
    from scripts.mm_load_ledger import funding_ledger_violations
    bills = [dict(billId='1', instId='LIFE-USDT-SWAP', ts='1700000000000', type='8', subType='174',
                  pnl='0.0000000000000000001', ccy='USDT')]
    row = dict(account_id='account', environment='demo', bill_id='1', symbol='LIFE-USDT', timestamp=1700000000000,
               amount='0.0000000000000000001', currency='USDT')
    assert funding_ledger_violations(bills, [row], account_id='account', environment='demo') == []
    for key, value in [('amount', '0'), ('currency', 'BTC'), ('account_id', 'other'), ('bill_id', '2')]:
        assert funding_ledger_violations(bills, [{**row, key: value}], account_id='account', environment='demo')
