"""Explicit transport-only seam for isolated container tests with dummy keys."""
import os
from urllib.parse import urlsplit, urlunsplit


def endpoint(url: str) -> str:
    origin = os.environ.get("HBOT_OFFLINE_HTTP_ORIGIN")
    if origin is None:
        return url
    if "K_SERVICE" in os.environ:
        raise ValueError("Offline transport overrides are forbidden on Cloud Run.")
    if "DATABASE_URL" not in os.environ:
        raise ValueError("Offline transport requires environment credential mode.")
    parsed = urlsplit(origin)
    if (parsed.scheme != "http" or parsed.hostname not in ("exchange", "127.0.0.1", "localhost")
            or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ValueError("Offline transport requires an isolated local HTTP origin.")
    for prefix in ("HBOT_OKX_", "HBOT_OKX_PERPETUAL_", "HBOT_OKX_DEMO_", "HBOT_OKX_PERPETUAL_DEMO_"):
        for field, dummy in (("API_KEY", "offline-key"), ("SECRET_KEY", "offline-secret"), ("PASSPHRASE", "offline-pass")):
            if prefix + field in os.environ and os.environ[prefix + field] != dummy:
                raise ValueError("Offline transport accepts only the public fixture credentials.")
    requested = urlsplit(url)
    scheme = "ws" if requested.scheme in ("ws", "wss") else "http"
    return urlunsplit((scheme, parsed.netloc, requested.path, requested.query, requested.fragment))
