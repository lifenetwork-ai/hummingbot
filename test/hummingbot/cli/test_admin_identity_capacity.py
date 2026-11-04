import asyncio
import threading
from types import SimpleNamespace

import pytest

from hummingbot.cli.admin_identity import AdminIdentityService, GoogleCertificates
from hummingbot.core.admin_identity import Principal


@pytest.mark.asyncio
async def test_slow_reader_verification_does_not_take_control_verification_slot():
    started, release = threading.Event(), threading.Event()

    class Verifier:
        def verify(self, token):
            if token == "reader":
                started.set()
                release.wait(3)
            return {"sub": token, "email": "boss@gmail.com"}

        def close(self):
            pass

    async def lookup(function, *args, **kwargs):
        return function(*args, **kwargs)

    principal = Principal("user-1", "google", "write")
    store = SimpleNamespace(resolve_google=lambda *args, **kwargs: principal)
    api = SimpleNamespace(access_store=lambda: store, run_database=lookup, run_control_database=lookup,
                          settings=SimpleNamespace(basic_auth_enabled=True))
    service = AdminIdentityService(api, verifier=Verifier())
    reader = asyncio.create_task(service.authenticate("reader"))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        assert await asyncio.wait_for(service.authenticate("control", control=True), .5) == principal
        assert not reader.done()
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)
        # A cancelled HTTP waiter cannot release a running physical verifier.
        assert service.work.active == 1
    finally:
        release.set()
        await service.close()


def test_google_certificate_cache_is_bounded_and_does_not_use_expired_keys_on_failure(monkeypatch):
    import requests
    from google.auth.exceptions import TransportError

    calls = []
    now = [10]
    monkeypatch.setattr("hummingbot.cli.admin_identity.time.monotonic", lambda: now[0])

    class Response:
        headers = {"Cache-Control": "public, max-age=20"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            yield b'{"certificate-id":"certificate-value"}'

    def fetch(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) > 1:
            raise requests.Timeout("private transport exception")
        return Response()

    cache = GoogleCertificates()
    monkeypatch.setattr(cache.session, "get", fetch)
    try:
        first = cache("https://www.googleapis.com/oauth2/v1/certs")
        assert cache("https://www.googleapis.com/oauth2/v1/certs") is first
        assert len(calls) == 1 and calls[0]["timeout"] == (2, 3)
        now[0] = 31
        with pytest.raises(TransportError, match="unavailable") as error:
            cache("https://www.googleapis.com/oauth2/v1/certs")
        assert "private transport" not in str(error.value)
    finally:
        cache.close()
