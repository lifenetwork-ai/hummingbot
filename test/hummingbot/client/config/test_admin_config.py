import pytest

from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.client.config.runtime_config import RuntimeConfigurationError


def test_admin_is_disabled_by_default_and_credentials_are_redacted():
    assert not AdminSettings.from_environment({}).enabled
    settings = AdminSettings.from_environment({"ADMIN_API_ENABLED": "true", "ADMIN_API_USERNAME": "operator",
                                               "ADMIN_API_PASSWORD": "test-password"})
    assert settings.enabled
    assert "test-password" not in repr(settings)
    assert settings.username == "operator"


@pytest.mark.parametrize("values", [
    {"ADMIN_API_ENABLED": "yes"},
    {"ADMIN_API_ENABLED": "true"},
    {"ADMIN_API_ENABLED": "true", "ADMIN_API_USERNAME": "operator", "ADMIN_API_PASSWORD": ""},
    {"ADMIN_API_ENABLED": "true", "ADMIN_API_USERNAME": "bad:username", "ADMIN_API_PASSWORD": "test-password"},
    {"ADMIN_CORS_ORIGINS": "https://*.example"},
    {"REPORTING_TIMEZONE": "unknown/timezone"},
    {"ADMIN_STATISTICS_CACHE_TTL_SECONDS": "nan"},
    {"ADMIN_STATISTICS_CACHE_TTL_SECONDS": "inf"},
    {"ADMIN_STATISTICS_CACHE_TTL_SECONDS": "-1"},
    {"ADMIN_STATISTICS_CACHE_TTL_SECONDS": "61"},
    {"ADMIN_STATISTICS_CACHE_TTL_SECONDS": "bad"},
])
def test_invalid_auth_cors_and_timezone_configuration_is_rejected(values):
    with pytest.raises(RuntimeConfigurationError):
        AdminSettings.from_environment(values)


def test_explicit_cors_origins_and_reporting_timezone():
    settings = AdminSettings.from_environment({"ADMIN_CORS_ORIGINS": "https://ui.example,http://localhost:5173",
                                               "REPORTING_TIMEZONE": "UTC"})
    assert settings.cors_origins == ("https://ui.example", "http://localhost:5173")
    assert settings.reporting_timezone == "UTC"


def test_cors_defaults_to_all_origins_and_accepts_explicit_wildcard():
    assert AdminSettings.from_environment({}).cors_origins == ("*",)
    assert AdminSettings.from_environment({"ADMIN_CORS_ORIGINS": "*"}).cors_origins == ("*",)


def test_daily_cache_ttl_is_bounded_and_can_be_disabled():
    assert AdminSettings.from_environment({}).statistics_cache_ttl_seconds == 5
    assert AdminSettings.from_environment({"ADMIN_STATISTICS_CACHE_TTL_SECONDS": "0"}).statistics_cache_ttl_seconds == 0


def test_no_quote_warning_default_and_disable_setting():
    assert AdminSettings.from_environment({}).no_quote_warning_seconds == 120
    assert AdminSettings.from_environment({'MM_NO_QUOTE_WARNING_SECONDS': '0'}).no_quote_warning_seconds == 0


@pytest.mark.parametrize('value', ['nan', 'inf', '-1', '1', '86401', 'invalid'])
def test_invalid_no_quote_warning_interval_is_rejected(value):
    with pytest.raises(RuntimeConfigurationError):
        AdminSettings.from_environment({'MM_NO_QUOTE_WARNING_SECONDS': value})
