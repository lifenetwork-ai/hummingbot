import json
from test.hummingbot.cli.test_admin_identity import signed_identity as signature_fixture
from test.integration.postgres.test_service import eventually, health, start_service, terminate
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from aiohttp import BasicAuth

signed_identity = signature_fixture


def test_real_service_restart_disables_password_but_preserves_google_admin(pg_engine, tmp_path, monkeypatch, signed_identity):
    token, certs = signed_identity
    certificate_path = tmp_path / "public-signing-certificates.json"
    certificate_path.write_text(json.dumps(certs))
    monkeypatch.setenv("HBOT_OFFLINE_GOOGLE_CERTIFICATES", str(certificate_path))
    monkeypatch.setenv("ADMIN_GOOGLE_AUTH_ENABLED", "true")
    monkeypatch.setenv("ADMIN_GOOGLE_CLIENT_ID", "offline-client")
    basic = BasicAuth("operator", "offline-admin").encode()
    google = "Bearer " + token()

    def call(port, path, authorization, body=None):
        headers = {"Authorization": authorization}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(f"http://127.0.0.1:{port}/api/v1/admin{path}", headers=headers,
                          data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    first, port, log = start_service(tmp_path / "bootstrap", admin_enabled=True)
    try:
        eventually(lambda: health(port, "/startup")[0] == 200)
        status, account = call(port, "/access/accounts", basic, {"email": "boss@gmail.com", "role": "admin"})
        assert status == 201
        status, actor = call(port, "/access/login", google, {})
        assert status == 200 and actor["id"] == account["id"]
    finally:
        terminate(first)
        log.close()
    second, port, log = start_service(tmp_path / "google-only", admin_enabled=True, basic_enabled=False)
    try:
        eventually(lambda: health(port, "/startup")[0] == 200)
        status, denial = call(port, "/access/me", basic)
        assert status == 401 and denial["code"] == "basic_auth_disabled"
        status, actor = call(port, "/access/me", google)
        assert status == 200 and actor["id"] == account["id"] and actor["role"] == "admin"
        assert health(port, "/live")[0] == 200
    finally:
        terminate(second)
        log.close()
