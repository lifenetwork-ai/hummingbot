"""Compare independent simulator truth with recorder rows, preserving units."""
from decimal import Decimal


def ledger_violations(ledger, rows):
    expected = {}
    for fill in ledger["fills"]:
        perpetual = fill["instId"].endswith("-SWAP")
        key = ("okx_perpetual" if perpetual else "okx", fill["clOrdId"], fill["tradeId"])
        expected[key] = (Decimal(fill["fillSz"]) * (Decimal("0.01") if perpetual else 1),
                         Decimal(fill["fillPx"]), -Decimal(fill["fee"]))
    actual = {}
    errors = []
    for row in rows:
        key = row["market"], row["order_id"], row["exchange_trade_id"]
        if key in actual:
            errors.append("duplicate_recorder_fill")
        actual[key] = tuple(Decimal(str(row[key])) for key in ("amount", "price", "trade_fee_in_quote"))
        if "trade_fee" in row:
            fees = row["trade_fee"]
            original = sum((Decimal(str(fee["amount"])) for fee in fees.get("flat_fees", []) if fee["token"] == "USDT"), Decimal(0))
            if original != actual[key][2]:
                errors.append("original_fee")
    if actual != expected:
        errors.append("recorder_fill_units_price_or_fee")
    return errors


def funding_ledger_violations(bills, rows, *, account_id, environment):
    expected = {(account_id, environment, str(bill["billId"])): (
        bill["instId"].removesuffix("-SWAP"), int(bill["ts"]), Decimal(bill["pnl"]), bill["ccy"])
        for bill in bills if str(bill["type"]) == "8" and str(bill["subType"]) in ("173", "174")}
    actual = {(row["account_id"], row["environment"], row["bill_id"]): (
        row["symbol"], row["timestamp"], Decimal(row["amount"]), row["currency"]) for row in rows}
    return [] if expected == actual and len(actual) == len(rows) else ["funding_identity_currency_or_amount"]
