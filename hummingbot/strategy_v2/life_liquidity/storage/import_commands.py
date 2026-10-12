"""Exact, signed repair proposals for supported legacy provenance/cashflow cases."""
from dataclasses import dataclass

from hummingbot.strategy_v2.life_liquidity.storage.capital import CashflowSpot
from hummingbot.strategy_v2.life_liquidity.storage.contracts import OwnerToken, StorageError, identifier
from hummingbot.strategy_v2.life_liquidity.storage.financial import FinancialCut


@dataclass(frozen=True)
class ImportPreparationOrigin:
    operation_id: str
    prepare_operation_id: str
    original_cut: FinancialCut
    original_owner: OwnerToken

    def __post_init__(self):
        identifier(self.operation_id)
        identifier(self.prepare_operation_id)
        if (len(self.operation_id) > 96 or not isinstance(self.original_cut, FinancialCut)
                or not isinstance(self.original_owner, OwnerToken)):
            raise StorageError('IMPORT_ORIGIN_INVALID')

    def json(self):
        return {'kind': 'IMPORT_PREPARATION_ORIGIN', 'operation_id': self.operation_id,
                'prepare_operation_id': self.prepare_operation_id, 'original_cut': self.original_cut.json(),
                'original_owner': self.original_owner.json()}


@dataclass(frozen=True)
class ImportApprovedCashflows:
    operation_id: str
    writer: OwnerToken
    cashflows: tuple[CashflowSpot, ...]

    def __post_init__(self):
        identifier(self.operation_id)
        if (len(self.operation_id) > 96 or not isinstance(self.writer, OwnerToken)
                or type(self.cashflows) is not tuple or not self.cashflows
                or any(not isinstance(row, CashflowSpot) for row in self.cashflows)
                or len({row.bill_id for row in self.cashflows}) != len(self.cashflows)
                or len({row.operation_id for row in self.cashflows}) != len(self.cashflows)):
            raise StorageError('IMPORT_CASHFLOW_INVALID')

    def json(self):
        return {'kind': 'IMPORT_APPROVED_CASHFLOWS', 'operation_id': self.operation_id,
                'writer': self.writer.json(), 'cashflows': [row.json() for row in self.cashflows]}
