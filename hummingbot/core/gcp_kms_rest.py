"""Cloud KMS REST using official Google Auth ADC/AuthorizedSession.

Avoid loading protobuf descriptors: existing connector SDKs vendor overlapping
Google proto definitions. Only encrypted DEK operations cross this boundary.
"""
from types import SimpleNamespace

from hummingbot.core.credential_encryption import decode_bytes, encode_bytes


class KmsRequestError(RuntimeError):
    def __init__(self, status_code):
        super().__init__("kms_request_failed")
        self.status_code = status_code


class GcpKmsRestClient:
    def __init__(self, *, session=None):
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession, Request

            class BoundedRequest(Request):
                def __call__(self, url, **kwargs):
                    timeout = kwargs.get("timeout")
                    kwargs["timeout"] = min(float(timeout), 5) if timeout is not None else 5
                    return super().__call__(url, **kwargs)

            credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloudkms"], request=BoundedRequest())
            session = AuthorizedSession(credentials, refresh_timeout=5, max_refresh_attempts=1)
        self.session = session

    def call(self, operation, request, *, timeout):
        name = request["name"]
        if not isinstance(name, str) or not name.startswith("projects/") or any(char in name for char in ("?", "#", "..")):
            raise KmsRequestError(400)
        body = {"additionalAuthenticatedData": encode_bytes(request["additional_authenticated_data"]),
                "additionalAuthenticatedDataCrc32c": str(request["additional_authenticated_data_crc32c"])}
        field = "plaintext" if operation == "encrypt" else "ciphertext"
        body[field] = encode_bytes(request[field])
        body[field + "Crc32c"] = str(request[field + "_crc32c"])
        with self.session.post("https://cloudkms.googleapis.com/v1/" + name + ":" + operation,
                               json=body, timeout=timeout, max_allowed_time=8, allow_redirects=False, stream=True) as response:
            if response.status_code != 200:
                raise KmsRequestError(response.status_code)
            content = bytearray()
            for chunk in response.iter_content(8192):
                content.extend(chunk)
                if len(content) > 65536:
                    raise KmsRequestError(502)
            import json
            value = json.loads(content)
        if operation == "encrypt":
            return SimpleNamespace(name=value["name"], ciphertext=decode_bytes(value["ciphertext"]),
                                   ciphertext_crc32c=int(value["ciphertextCrc32c"]),
                                   verified_plaintext_crc32c=value.get("verifiedPlaintextCrc32c") is True,
                                   verified_additional_authenticated_data_crc32c=value.get("verifiedAdditionalAuthenticatedDataCrc32c") is True)
        return SimpleNamespace(plaintext=decode_bytes(value["plaintext"]), plaintext_crc32c=int(value["plaintextCrc32c"]))

    def encrypt(self, *, request, timeout=5):
        return self.call("encrypt", request, timeout=timeout)

    def decrypt(self, *, request, timeout=5):
        return self.call("decrypt", request, timeout=timeout)

    def close(self):
        self.session.close()
