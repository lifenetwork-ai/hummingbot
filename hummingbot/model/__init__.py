from sqlalchemy import MetaData
from sqlalchemy.orm import declarative_base

HummingbotBase = declarative_base(metadata=MetaData(naming_convention={
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ix": "idx_%(table_name)s_%(column_0_N_name)s",
}))


def get_declarative_base():
    from .admin_command import AdminCommand  # noqa: F401
    from .bot_state import BotState  # noqa: F401
    from .controllers import Controllers  # noqa: F401
    from .daily_balance_snapshot import DailyBalanceSnapshot  # noqa: F401
    from .executors import Executors  # noqa: F401
    from .funding_payment import FundingPayment  # noqa: F401
    from .inventory_cost import InventoryCost  # noqa: F401
    from .market_data import MarketData  # noqa: F401
    from .market_state import MarketState  # noqa: F401
    from .metadata import Metadata  # noqa: F401
    from .order import Order  # noqa: F401
    from .order_status import OrderStatus  # noqa: F401
    from .position import Position  # noqa: F401
    from .range_position_collected_fees import RangePositionCollectedFees  # noqa: F401
    from .range_position_update import RangePositionUpdate  # noqa: F401
    from .trade_fill import TradeFill  # noqa: F401
    return HummingbotBase
