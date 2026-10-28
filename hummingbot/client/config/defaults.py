"""Non-secret environment defaults shared by runtime configuration loaders."""
from types import MappingProxyType

DEFAULT_ENV_CONFIG = MappingProxyType({
    "ENV": "dev",
    "MM_NO_QUOTE_WARNING_SECONDS": "120",
    "ENV_FILE": ".env",
    "STORAGE_BACKEND": "file",
    "ENABLE_AUTO_MIGRATION": "false",
    "DB_POOL_SIZE": "5",
    "BOT_ID": "hummingbot",
    "PORT": "8080",
    "LOG_FORMAT": "json",
    "LOG_LEVEL": "INFO",
    "ADMIN_API_ENABLED": "false",
    "ADMIN_CORS_ORIGINS": "*",
    "ADMIN_STATISTICS_CACHE_TTL_SECONDS": "5",
    "FUNDING_SYNC_INTERVAL_SECONDS": "120",
    "REPORTING_TIMEZONE": "Asia/Ho_Chi_Minh",
    "OKX_TRADING_ENV": "live",
    "OKX_SITE": "www",
})
