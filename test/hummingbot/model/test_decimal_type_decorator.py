from decimal import Decimal

import pytest
from sqlalchemy import Numeric, literal, select
from sqlalchemy.dialects import postgresql, sqlite

from hummingbot.model.decimal_type_decorator import SqliteDecimal


@pytest.mark.parametrize("value", [Decimal("0.00024818"), Decimal("1E-30"),
                                   Decimal("123456789012345678901234567890.123456789012345678901")])
def test_postgres_precision_preserves_decimal_without_scaling(value):
    kind, dialect = SqliteDecimal(6), postgresql.dialect()
    assert isinstance(kind.dialect_impl(dialect).impl, Numeric)
    bound = kind.process_bind_param(value, dialect)
    assert bound == value
    assert kind.process_result_value(bound, dialect) == value


def test_sqlite_legacy_representation_and_postgres_literal_units():
    kind = SqliteDecimal(6)
    assert kind.process_bind_param(Decimal("0.00024818"), sqlite.dialect()) == 248
    assert kind.process_result_value(248, sqlite.dialect()) == Decimal("0.000248")
    sql = str(select(literal(Decimal("0.00024818"), type_=kind)).compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "0.00024818" in sql


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
@pytest.mark.parametrize("dialect", [postgresql.dialect(), sqlite.dialect()])
def test_nonfinite_financial_values_are_rejected(value, dialect):
    with pytest.raises(ValueError, match="finite"):
        SqliteDecimal(6).process_bind_param(Decimal(value), dialect)
