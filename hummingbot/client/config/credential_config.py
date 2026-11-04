"""Deployment-owned credential source/KEK selection; no runtime secret defaults."""
import base64
import os
import re
from dataclasses import dataclass, field
from typing import Optional

from hummingbot.client.config.defaults import DEFAULT_ENV_CONFIG
from hummingbot.client.config.runtime_config import RuntimeConfigurationError, storage_backend


def credential_source_mode(environ=None):
    values = os.environ if environ is None else environ
    source = values.get("EXCHANGE_CREDENTIALS_SOURCE", DEFAULT_ENV_CONFIG["EXCHANGE_CREDENTIALS_SOURCE"])
    if source not in ("environment", "postgres_kms"):
        raise RuntimeConfigurationError("Choose EXCHANGE_CREDENTIALS_SOURCE=environment/postgres_kms.")
    return source


@dataclass(frozen=True)
class CredentialSettings:
    source: str = "environment"
    provider: str = "local"
    local_key: Optional[bytes] = field(default=None, repr=False)
    local_key_id: str = "local-v1"
    gcp_key_resource: str = ""

    @classmethod
    def from_environment(cls, environ=None):
        values = os.environ if environ is None else environ
        source = values.get("EXCHANGE_CREDENTIALS_SOURCE", DEFAULT_ENV_CONFIG["EXCHANGE_CREDENTIALS_SOURCE"])
        provider = values.get("KMS_PROVIDER", DEFAULT_ENV_CONFIG["KMS_PROVIDER"])
        if source not in ("environment", "postgres_kms") or provider not in ("local", "gcp"):
            raise RuntimeConfigurationError("Choose EXCHANGE_CREDENTIALS_SOURCE=environment/postgres_kms and KMS_PROVIDER=local/gcp.")
        key_id = values.get("KMS_LOCAL_KEY_ID", DEFAULT_ENV_CONFIG["KMS_LOCAL_KEY_ID"])
        resource = values.get("KMS_GCP_KEY_RESOURCE", "")
        key = None
        if source == "postgres_kms":
            if storage_backend(values) != "postgres":
                raise RuntimeConfigurationError("Managed exchange credentials require STORAGE_BACKEND=postgres.")
            if provider == "local":
                try:
                    key = base64.b64decode(values.get("KMS_LOCAL_SECRET_KEY", ""), validate=True)
                    if len(key) != 32 or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", key_id):
                        raise ValueError
                except Exception:
                    raise RuntimeConfigurationError("Local KMS requires a base64 32-byte KMS_LOCAL_SECRET_KEY and valid KMS_LOCAL_KEY_ID.") from None
            elif not re.fullmatch(r"projects/[A-Za-z0-9:_-]+/locations/[A-Za-z0-9_-]+/keyRings/[A-Za-z0-9_-]+/cryptoKeys/[A-Za-z0-9_-]+", resource):
                raise RuntimeConfigurationError("KMS_GCP_KEY_RESOURCE must identify a configured symmetric CryptoKey.")
        return cls(source, provider, key, key_id, resource)
