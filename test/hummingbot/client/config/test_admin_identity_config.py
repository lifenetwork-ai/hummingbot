import pytest

from hummingbot.client.config.admin_config import AdminSettings
from hummingbot.client.config.runtime_config import RuntimeConfigurationError


def test_basic_is_disabled_by_default_even_when_password_is_populated():
    settings = AdminSettings.from_environment({"ADMIN_API_USERNAME": "old-admin", "ADMIN_API_PASSWORD": "old-secret"})
    assert settings.basic_auth_enabled is False
    with pytest.raises(RuntimeConfigurationError, match="authentication method"):
        AdminSettings.from_environment({"ADMIN_API_ENABLED": "true", "ADMIN_API_USERNAME": "old-admin",
                                        "ADMIN_API_PASSWORD": "old-secret"})


def test_explicit_basic_bootstrap_requires_credentials():
    settings = AdminSettings.from_environment({"ADMIN_API_ENABLED": "true", "ADMIN_BASIC_AUTH_ENABLED": "true",
                                               "ADMIN_API_USERNAME": "operator", "ADMIN_API_PASSWORD": "test-secret"})
    assert settings.enabled and settings.basic_auth_enabled
    assert "test-secret" not in repr(settings)
    with pytest.raises(RuntimeConfigurationError, match="USERNAME"):
        AdminSettings.from_environment({"ADMIN_API_ENABLED": "true", "ADMIN_BASIC_AUTH_ENABLED": "true"})


def test_google_only_does_not_require_basic_credentials():
    settings = AdminSettings.from_environment({"ADMIN_API_ENABLED": "true", "ADMIN_GOOGLE_AUTH_ENABLED": "true",
                                               "ADMIN_GOOGLE_CLIENT_ID": "test-client.apps.googleusercontent.com",
                                               "STORAGE_BACKEND": "postgres"})
    assert settings.google_auth_enabled and not settings.basic_auth_enabled
    assert settings.google_client_id == "test-client.apps.googleusercontent.com"


@pytest.mark.parametrize("values", [
    {"ADMIN_GOOGLE_AUTH_ENABLED": "true", "STORAGE_BACKEND": "postgres"},
    {"ADMIN_GOOGLE_AUTH_ENABLED": "true", "ADMIN_GOOGLE_CLIENT_ID": "test-client", "STORAGE_BACKEND": "file"},
    {"ADMIN_BASIC_AUTH_ENABLED": "yes"}, {"ADMIN_GOOGLE_AUTH_ENABLED": "yes"},
])
def test_invalid_enabled_method_configuration_fails_without_leaking_values(values):
    with pytest.raises(RuntimeConfigurationError):
        AdminSettings.from_environment({"ADMIN_API_ENABLED": "true", **values})


def test_disabled_google_method_ignores_absent_client_id():
    assert not AdminSettings.from_environment({"ADMIN_GOOGLE_AUTH_ENABLED": "false"}).google_auth_enabled
