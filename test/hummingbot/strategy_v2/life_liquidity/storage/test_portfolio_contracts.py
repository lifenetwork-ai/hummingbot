"""Native valuation receipts are diagnostic decimal28 values, never grants."""
from decimal import ROUND_DOWN, Decimal as D, localcontext

import pytest

from hummingbot.strategy_v2.life_liquidity.storage.contracts import AtomicScope, StorageError
from hummingbot.strategy_v2.life_liquidity.storage.postgres.portfolio import MeasurePortfolio, PgPortfolioRepository


def receipt():
    return dict(kind='PORTFOLIO_NAV', operation_id='nav1', halted=False, replayed=False, trading_permission=False,
                measurement=dict(nav_quote='14.6', adjusted_nav_quote='14.6', highwater_quote='15',
                                 drawdown_bps='266.6666666666666666666666667', unrealized_quote='.11',
                                 external_flows_quote='0', trading_permission=False))


def parse(value):
    repo = object.__new__(PgPortfolioRepository)
    repo._original = lambda: None  # Native tests exercise actual context fencing.
    repo.binding = None
    return repo._receipt(value)


@pytest.mark.parametrize('value', [{}, {'operation_id': 'nav', 'nav': '15'}, {'operation_id': True}, {'operation_id': ''}])
def test_measurement_command_cannot_accept_caller_money_or_permission(value):
    with pytest.raises(StorageError):
        MeasurePortfolio.from_json(value)


@pytest.mark.parametrize('precision', [6, 28, 80])
def test_portfolio_receipt_preserves_declared_precision_and_never_enables_trading(precision):
    with localcontext() as context:
        context.prec, context.rounding = precision, ROUND_DOWN
        result = parse(receipt())
        assert result.measurement['drawdown_bps'] == D('266.6666666666666666666666667')
        assert context.prec == precision and context.rounding == ROUND_DOWN
        assert result.atomic_scope is AtomicScope.ACCOUNT and not result.trading_permission
    command = MeasurePortfolio('nav1')
    assert MeasurePortfolio.from_json(command.json()) == command


@pytest.mark.parametrize('field,value', [('nav_quote', 'NaN'), ('nav_quote', 'Infinity'), ('nav_quote', 'bad'),
                                         ('nav_quote', 1), ('nav_quote', True), ('nav_quote', '1e200'),
                                         ('highwater_quote', '0'), ('drawdown_bps', '-1'), ('trading_permission', True)])
def test_invalid_measurement_is_rejected_without_coercing_money_or_permission(field, value):
    data = receipt()
    data['measurement'][field] = value
    with pytest.raises(StorageError, match='PG_PORTFOLIO_RECEIPT_INVALID'):
        parse(data)


@pytest.mark.parametrize('field,value', [('halted', 1), ('replayed', 0), ('trading_permission', True), ('kind', 'SPOT_NAV')])
def test_receipt_metadata_does_not_coerce_boolean_or_product_scope(field, value):
    data = receipt()
    data[field] = value
    with pytest.raises(StorageError, match='PG_PORTFOLIO_RECEIPT_INVALID'):
        parse(data)
