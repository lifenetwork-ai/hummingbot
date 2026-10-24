import asyncio
import logging
from pathlib import Path
from typing import Dict, Iterable, Optional

from hummingbot.client.config.config_crypt import PASSWORD_VERIFICATION_PATH, BaseSecretsManager, validate_password
from hummingbot.client.config.config_helpers import (
    ClientConfigAdapter,
    api_keys_from_connector_config_map,
    connector_name_from_file,
    get_connector_config_yml_path,
    list_connector_configs,
    load_connector_config_map_from_file,
    reset_connector_hb_config,
    save_to_yml,
    update_connector_hb_config,
)

# from hummingbot.core.utils.async_call_scheduler import AsyncCallScheduler
# from hummingbot.core.utils.async_utils import safe_ensure_future
from hummingbot.logger import HummingbotLogger


class Security:
    __instance = None
    secrets_manager: Optional[BaseSecretsManager] = None
    _secure_configs = {}
    _decryption_done = asyncio.Event()
    _environment_mode = False

    _logger: Optional[HummingbotLogger] = None

    @classmethod
    def logger(cls) -> HummingbotLogger:
        if cls._logger is None:
            cls._logger = logging.getLogger(__name__)
        return cls._logger

    @staticmethod
    def new_password_required() -> bool:
        return not PASSWORD_VERIFICATION_PATH.exists()

    @classmethod
    def any_secure_configs(cls):
        return len(cls._secure_configs) > 0

    @staticmethod
    def connector_config_file_exists(connector_name: str) -> bool:
        if Security._environment_mode:
            return connector_name in Security._secure_configs
        connector_configs_path = get_connector_config_yml_path(connector_name)
        return connector_configs_path.exists()

    @classmethod
    def login(cls, secrets_manager: BaseSecretsManager) -> bool:
        if not validate_password(secrets_manager):
            return False
        cls.secrets_manager = secrets_manager
        cls._environment_mode = False
        cls.decrypt_all()
        # coro = AsyncCallScheduler.shared_instance().call_async(cls.decrypt_all, timeout_seconds=30)
        # safe_ensure_future(coro)
        return True

    @classmethod
    def initialize_from_environment(cls, connectors: Iterable[str] = ()) -> None:
        from hummingbot.client.config.environment_credentials import load_connector_credentials

        # Build/validate all requested inputs before replacing the selected source.
        configs = {name: load_connector_credentials(name) for name in connectors}
        for name in cls._secure_configs:
            reset_connector_hb_config(name)
        cls._decryption_done.clear()
        cls._secure_configs = configs
        cls.secrets_manager = None
        cls._environment_mode = True
        for config in configs.values():
            update_connector_hb_config(config)
        cls._decryption_done.set()

    @classmethod
    def uses_environment_credentials(cls) -> bool:
        return cls._environment_mode

    @classmethod
    def decrypt_all(cls):
        if cls._environment_mode:
            raise RuntimeError("Environment credentials must not enter the keystore decryption path.")
        cls._secure_configs.clear()
        cls._decryption_done.clear()
        encrypted_files = list_connector_configs()
        for file in encrypted_files:
            cls.decrypt_connector_config(file)
        cls._decryption_done.set()

    @classmethod
    def decrypt_connector_config(cls, file_path: Path):
        if cls._environment_mode:
            raise RuntimeError("Environment credentials must not enter the keystore decryption path.")
        connector_name = connector_name_from_file(file_path)
        connector_config = load_connector_config_map_from_file(file_path)
        cls._secure_configs[connector_name] = connector_config
        update_connector_hb_config(connector_config)

    @classmethod
    def update_secure_config(cls, connector_config: ClientConfigAdapter):
        if cls._environment_mode:
            raise RuntimeError("Environment credentials are read-only; update the environment and restart.")
        connector_name = connector_config.connector
        file_path = get_connector_config_yml_path(connector_name)
        save_to_yml(file_path, connector_config)
        update_connector_hb_config(connector_config)
        cls._secure_configs[connector_name] = connector_config

    @classmethod
    def remove_secure_config(cls, connector_name: str):
        if cls._environment_mode:
            raise RuntimeError("Environment credentials are read-only; update the environment and restart.")
        file_path = get_connector_config_yml_path(connector_name)
        file_path.unlink(missing_ok=True)
        reset_connector_hb_config(connector_name)
        cls._secure_configs.pop(connector_name, None)

    @classmethod
    def is_decryption_done(cls):
        return cls._decryption_done.is_set()

    @classmethod
    def decrypted_value(cls, key: str) -> Optional[ClientConfigAdapter]:
        return cls._secure_configs.get(key, None)

    @classmethod
    def all_decrypted_values(cls) -> Dict[str, ClientConfigAdapter]:
        return cls._secure_configs.copy()

    @classmethod
    async def wait_til_decryption_done(cls):
        await cls._decryption_done.wait()

    @classmethod
    def api_keys(cls, connector_name: str) -> Dict[str, Optional[str]]:
        connector_config = cls.decrypted_value(connector_name)
        if cls._environment_mode and connector_config is None:
            from hummingbot.client.config.environment_credentials import load_connector_credentials

            # The existing connector manager selects the connector. Lazy validation
            # avoids requiring credentials for unused exchanges or a stopped bot.
            connector_config = load_connector_credentials(connector_name)
            cls._secure_configs[connector_name] = connector_config
            update_connector_hb_config(connector_config)
        keys = (
            api_keys_from_connector_config_map(connector_config)
            if connector_config is not None
            else {}
        )
        return keys
