"""One process-level OKX trading environment, shared by both existing adapters."""
import os
from dataclasses import dataclass

from hummingbot.client.config.defaults import DEFAULT_ENV_CONFIG
from hummingbot.core.web_assistant.rest_pre_processors import RESTPreProcessorBase


@dataclass(frozen=True)
class OKXEnvironment:
    mode: str
    site: str

    @classmethod
    def load(cls, *, site=None, legacy_demo=False, environ=None):
        values = os.environ if environ is None else environ
        mode = values.get("OKX_TRADING_ENV", "demo" if legacy_demo else DEFAULT_ENV_CONFIG["OKX_TRADING_ENV"])
        configured_site = values.get("OKX_SITE") or values.get("HBOT_OKX_REGISTRATION_SUB_DOMAIN")
        if configured_site and site and configured_site != site:
            raise ValueError("OKX site conflicts with the connector's configured registration site")
        chosen_site = configured_site or site or DEFAULT_ENV_CONFIG["OKX_SITE"]
        if mode not in ("live", "demo") or chosen_site not in ("www", "app", "my"):
            raise ValueError("OKX_TRADING_ENV must be live/demo; OKX_SITE / okx_registration_sub_domain must be www/app/my")
        if legacy_demo and mode != "demo":
            raise ValueError("Legacy demo domain conflicts with OKX_TRADING_ENV")
        return cls(mode, chosen_site)

    @property
    def rest_url(self):
        return "https://" + {"www": "www", "app": "us", "my": "eea"}[self.site] + ".okx.com"

    def websocket_url(self, channel):
        host = {"www": "ws", "app": "wsus", "my": "wseea"}[self.site]
        if self.mode == "demo":
            host = "wspap" if self.site == "www" else host + "pap"
        return f"wss://{host}.okx.com/ws/v5/{channel}"

    @property
    def perpetual_domain(self):
        if self.site == "www":
            return "okx_perpetual_demo" if self.mode == "demo" else "okx_perpetual"
        return f"okx_perpetual_{self.site}" + ("_demo" if self.mode == "demo" else "")


class OKXEnvironmentHeaders(RESTPreProcessorBase):
    def __init__(self, environment):
        self.environment = environment

    async def pre_process(self, request):
        headers = {key: value for key, value in (request.headers or {}).items() if key.lower() != "x-simulated-trading"}
        if self.environment.mode == "demo":
            headers["x-simulated-trading"] = "1"
        request.headers = headers
        return request
