"""Purpose/scope-bound envelope encryption using existing authenticated primitives."""
import base64
import json
import os
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialCryptoError(RuntimeError):
    def __init__(self, code="credential_decryption_failed"):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class CredentialContext:
    bot_id: str
    exchange: str
    connector: str
    environment: str
    version: int
    account_id: str

    def aad(self):
        if (type(self.version) is not int or self.version < 1 or any(not isinstance(value, str) or not 1 <= len(value) <= 255
                                                                     for value in (self.bot_id, self.exchange, self.connector, self.environment, self.account_id))):
            raise ValueError("Invalid credential scope")
        return json.dumps([1, self.bot_id, self.exchange, self.connector, self.environment, self.version, self.account_id],
                          separators=(",", ":"), ensure_ascii=True).encode()


@dataclass(frozen=True)
class CredentialEnvelope:
    data: dict = field(repr=False)


def encode_bytes(value):
    return base64.b64encode(value).decode("ascii")


def decode_bytes(value, *, size=None, maximum=65536):
    if not isinstance(value, str) or len(value) > maximum:
        raise CredentialCryptoError()
    try:
        result = base64.b64decode(value, validate=True)
        if size is not None and len(result) != size:
            raise ValueError
        return result
    except Exception:
        raise CredentialCryptoError() from None


class LocalKeyProvider:
    kind = "local"

    def __init__(self, key, key_id):
        if not isinstance(key, bytes) or len(key) != 32 or not isinstance(key_id, str) or not key_id:
            raise ValueError("Invalid local KEK configuration")
        self._key, self.key_id = key, key_id

    def wrap(self, dek, aad):
        nonce = os.urandom(12)
        return dict(key_id=self.key_id, wrapped_dek=encode_bytes(AESGCM(self._key).encrypt(nonce, dek, aad)),
                    wrap_nonce=encode_bytes(nonce))

    def unwrap(self, envelope, aad):
        if envelope.get("key_id") != self.key_id:
            raise CredentialCryptoError()
        return AESGCM(self._key).decrypt(decode_bytes(envelope.get("wrap_nonce"), size=12),
                                         decode_bytes(envelope.get("wrapped_dek"), maximum=8192), aad)

    def close(self):
        pass


class CredentialCipher:
    def __init__(self, provider):
        self.provider = provider

    def bundle_aad(self, context_aad, key_id):
        if not isinstance(key_id, str) or not 1 <= len(key_id) <= 2048:
            raise CredentialCryptoError()
        header = json.dumps([1, self.provider.kind, key_id], separators=(",", ":"), ensure_ascii=True).encode()
        return b"credentials:" + context_aad + b":" + header

    def encrypt(self, credentials, context):
        if (not isinstance(credentials, dict) or not credentials or not all(
                isinstance(key, str) and isinstance(value, str) for key, value in credentials.items())):
            raise ValueError("Invalid credential bundle")
        plaintext = json.dumps(credentials, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        if len(plaintext) > 16384:
            raise ValueError("Credential bundle exceeds size limit")
        aad = context.aad()
        dek, nonce = os.urandom(32), os.urandom(12)
        wrapped = self.provider.wrap(dek, b"dek-wrap:" + aad)
        data = dict(format=1, provider=self.provider.kind, nonce=encode_bytes(nonce),
                    ciphertext=encode_bytes(AESGCM(dek).encrypt(nonce, plaintext, self.bundle_aad(aad, wrapped["key_id"]))), **wrapped)
        return CredentialEnvelope(data)

    def decrypt(self, envelope, context):
        data = envelope.data if isinstance(envelope, CredentialEnvelope) else envelope
        try:
            allowed = {"format", "provider", "key_id", "nonce", "ciphertext", "wrapped_dek"}
            if self.provider.kind == "local":
                allowed.add("wrap_nonce")
            if (not isinstance(data, dict) or set(data) != allowed or type(data["format"]) is not int
                    or data["format"] != 1 or data["provider"] != self.provider.kind):
                raise CredentialCryptoError()
            aad = context.aad()
            dek = self.provider.unwrap(data, b"dek-wrap:" + aad)
            if len(dek) != 32:
                raise CredentialCryptoError()
            plaintext = AESGCM(dek).decrypt(decode_bytes(data["nonce"], size=12), decode_bytes(data["ciphertext"]),
                                            self.bundle_aad(aad, data["key_id"]))
            if len(plaintext) > 16384:
                raise CredentialCryptoError()
            values = json.loads(plaintext)
            if not isinstance(values, dict) or not values or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
                raise CredentialCryptoError()
            return values
        except CredentialCryptoError:
            raise
        except Exception:
            raise CredentialCryptoError() from None

    def close(self):
        self.provider.close()


def credential_cipher(settings):
    if settings.source != "postgres_kms":
        raise ValueError("Encrypted credential provider requires managed source")
    if settings.provider == "local":
        return CredentialCipher(LocalKeyProvider(settings.local_key, settings.local_key_id))
    if settings.provider == "gcp":
        from hummingbot.core.gcp_key_provider import GcpKeyProvider
        return CredentialCipher(GcpKeyProvider(settings.gcp_key_resource))
    raise ValueError("Unsupported key provider")
