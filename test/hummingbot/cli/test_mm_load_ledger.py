from scripts.mm_load_ledger import ledger_violations


def test_load_ledger_detects_duplicates_missing_events_and_wrong_contract_units():
    ledger = {"fills": [dict(instId="LIFE-USDT-SWAP", clOrdId="one", tradeId="1", fillSz="0.1", fillPx="0.1", fee="-0.000001")]}
    row = dict(market="okx_perpetual", order_id="one", exchange_trade_id="1", amount=1000, price=100000, trade_fee_in_quote=1)
    assert ledger_violations(ledger, [row]) == []
    assert ledger_violations(ledger, [])
    assert ledger_violations(ledger, [row, row])
    assert ledger_violations(ledger, [{**row, "amount": 100000}])
    assert ledger_violations(ledger, [{**row, "trade_fee_in_quote": 0}])
