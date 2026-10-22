import asyncio
import threading
import time
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List, Optional, Set, Tuple

import pandas as pd

from hummingbot.client.performance import PerformanceMetrics
from hummingbot.client.settings import MAXIMUM_TRADE_FILLS_DISPLAY_OUTPUT, AllConnectorSettings
from hummingbot.client.ui.interface_utils import format_df_for_printout
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.model.trade_fill import TradeFill

s_float_0 = float(0)
s_decimal_0 = Decimal("0")


if TYPE_CHECKING:
    from hummingbot.client.hummingbot_application import HummingbotApplication  # noqa: F401


def get_timestamp(days_ago: float = 0.) -> float:
    return time.time() - (60. * 60. * 24. * days_ago)


class HistoryCommand:
    def history(self,  # type: HummingbotApplication
                days: float = 0,
                verbose: bool = False,
                precision: Optional[int] = None
                ):
        if threading.current_thread() != threading.main_thread():
            self.ev_loop.call_soon_threadsafe(self.history, days, verbose, precision)
            return

        if self.strategy_file_name is None:
            self.notify("\n  Please first import a strategy config file of which to show historical performance.")
            return
        start_time = get_timestamp(days) if days > 0 else self.init_time
        if self._schedule_postgres_history(start_time):
            return
        with self.trading_core.trade_fill_db.get_new_session() as session:
            trades: List[TradeFill] = self._get_trades_from_session(
                int(start_time * 1e3),
                session=session,
                config_file_path=self.strategy_file_name)
            if not trades:
                self.notify("\n  No past trades to report.")
                return
            if verbose:
                self.list_trades(start_time)
            safe_ensure_future(self.history_report(start_time, trades, precision))

    def get_history_trades_json(self,  # type: HummingbotApplication
                                days: float = 0):
        if self._postgres_history_reader() is not None:
            from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
            raise StorageError('PG_HISTORY_ASYNC_REQUIRED')
        if self.strategy_file_name is None:
            return
        start_time = get_timestamp(days) if days > 0 else self.init_time
        with self.trading_core.trade_fill_db.get_new_session() as session:
            trades: List[TradeFill] = self._get_trades_from_session(
                int(start_time * 1e3),
                session=session,
                config_file_path=self.strategy_file_name)
            return list([TradeFill.to_bounty_api_json(t) for t in trades])

    def _postgres_history_reader(self):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.operations import postgres_reporting
        reader = postgres_reporting(self.trading_core.strategy)
        selected = getattr(self, '_postgres_history_sources', None)
        if reader is None and type(selected) is frozenset and self.strategy_file_name in selected:
            from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
            raise StorageError('PG_HISTORY_ASSEMBLY_REQUIRED')
        if reader is not None:
            # A denial-only source marker survives detach/stop. It is not a
            # cached reader or authority and cannot open any database itself.
            self._postgres_history_sources = frozenset(r.source.config_file_path for r in reader.app.events)
        if reader is not None and self.strategy_file_name not in self._postgres_history_sources:
            from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
            raise StorageError('PG_HISTORY_SOURCE_SCOPE')
        return reader

    def _schedule_postgres_history(self, start_time):
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
        try:
            if self._postgres_history_reader() is None:
                return False
            safe_ensure_future(self._postgres_history_report(start_time))
        except StorageError:
            self.notify('PostgreSQL reporting incomplete: PG_HISTORY_ASSEMBLY_REQUIRED; no SQLite fallback.')
        return True

    async def async_get_history_trades_json(self, days: float = 0):
        reader = self._postgres_history_reader()
        if reader is None:
            return self.get_history_trades_json(days)
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
        start_time = get_timestamp(days) if days > 0 else self.init_time
        report = await reader.snapshot(start_ms=max(0, int(start_time * 1000)),
                                       timeout_s=float(self.client_config_map.commands_timeout.other_commands_timeout))
        if not report.reporting_complete:
            raise StorageError(report.reason)
        return report.trades

    async def _postgres_history_report(self, start_time):
        import json

        from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
        try:
            reader = self._postgres_history_reader()
            if reader is None:
                raise StorageError('PG_HISTORY_ASSEMBLY_REQUIRED')
            report = await reader.snapshot(start_ms=max(0, int(start_time * 1000)),
                                           timeout_s=float(self.client_config_map.commands_timeout.other_commands_timeout))
            lines = [report.format_status()]
            if report.reporting_complete:
                trades = report.trades
                if len(trades) > MAXIMUM_TRADE_FILLS_DISPLAY_OUTPUT:
                    lines.append(f'Showing last {MAXIMUM_TRADE_FILLS_DISPLAY_OUTPUT} of {len(trades)} qualified events in this view.')
                # Keep decimal and fee bytes; ORM/pandas numeric conversion is
                # rounded legacy reporting, not native financial precision.
                trades.sort(key=lambda t: (t['timestamp_ms'], t['market'], t['trade_id']))
                lines.extend(json.dumps(t, sort_keys=True) for t in trades[-MAXIMUM_TRADE_FILLS_DISPLAY_OUTPUT:])
                if not trades:
                    lines.append('No qualified trade events in this bounded view.')
            self.notify('\n'.join(lines))
        except StorageError:
            self.notify('PostgreSQL reporting incomplete: PG_HISTORY_ASSEMBLY_REQUIRED; no SQLite fallback.')

    async def history_report(self,  # type: HummingbotApplication
                             start_time: float,
                             trades: List[TradeFill],
                             precision: Optional[int] = None,
                             display_report: bool = True) -> Decimal:
        market_info: Set[Tuple[str, str]] = set((t.market, t.symbol) for t in trades)
        if display_report:
            self.report_header(start_time)
        return_pcts = []
        for market, symbol in market_info:
            cur_trades = [t for t in trades if t.market == market and t.symbol == symbol]
            network_timeout = float(self.client_config_map.commands_timeout.other_commands_timeout)
            try:
                cur_balances = await asyncio.wait_for(self.trading_core.get_current_balances(market), network_timeout)
            except asyncio.TimeoutError:
                self.notify(
                    "\nA network error prevented the balances retrieval to complete. See logs for more details."
                )
                raise
            perf = await PerformanceMetrics.create(symbol, cur_trades, cur_balances)
            if display_report:
                self.report_performance_by_market(market, symbol, perf, precision)
            return_pcts.append(perf.return_pct)
        avg_return = sum(return_pcts) / len(return_pcts) if len(return_pcts) > 0 else s_decimal_0
        if display_report and len(return_pcts) > 1:
            self.notify(f"\nAveraged Return = {avg_return:.2%}")
        return avg_return

    def report_header(self,  # type: HummingbotApplication
                      start_time: float):
        lines = []
        current_time = get_timestamp()
        lines.extend(
            [f"\nStart Time: {datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S')}"] +
            [f"Current Time: {datetime.fromtimestamp(current_time).strftime('%Y-%m-%d %H:%M:%S')}"] +
            [f"Duration: {pd.Timedelta(seconds=int(current_time - start_time))}"]
        )
        self.notify("\n".join(lines))

    def report_performance_by_market(self,  # type: HummingbotApplication
                                     market: str,
                                     trading_pair: str,
                                     perf: PerformanceMetrics,
                                     precision: int):
        lines = []
        base, quote = trading_pair.split("-")
        lines.extend(
            [f"\n{market} / {trading_pair}"]
        )

        trades_columns = ["", "buy", "sell", "total"]
        trades_data = [
            [f"{'Number of trades':<27}", perf.num_buys, perf.num_sells, perf.num_trades],
            [f"{f'Total trade volume ({base})':<27}",
             PerformanceMetrics.smart_round(perf.b_vol_base, precision),
             PerformanceMetrics.smart_round(perf.s_vol_base, precision),
             PerformanceMetrics.smart_round(perf.tot_vol_base, precision)],
            [f"{f'Total trade volume ({quote})':<27}",
             PerformanceMetrics.smart_round(perf.b_vol_quote, precision),
             PerformanceMetrics.smart_round(perf.s_vol_quote, precision),
             PerformanceMetrics.smart_round(perf.tot_vol_quote, precision)],
            [f"{'Avg price':<27}",
             PerformanceMetrics.smart_round(perf.avg_b_price, precision),
             PerformanceMetrics.smart_round(perf.avg_s_price, precision),
             PerformanceMetrics.smart_round(perf.avg_tot_price, precision)],
        ]
        trades_df: pd.DataFrame = pd.DataFrame(data=trades_data, columns=trades_columns)
        lines.extend(["", "  Trades:"] + ["    " + line for line in trades_df.to_string(index=False).split("\n")])

        assets_columns = ["", "start", "current", "change"]
        assets_data = [
            [f"{base:<17}", "-", "-", "-"] if market in AllConnectorSettings.get_derivative_names() else  # No base asset for derivatives because they are margined
            [f"{base:<17}",
             PerformanceMetrics.smart_round(perf.start_base_bal, precision),
             PerformanceMetrics.smart_round(perf.cur_base_bal, precision),
             PerformanceMetrics.smart_round(perf.tot_vol_base, precision)],
            [f"{quote:<17}",
             PerformanceMetrics.smart_round(perf.start_quote_bal, precision),
             PerformanceMetrics.smart_round(perf.cur_quote_bal, precision),
             PerformanceMetrics.smart_round(perf.tot_vol_quote, precision)],
            [f"{trading_pair + ' price':<17}",
             PerformanceMetrics.smart_round(perf.start_price),
             PerformanceMetrics.smart_round(perf.cur_price),
             PerformanceMetrics.smart_round(perf.cur_price - perf.start_price)],
            [f"{'Base asset %':<17}", "-", "-", "-"] if market in AllConnectorSettings.get_derivative_names() else  # No base asset for derivatives because they are margined
            [f"{'Base asset %':<17}",
             f"{perf.start_base_ratio_pct:.2%}",
             f"{perf.cur_base_ratio_pct:.2%}",
             f"{perf.cur_base_ratio_pct - perf.start_base_ratio_pct:.2%}"],
        ]
        assets_df: pd.DataFrame = pd.DataFrame(data=assets_data, columns=assets_columns)
        lines.extend(["", "  Assets:"] + ["    " + line for line in assets_df.to_string(index=False).split("\n")])

        perf_data = [
            ["Hold portfolio value    ", f"{PerformanceMetrics.smart_round(perf.hold_value, precision)} {quote}"],
            ["Current portfolio value ", f"{PerformanceMetrics.smart_round(perf.cur_value, precision)} {quote}"],
            ["Trade P&L               ", f"{PerformanceMetrics.smart_round(perf.trade_pnl, precision)} {quote}"]
        ]
        perf_data.extend(
            ["Fees paid               ", f"{PerformanceMetrics.smart_round(fee_amount, precision)} {fee_token}"]
            for fee_token, fee_amount in perf.fees.items()
        )
        perf_data.extend(
            [["Total P&L               ", f"{PerformanceMetrics.smart_round(perf.total_pnl, precision)} {quote}"],
             ["Return %                ", f"{perf.return_pct:.2%}"]]
        )
        perf_df: pd.DataFrame = pd.DataFrame(data=perf_data)
        lines.extend(["", "  Performance:"] +
                     ["    " + line for line in perf_df.to_string(index=False, header=False).split("\n")])

        self.notify("\n".join(lines))

    def list_trades(self,  # type: HummingbotApplication
                    start_time: float):
        if threading.current_thread() != threading.main_thread():
            self.ev_loop.call_soon_threadsafe(self.list_trades, start_time)
            return

        if self._schedule_postgres_history(start_time):
            return

        lines = []

        with self.trading_core.trade_fill_db.get_new_session() as session:
            queried_trades: List[TradeFill] = self._get_trades_from_session(
                int(start_time * 1e3),
                session=session,
                number_of_rows=MAXIMUM_TRADE_FILLS_DISPLAY_OUTPUT + 1,
                config_file_path=self.strategy_file_name)
            df: pd.DataFrame = TradeFill.to_pandas(queried_trades)

        if len(df) > 0:
            # Check if number of trades exceed maximum number of trades to display
            if len(df) > MAXIMUM_TRADE_FILLS_DISPLAY_OUTPUT:
                df = df[:MAXIMUM_TRADE_FILLS_DISPLAY_OUTPUT]
                self.notify(
                    f"\n  Showing last {MAXIMUM_TRADE_FILLS_DISPLAY_OUTPUT} trades in the current session.")
            df_lines = format_df_for_printout(df, self.client_config_map.tables_format).split("\n")
            lines.extend(["", "  Recent trades:"] +
                         ["    " + line for line in df_lines])
        else:
            lines.extend(["\n  No past trades in this session."])
        self.notify("\n".join(lines))
