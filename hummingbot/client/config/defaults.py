"""Non-secret environment defaults shared by runtime configuration loaders."""
from types import MappingProxyType

DEFAULT_ENV_CONFIG = MappingProxyType({
    "ENV_FILE": ".env",
    "STORAGE_BACKEND": "file",
    "ENABLE_AUTO_MIGRATION": "false",
    "BOT_ID": "hummingbot",
    "PORT": "8080",
})
