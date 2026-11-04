import base64

import pytest

from hummingbot.client.config.credential_config import CredentialSettings
from hummingbot.client.config.runtime_config import RuntimeConfigurationError


def test_default_environment_source_does_not_need_a_local_key():
    settings = CredentialSettings.from_environment({})
    assert settings.source == "environment" and settings.provider == "local"


def test_managed_local_key_is_required_and_never_exposed_in_repr():
    key = base64.b64encode(b"L" * 32).decode()
    settings = CredentialSettings.from_environment({"EXCHANGE_CREDENTIALS_SOURCE": "postgres_kms", "STORAGE_BACKEND": "postgres",
                                                    "KMS_LOCAL_SECRET_KEY": key})
    assert settings.local_key == b"L" * 32
    assert key not in repr(settings) and "LLLL" not in repr(settings)


@pytest.mark.parametrize("updates", [{"KMS_LOCAL_SECRET_KEY": "invalid-canary-secret"}, {"KMS_LOCAL_SECRET_KEY": ""},
                                     {"KMS_PROVIDER": "env"}, {"KMS_PROVIDER": "remote"},
                                     {"STORAGE_BACKEND": "file"}, {"EXCHANGE_CREDENTIALS_SOURCE": "other"}])
def test_invalid_configuration_is_rejected_safely(updates):
    with pytest.raises(RuntimeConfigurationError) as error:
        CredentialSettings.from_environment({"EXCHANGE_CREDENTIALS_SOURCE": "postgres_kms", "STORAGE_BACKEND": "postgres", **updates})
    assert "invalid-canary-secret" not in str(error.value)


def test_gcp_mode_requires_a_crypto_key_resource_without_a_local_key():
    resource = "projects/offline/locations/global/keyRings/bot/cryptoKeys/credentials"
    settings = CredentialSettings.from_environment({"EXCHANGE_CREDENTIALS_SOURCE": "postgres_kms", "STORAGE_BACKEND": "postgres",
                                                    "KMS_PROVIDER": "gcp", "KMS_GCP_KEY_RESOURCE": resource})
    assert settings.gcp_key_resource == resource and settings.local_key is None
