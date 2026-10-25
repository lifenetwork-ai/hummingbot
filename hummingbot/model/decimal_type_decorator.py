from decimal import Decimal

from sqlalchemy import BigInteger, Numeric, TypeDecorator


class SqliteDecimal(TypeDecorator):
    """Exact PostgreSQL decimals with the upstream scaled-integer SQLite format.

    PostgreSQL NUMERIC has no fixed scale: derived prices/fees must not be rounded
    by storage. The constructor's scale applies only to existing SQLite files.
    """
    impl = BigInteger
    cache_ok = True

    def __init__(self, scale):
        """
        :param scale: number of digits to the right of the decimal point to consider when storing the value in the DB
        e.g. value = Column(SqliteDecimal(2)) means a value such as Decimal('12.34') will be converted to 1234
        """
        TypeDecorator.__init__(self)
        self.scale = scale
        self.multiplier_int = 10 ** self.scale

    @property
    def python_type(self):
        return Decimal

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(Numeric() if dialect.name == "postgresql" else BigInteger())

    def process_bind_param(self, value, dialect):
        if dialect.name == "postgresql":
            return self._finite_decimal(value)
        return self._convert_decimal(value)

    def process_result_value(self, value, dialect):
        if value is not None:
            value = Decimal(value) if dialect.name == "postgresql" else Decimal(value) / self.multiplier_int
        return value

    def process_literal_param(self, value, dialect):
        return str(self.process_bind_param(value, dialect))

    @staticmethod
    def _finite_decimal(value):
        if value is None:
            return None
        # Existing market-data timestamps can arrive as floats. Preserve their
        # decimal spelling instead of manufacturing binary float digits.
        result = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
        if not result.is_finite():
            raise ValueError("Recorded Decimal must be finite.")
        return result

    def _convert_decimal(self, value: Decimal) -> int:
        if value is None:
            return None
        decimal_value = Decimal(value)
        if not decimal_value.is_finite():
            raise ValueError("Recorded Decimal must be finite.")
        result = int(decimal_value * self.multiplier_int)
        if not -(2 ** 63) <= result < 2 ** 63:
            raise ValueError("Recorded Decimal exceeds the signed BIGINT range at its configured scale.")
        return result
