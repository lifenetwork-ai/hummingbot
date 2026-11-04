import os
from test.hummingbot.core.test_credential_encryption import CONTEXT, SECRETS
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from google.auth.exceptions import RefreshError

from hummingbot.core.credential_encryption import CredentialCipher, CredentialCryptoError
from hummingbot.core.gcp_key_provider import GcpKeyProvider, checksum
from hummingbot.core.gcp_kms_rest import KmsRequestError

RESOURCE = "projects/offline/locations/global/keyRings/bot/cryptoKeys/credentials"


class OfflineKms:
    def __init__(self):
        self.key = b"G" * 32
        self.calls = []
        self.failure = None
        self.bad_checksum = False
        self.transport = SimpleNamespace(close=lambda: None)

    def close(self):
        pass

    def encrypt(self, *, request, timeout):
        self.calls.append(("encrypt", request, timeout))
        if self.failure:
            raise self.failure
        assert request["plaintext_crc32c"] == checksum(request["plaintext"])
        assert request["additional_authenticated_data_crc32c"] == checksum(request["additional_authenticated_data"])
        nonce = os.urandom(12)
        value = nonce + AESGCM(self.key).encrypt(nonce, request["plaintext"], request["additional_authenticated_data"])
        return SimpleNamespace(name=RESOURCE + "/cryptoKeyVersions/1", ciphertext=value,
                               ciphertext_crc32c=0 if self.bad_checksum else checksum(value),
                               verified_plaintext_crc32c=True, verified_additional_authenticated_data_crc32c=True)

    def decrypt(self, *, request, timeout):
        self.calls.append(("decrypt", request, timeout))
        if self.failure:
            raise self.failure
        value = request["ciphertext"]
        plaintext = AESGCM(self.key).decrypt(value[:12], value[12:], request["additional_authenticated_data"])
        return SimpleNamespace(plaintext=plaintext, plaintext_crc32c=0 if self.bad_checksum else checksum(plaintext))


def test_gcp_wrap_uses_scoped_aad_and_bounded_transport_call():
    client = OfflineKms()
    service = CredentialCipher(GcpKeyProvider(RESOURCE, client=client))
    envelope = service.encrypt(SECRETS, CONTEXT)
    assert envelope.data["key_id"] == RESOURCE + "/cryptoKeyVersions/1"
    assert service.decrypt(envelope, CONTEXT) == SECRETS
    assert all(timeout == 5 for _, _, timeout in client.calls)
    assert all(request["additional_authenticated_data"] for _, request, _ in client.calls)


def test_gcp_key_version_metadata_is_authenticated_even_for_same_crypto_key():
    service = CredentialCipher(GcpKeyProvider(RESOURCE, client=OfflineKms()))
    envelope = service.encrypt(SECRETS, CONTEXT)
    modified = {**envelope.data, "key_id": RESOURCE + "/cryptoKeyVersions/2"}
    with pytest.raises(CredentialCryptoError):
        service.decrypt(modified, CONTEXT)


@pytest.mark.parametrize("failure,code", [(KmsRequestError(403), "kms_permission_denied"),
                                          (RefreshError("canary-private-error", retryable=True), "kms_unavailable"),
                                          (TimeoutError("canary-private-error"), "kms_unavailable")])
def test_transport_failures_are_safe_and_never_trigger_a_local_fallback(failure, code):
    client = OfflineKms()
    client.failure = failure
    with pytest.raises(CredentialCryptoError, match=code) as error:
        CredentialCipher(GcpKeyProvider(RESOURCE, client=client)).encrypt(SECRETS, CONTEXT)
    assert "canary" not in str(error.value)
    assert len(client.calls) == 1


def test_gcp_response_checksum_failure_rejects_data():
    client = OfflineKms()
    service = CredentialCipher(GcpKeyProvider(RESOURCE, client=client))
    client.bad_checksum = True
    with pytest.raises(CredentialCryptoError):
        service.encrypt(SECRETS, CONTEXT)
    client.bad_checksum = False
    envelope = service.encrypt(SECRETS, CONTEXT)
    client.bad_checksum = True
    with pytest.raises(CredentialCryptoError):
        service.decrypt(envelope, CONTEXT)
