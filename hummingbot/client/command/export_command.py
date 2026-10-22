import os
from typing import TYPE_CHECKING, List

import pandas as pd

from hummingbot.client.config.security import Security
from hummingbot.client.settings import DEFAULT_LOG_FILE_PATH
from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.model.trade_fill import TradeFill

if TYPE_CHECKING:
    from hummingbot.client.hummingbot_application import HummingbotApplication  # noqa: F401


class ExportCommand:
    def export(self,  # type: HummingbotApplication
               option):
        if option is None or option not in ("keys", "trades"):
            self.notify("Invalid export option.")
            return
        elif option == "keys":
            safe_ensure_future(self.export_keys())
        elif option == "trades":
            safe_ensure_future(self.export_trades())

    async def export_keys(self,  # type: HummingbotApplication
                          ):
        await Security.wait_til_decryption_done()
        if not Security.any_secure_configs():
            self.notify("There are no keys to export.")
            return
        self.placeholder_mode = True
        self.app.hide_input = True
        if await self.check_password():
            self.notify("\nWarning: Never disclose API keys or private keys. Anyone with your keys can steal any "
                        "assets held in your account.")
            self.notify("\nAPI keys:")
            for key, cm in Security.all_decrypted_values().items():
                for el in cm.traverse(secure=False):
                    if el.client_field_data is not None and el.client_field_data.is_secure:
                        self.notify(f"{el.attr}: {el.printable_value}")
        self.app.change_prompt(prompt=">>> ")
        self.app.hide_input = False
        self.placeholder_mode = False

    async def prompt_new_export_file_name(self,  # type: HummingbotApplication
                                          path):
        input = await self.app.prompt(prompt="Enter a new csv file name >>> ")
        if input is None or input == "":
            self.notify("Value is required.")
            return await self.prompt_new_export_file_name(path)
        if input == " ":
            return None
        if "." not in input:
            input = input + ".csv"
        file_path = os.path.join(path, input)
        if os.path.exists(file_path):
            self.notify(f"{input} file already exists, please enter a new name.")
            return await self.prompt_new_export_file_name(path)
        else:
            return input

    async def export_trades(self,  # type: HummingbotApplication
                            ):
        from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
        try:
            reader = self._postgres_history_reader()
            if reader is not None:
                await self._export_postgres_trades(reader)
                return
        except StorageError:
            self.notify('PostgreSQL reporting incomplete; export denied, no SQLite fallback.')
            return
        with self.trading_core.trade_fill_db.get_new_session() as session:
            trades: List[TradeFill] = self._get_trades_from_session(
                int(self.init_time * 1e3),
                session=session)
            if len(trades) == 0:
                self.notify("No past trades to export.")
                return
            self.placeholder_mode = True
            self.app.hide_input = True
            path = self.client_config_map.log_file_path
            if path is None:
                path = str(DEFAULT_LOG_FILE_PATH)
            file_name = await self.prompt_new_export_file_name(path)
            if file_name is None:
                return
            file_path = os.path.join(path, file_name)
            try:
                df: pd.DataFrame = TradeFill.to_pandas(trades)
                df.to_csv(file_path, header=True)
                self.notify(f"Successfully exported trades to {file_path}")
            except Exception as e:
                self.notify(f"Error exporting trades to {path}: {e}")
            self.app.change_prompt(prompt=">>> ")
            self.placeholder_mode = False
            self.app.hide_input = False

    async def _export_postgres_trades(self, reader):
        import asyncio
        import csv

        report = await reader.snapshot(start_ms=max(0, int(self.init_time * 1000)),
                                       timeout_s=float(self.client_config_map.commands_timeout.other_commands_timeout))
        if not report.reporting_complete:
            self.notify(report.format_status() + '; export denied.')
            return
        trades = report.trades
        if not trades:
            self.notify('No qualified PostgreSQL trade events to export in this bounded view.')
            return
        self.placeholder_mode, self.app.hide_input = True, True
        try:
            path = self.client_config_map.log_file_path or str(DEFAULT_LOG_FILE_PATH)
            name = await self.prompt_new_export_file_name(path)
            if name is None:
                return
            file_path = os.path.join(path, name)

            def write():
                # Detached reporting strings only; no live DB session/owner is
                # handed to this filesystem worker. Never overwrite an export.
                with open(file_path, 'x', encoding='utf-8', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=sorted(trades[0]))
                    writer.writeheader()
                    writer.writerows(trades)
            await asyncio.to_thread(write)
            self.notify(f'Exported PostgreSQL reporting-only events to {file_path}; not a financial recovery proof.')
        except OSError:
            self.notify('PostgreSQL trade export failed; no file was overwritten.')
        finally:
            self.app.change_prompt(prompt='>>> ')
            self.placeholder_mode, self.app.hide_input = False, False
