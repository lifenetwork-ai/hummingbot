"""Bounded OKX spot bill scan with explicitly approved cashflows."""

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.risk import ReservationLedger
from hummingbot.strategy_v2.life_liquidity.state import IntentWAL


def _bill_id(value: object) -> bool:
    return (isinstance(value, str) and value.isascii() and value.isdecimal()
            and int(value) > 0)


@dataclass(frozen=True)
class CashflowApprovals:
    anchor_bill_id: str
    approved: dict[str, tuple[str, Decimal]]

    @classmethod
    def load(cls, path: Path) -> "CashflowApprovals":
        try:
            path = Path(path)
            if not path.is_absolute() or path.is_symlink() or not path.is_file():
                raise ValueError("CASHFLOW_APPROVAL_INVALID")
            with path.open(encoding="utf-8") as handle:
                raw = json.load(handle)
            anchor = raw["anchor_bill_id"]
            items = raw["approved"]
            if (raw["schema_version"] != 1 or not _bill_id(anchor)
                    or not isinstance(items, list)):
                raise ValueError("CASHFLOW_APPROVAL_INVALID")
            approved = {}
            for item in items:
                bill_id = item["bill_id"]
                currency = item["currency"]
                amount = Decimal(item["amount"])
                if (not _bill_id(bill_id) or int(bill_id) <= int(anchor)
                        or bill_id in approved or currency not in ("LIFE", "USDT")
                        or not amount.is_finite() or amount == 0):
                    raise ValueError("CASHFLOW_APPROVAL_INVALID")
                approved[bill_id] = (currency, amount)
            return cls(anchor, approved)
        except (AttributeError, KeyError, TypeError, OSError, InvalidOperation,
                json.JSONDecodeError) as exc:
            raise ValueError("CASHFLOW_APPROVAL_INVALID") from exc


class SpotBillReconciler:
    MAX_PAGES = 6

    def __init__(self, connector, reservations: ReservationLedger, wal: IntentWAL,
                 approvals: CashflowApprovals):
        self.connector = connector
        self.reservations = reservations
        self.wal = wal
        self.approvals = approvals

    async def _since_anchor(self) -> list[dict]:
        rows = []
        cursor = None
        previous_id = None
        for _ in range(self.MAX_PAGES):
            response = await self.connector.get_account_bills_page(after=cursor)
            if (not isinstance(response, dict) or response.get("code") != "0"
                    or not isinstance(response.get("data"), list)
                    or len(response["data"]) > 100):
                raise ValueError("ACCOUNT_BILLS_UNTRUSTED")
            page = response["data"]
            for row in page:
                bill_id = row["billId"]
                if (not _bill_id(bill_id) or previous_id is not None
                        and int(bill_id) >= previous_id):
                    raise ValueError("ACCOUNT_BILLS_UNTRUSTED")
                previous_id = int(bill_id)
                if bill_id == self.approvals.anchor_bill_id:
                    return rows
                if previous_id < int(self.approvals.anchor_bill_id):
                    raise ValueError("ACCOUNT_BILL_ANCHOR_MISSING")
                rows.append(row)
            if len(page) < 100:
                raise ValueError("ACCOUNT_BILL_ANCHOR_MISSING")
            cursor = page[-1]["billId"]
        raise ValueError("ACCOUNT_BILLS_INCOMPLETE")

    async def reconcile(self) -> bool:
        try:
            rows = await self._since_anchor()
            transfers = []
            seen_approved = set()
            seen_trades = set()
            trade_fees: dict[str, dict[str, Decimal]] = {}
            for row in rows:
                bill_id = row["billId"]
                currency = row["ccy"]
                if currency not in ("LIFE", "USDT"):
                    raise ValueError("ACCOUNT_BILL_CURRENCY_UNKNOWN")
                if row["type"] == "1":
                    amount = Decimal(row["balChg"])
                    if (not amount.is_finite() or amount == 0
                            or (row["subType"], row["from"], row["to"])
                            not in (("11", "6", "18"), ("12", "18", "6"))
                            or row["subType"] == "11" and amount <= 0
                            or row["subType"] == "12" and amount >= 0
                            or self.approvals.approved.get(bill_id) != (currency, amount)
                            or row.get("ordId") or row.get("tradeId")):
                        raise ValueError("ACCOUNT_CASHFLOW_UNAPPROVED")
                    seen_approved.add(bill_id)
                    transfers.append((bill_id, currency, amount))
                elif row["type"] == "2":
                    trade_id = row["tradeId"]
                    intent_id = self.reservations.trade_intent_id(trade_id)
                    if (not isinstance(trade_id, str) or not trade_id
                            or intent_id is None or row["instId"] != "LIFE-USDT"
                            or row["subType"] not in ("1", "2")
                            or not isinstance(row["ordId"], str) or not row["ordId"]):
                        raise ValueError("ACCOUNT_TRADE_UNOWNED")
                    intent = self.wal.get(intent_id)
                    if (intent.exchange_order_id != row["ordId"]
                            or row.get("clOrdId") not in (None, "", intent.client_order_id)):
                        raise ValueError("ACCOUNT_TRADE_UNOWNED")
                    bill_fee = Decimal(row["fee"])
                    bill_change = Decimal(row["balChg"])
                    if not bill_fee.is_finite() or not bill_change.is_finite():
                        raise ValueError("ACCOUNT_TRADE_BILL_INVALID")
                    amounts = trade_fees.setdefault(trade_id, {})
                    amounts[currency] = amounts.get(currency, Decimal("0")) + bill_fee
                    seen_trades.add(trade_id)
                else:
                    raise ValueError("ACCOUNT_BILL_TYPE_UNKNOWN")
            if seen_approved != set(self.approvals.approved):
                raise ValueError("ACCOUNT_CASHFLOW_MISSING")
            if seen_trades != self.reservations.trade_ids:
                raise ValueError("ACCOUNT_TRADE_BILL_MISSING")
            for trade_id, amounts in trade_fees.items():
                actual = self.reservations.fee_for_trade(trade_id)
                if (actual is None or amounts.get(actual[0], Decimal("0")) != actual[1]
                        or any(amount != 0 for currency, amount in amounts.items()
                               if currency != actual[0])):
                    raise ValueError("ACCOUNT_FEE_BILL_MISMATCH")
            self.reservations.record_cashflows_batch(tuple(reversed(transfers)))
            return True
        except (AttributeError, KeyError, TypeError, ValueError, InvalidOperation,
                TimeoutError, OSError):
            return False
