import base64
import json
from test.hummingbot.core.test_credential_encryption import CONTEXT, SECRETS
from test.hummingbot.core.test_gcp_key_provider import RESOURCE, OfflineKms

import pytest

from hummingbot.core.credential_encryption import CredentialCipher
from hummingbot.core.gcp_key_provider import GcpKeyProvider
from hummingbot.core.gcp_kms_rest import GcpKmsRestClient, KmsRequestError


class FakeResponse:
    status_code = 200

    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def iter_content(self, size):
        yield json.dumps(self.data).encode()


class FakeAuthorizedSession:
    def __init__(self):
        self.remote, self.calls = OfflineKms(), []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        body = kwargs["json"]
        operation = url.rsplit(":", 1)[1]
        field = "plaintext" if operation == "encrypt" else "ciphertext"
        assert set(body) == {field, field + "Crc32c", "additionalAuthenticatedData", "additionalAuthenticatedDataCrc32c"}
        request = {"name": RESOURCE, field: base64.b64decode(body[field]),
                   field + "_crc32c": int(body[field + "Crc32c"]),
                   "additional_authenticated_data": base64.b64decode(body["additionalAuthenticatedData"]),
                   "additional_authenticated_data_crc32c": int(body["additionalAuthenticatedDataCrc32c"])}
        response = getattr(self.remote, operation)(request=request, timeout=5)
        if operation == "encrypt":
            return FakeResponse(dict(name=response.name, ciphertext=base64.b64encode(response.ciphertext).decode(),
                                     ciphertextCrc32c=str(response.ciphertext_crc32c), verifiedPlaintextCrc32c=True,
                                     verifiedAdditionalAuthenticatedDataCrc32c=True))
        return FakeResponse(dict(plaintext=base64.b64encode(response.plaintext).decode(), plaintextCrc32c=str(response.plaintext_crc32c)))

    def close(self):
        pass


def test_authorized_rest_roundtrip_without_loading_cloud_kms_protobuf_sdk():
    # Load the real API/connector registry first: that exposed the descriptor collision.
    from hummingbot.cli.admin_api import AdminAPI
    assert AdminAPI
    session = FakeAuthorizedSession()
    service = CredentialCipher(GcpKeyProvider(RESOURCE, client=GcpKmsRestClient(session=session)))
    assert service.decrypt(service.encrypt(SECRETS, CONTEXT), CONTEXT) == SECRETS
    assert [url for url, _ in session.calls] == ["https://cloudkms.googleapis.com/v1/" + RESOURCE + ":encrypt",
                                                 "https://cloudkms.googleapis.com/v1/" + RESOURCE + ":decrypt"]
    assert all(item["timeout"] == 5 and item["max_allowed_time"] == 8 and not item["allow_redirects"] for _, item in session.calls)


def test_rest_rejection_does_not_read_or_return_raw_response_body():
    response = FakeResponse({"error": "CANARY-PRIVATE-ERROR"})
    response.status_code = 403
    response.iter_content = lambda size: (_ for _ in ()).throw(AssertionError("Do not inspect private error body"))
    session = FakeAuthorizedSession()
    session.post = lambda *args, **kwargs: response
    client = GcpKmsRestClient(session=session)
    with pytest.raises(KmsRequestError) as error:
        client.encrypt(request={"name": RESOURCE, "plaintext": b"data", "plaintext_crc32c": 0,
                                "additional_authenticated_data": b"context", "additional_authenticated_data_crc32c": 0})
    assert error.value.status_code == 403 and "CANARY" not in str(error.value)
