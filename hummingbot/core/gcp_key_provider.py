"""Cloud KMS with official Google Auth REST transport; no connector proto conflicts."""
import threading

from hummingbot.core.credential_encryption import CredentialCryptoError, decode_bytes, encode_bytes


def checksum(value):
    import google_crc32c
    return google_crc32c.value(value)


class GcpKeyProvider:
    kind = "gcp"

    def __init__(self, resource, *, client=None):
        self.resource, self._client = resource, client
        self._lock = threading.Lock()

    @property
    def client(self):
        with self._lock:
            if self._client is None:
                from hummingbot.core.gcp_kms_rest import GcpKmsRestClient
                self._client = GcpKmsRestClient()
            return self._client

    @staticmethod
    def failure(error):
        from google.auth.exceptions import DefaultCredentialsError, RefreshError
        status = getattr(error, "status_code", None)
        if isinstance(error, RefreshError) and error.retryable:
            return CredentialCryptoError("kms_unavailable")
        if status in (401, 403) or isinstance(error, (DefaultCredentialsError, RefreshError)):
            return CredentialCryptoError("kms_permission_denied")
        if status in (400, 404, 412):
            return CredentialCryptoError("kms_key_unavailable")
        return CredentialCryptoError("kms_unavailable")

    def valid_version(self, name):
        prefix = self.resource + "/cryptoKeyVersions/"
        return isinstance(name, str) and name.startswith(prefix) and name[len(prefix):].isascii() and name[len(prefix):].isdigit()

    def wrap(self, dek, aad):
        try:
            response = self.client.encrypt(request=dict(name=self.resource, plaintext=dek, additional_authenticated_data=aad,
                                                        plaintext_crc32c=checksum(dek), additional_authenticated_data_crc32c=checksum(aad)),
                                           timeout=5)
            if (not response.verified_plaintext_crc32c or not response.verified_additional_authenticated_data_crc32c
                    or response.ciphertext_crc32c != checksum(response.ciphertext) or not self.valid_version(response.name)):
                raise CredentialCryptoError("kms_unavailable")
            return dict(key_id=response.name, wrapped_dek=encode_bytes(response.ciphertext))
        except CredentialCryptoError:
            raise
        except Exception as error:
            raise self.failure(error) from None

    def unwrap(self, envelope, aad):
        if not self.valid_version(envelope.get("key_id")):
            raise CredentialCryptoError()
        ciphertext = decode_bytes(envelope.get("wrapped_dek"), maximum=8192)
        try:
            response = self.client.decrypt(request=dict(name=self.resource, ciphertext=ciphertext, additional_authenticated_data=aad,
                                                        ciphertext_crc32c=checksum(ciphertext), additional_authenticated_data_crc32c=checksum(aad)),
                                           timeout=5)
            if response.plaintext_crc32c != checksum(response.plaintext):
                raise CredentialCryptoError("kms_unavailable")
            return response.plaintext
        except CredentialCryptoError:
            raise
        except Exception as error:
            raise self.failure(error) from None

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None
