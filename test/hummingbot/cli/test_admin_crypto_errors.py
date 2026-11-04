from test.hummingbot.core.test_credential_encryption import CONTEXT, SECRETS, cipher
from test.hummingbot.core.test_gcp_key_provider import RESOURCE, OfflineKms

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from hummingbot.cli.admin_api import ADMIN_PREFIX, admin_errors
from hummingbot.core.credential_encryption import CredentialCipher
from hummingbot.core.gcp_key_provider import GcpKeyProvider
from hummingbot.core.gcp_kms_rest import KmsRequestError


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,code", [("permission", "kms_permission_denied"), ("ciphertext", "credential_decryption_failed")])
async def test_provider_failures_reach_http_as_safe_actionable_errors(kind, code, caplog):
    app = web.Application(middlewares=[admin_errors])

    async def failed(request):
        if kind == "permission":
            client = OfflineKms()
            client.failure = KmsRequestError(403)
            CredentialCipher(GcpKeyProvider(RESOURCE, client=client)).encrypt(SECRETS, CONTEXT)
        else:
            service = cipher()
            envelope = service.encrypt(SECRETS, CONTEXT)
            service.decrypt({**envelope.data, "ciphertext": "CANARY-PRIVATE-CIPHERTEXT"}, CONTEXT)
        raise AssertionError("Provider failure did not fail closed")

    app.router.add_get(ADMIN_PREFIX + "/provider-test", failed)
    async with TestClient(TestServer(app)) as client:
        response = await client.get(ADMIN_PREFIX + "/provider-test")
        body = await response.json()
        assert response.status == 503 and body["code"] == code
        assert body["request_id"] and body["detail"] and body["retry"] == "contact_support"
        assert all(value not in str(body) for value in (*SECRETS.values(), "CANARY-PRIVATE-SDK-ERROR", "CANARY-PRIVATE-CIPHERTEXT"))
        assert all(value not in caplog.text for value in (*SECRETS.values(), "CANARY-PRIVATE-CIPHERTEXT"))
