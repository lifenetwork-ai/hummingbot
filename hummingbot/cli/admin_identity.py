"""Bounded Google verification; no session tokens or cached account permissions."""
import asyncio
import contextvars
import functools
import json
import re
import threading
import time
from types import SimpleNamespace

from sqlalchemy.exc import SQLAlchemyError

from hummingbot.cli.admin_database import AdminDatabaseExecutor, DatabaseBusy
from hummingbot.core.admin_identity import AccessDenied

CERTIFICATES_URL = "https://www.googleapis.com/oauth2/v1/certs"


class GoogleCertificates:
    """One bounded public-key response, honoring expiry without stale fallback."""

    def __init__(self):
        import requests
        self.session = requests.Session()
        self.lock = threading.Lock()
        self.response, self.expires = None, 0

    def __call__(self, url, method="GET", **kwargs):
        from google.auth.exceptions import TransportError
        if url != CERTIFICATES_URL or method != "GET":
            raise TransportError("Unreviewed Google verification request")
        with self.lock:
            if self.response is not None and time.monotonic() < self.expires:
                return self.response
            try:
                with self.session.get(url, timeout=(2, 3), stream=True, allow_redirects=False) as response:
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_content(8192):
                        content.extend(chunk)
                        if len(content) > 131072:
                            raise ValueError("Certificate response exceeded bound")
                    certs = json.loads(content)
                    if not isinstance(certs, dict) or not 1 <= len(certs) <= 20 or not all(
                            isinstance(k, str) and isinstance(v, str) for k, v in certs.items()):
                        raise ValueError("Invalid certificate response")
                    match = re.search(r"(?:^|,)\s*max-age=(\d+)", response.headers.get("Cache-Control", ""))
                    ttl = min(3600, int(match.group(1))) if match else 0
                    self.response = SimpleNamespace(status=200, data=bytes(content))
                    self.expires = time.monotonic() + ttl
                    return self.response
            except Exception:
                raise TransportError("Google signing keys are unavailable") from None

    def close(self):
        self.session.close()
        self.response = None


class GoogleTokenVerifier:
    def __init__(self, audience, *, certificates=None):
        if not audience:
            raise ValueError("A Google audience is required")
        self.audience = audience
        # Tests inject signing certificates, never a bypass of signature validation.
        self.transport = (lambda *args, **kwargs: SimpleNamespace(status=200, data=json.dumps(certificates).encode())) \
            if certificates is not None else GoogleCertificates()

    def verify(self, token):
        from google.auth import exceptions
        from google.oauth2.id_token import verify_oauth2_token
        if not isinstance(token, str) or not 1 <= len(token) <= 16384:
            raise AccessDenied()
        try:
            claims = verify_oauth2_token(token, self.transport, audience=self.audience)
        except exceptions.TransportError:
            raise AccessDenied("identity_provider_unavailable", status=503) from None
        except (exceptions.GoogleAuthError, ValueError, TypeError, KeyError) as error:
            code = "token_expired" if isinstance(error, exceptions.InvalidValue) and str(error).startswith("Token expired,") else "authentication_required"
            raise AccessDenied(code) from None
        email, subject = claims.get("email"), claims.get("sub")
        if (claims.get("email_verified") is not True or not isinstance(subject, str) or not 1 <= len(subject) <= 255
                or not isinstance(email, str) or not 3 <= len(email) <= 254 or email.count("@") != 1):
            raise AccessDenied()
        if email.rsplit("@", 1)[1].casefold() != "gmail.com" and not (
                isinstance(claims.get("hd"), str) and claims["hd"]):
            raise AccessDenied("account_not_allowed", status=403)
        return {"iss": "https://accounts.google.com", "sub": subject, "email": email.casefold()}

    def close(self):
        if hasattr(self.transport, "close"):
            self.transport.close()


class AdminIdentityService:
    def __init__(self, api, *, verifier=None):
        self.api = api
        self.verifier = verifier or GoogleTokenVerifier(api.settings.google_client_id)
        # Reuse bounded scheduling; controls have their own reserved execution slot.
        self.work = AdminDatabaseExecutor()

    async def authenticate(self, token, *, control=False, bind=False, request_id=None):
        def verify_and_resolve():
            claims = self.verifier.verify(token)
            try:
                return self.api.access_store().resolve_google(claims, bind=bind, request_id=request_id,
                                                              bootstrap_enabled=self.api.settings.basic_auth_enabled)
            except AccessDenied as error:
                if error.actor is None:
                    from hummingbot.core.admin_identity import Principal
                    error.actor = Principal("google:" + claims["sub"], "google", "unassigned", email=claims["email"])
                raise
        try:
            # Fresh role lookup shares the existing bounded identity worker, not
            # reporting admission. Keep the separate control slot and DB pool limits.
            job = self.work.submit(functools.partial(contextvars.copy_context().run, verify_and_resolve),
                                   control=control, heavy=not control)
            return await asyncio.wait_for(job, timeout=6)
        except DatabaseBusy:
            raise AccessDenied("security_work_busy", status=429) from None
        except (SQLAlchemyError, asyncio.TimeoutError):
            raise AccessDenied("database_unavailable", status=503) from None

    async def close(self):
        await self.work.close()
        self.verifier.close()
