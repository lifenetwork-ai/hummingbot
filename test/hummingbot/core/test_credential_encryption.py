from dataclasses import replace

import pytest

from hummingbot.core.credential_encryption import (
    CredentialCipher,
    CredentialContext,
    CredentialCryptoError,
    LocalKeyProvider,
)

CONTEXT = CredentialContext(bot_id="bot-1", exchange="okx", connector="okx", environment="demo", version=1, account_id="account-1")
SECRETS = {"okx_api_key": "canary-api-key", "okx_secret_key": "canary-secret", "okx_passphrase": "canary-passphrase"}


def cipher(key=b"K" * 32, key_id="local-v1"):
    return CredentialCipher(LocalKeyProvider(key, key_id))


def test_complete_bundle_roundtrips_with_new_dek_and_nonces_each_time():
    service = cipher()
    first, second = service.encrypt(SECRETS, CONTEXT), service.encrypt(SECRETS, CONTEXT)
    assert service.decrypt(first, CONTEXT) == SECRETS
    assert cipher().decrypt(first, CONTEXT) == SECRETS
    for field in ("ciphertext", "nonce", "wrapped_dek", "wrap_nonce"):
        assert first.data[field] != second.data[field]
    assert all(value not in repr(first.data) and value not in repr(first) for value in SECRETS.values())


@pytest.mark.parametrize("context", [replace(CONTEXT, bot_id="bot-2"), replace(CONTEXT, exchange="other"),
                                     replace(CONTEXT, connector="okx_perpetual"), replace(CONTEXT, environment="live"),
                                     replace(CONTEXT, version=2), replace(CONTEXT, account_id="account-2")])
def test_envelope_cannot_be_moved_between_scopes_or_versions(context):
    service = cipher()
    with pytest.raises(CredentialCryptoError, match="credential_decryption_failed"):
        service.decrypt(service.encrypt(SECRETS, CONTEXT), context)


@pytest.mark.parametrize("field", ["ciphertext", "nonce", "wrapped_dek", "wrap_nonce", "key_id", "provider", "format"])
def test_modified_envelope_never_returns_partial_plaintext(field):
    service = cipher()
    envelope = service.encrypt(SECRETS, CONTEXT)
    corrupted = {**envelope.data, field: "tampered-canary"}
    with pytest.raises(CredentialCryptoError) as error:
        service.decrypt(corrupted, CONTEXT)
    assert "canary" not in str(error.value)


def test_wrong_key_or_key_identifier_does_not_regenerate_or_fall_back():
    envelope = cipher().encrypt(SECRETS, CONTEXT)
    for service in (cipher(b"X" * 32), cipher(key_id="different-key")):
        with pytest.raises(CredentialCryptoError):
            service.decrypt(envelope, CONTEXT)
