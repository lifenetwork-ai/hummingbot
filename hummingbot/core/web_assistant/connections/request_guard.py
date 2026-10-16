"""Optional final HTTP guards over an immutable, post-authentication request.

The auth flag and headers describe local processing, not remote account identity
or write exclusion. These guards cannot make database and exchange commits atomic.
"""
from dataclasses import dataclass, field
from typing import Protocol

from hummingbot.core.web_assistant.connections.data_types import RESTMethod, RESTRequest


@dataclass(frozen=True)
class RESTRequestSnapshot:
    method: RESTMethod
    url: str
    params: tuple[tuple[str, str], ...]
    data: str | None = field(repr=False)
    headers: tuple[tuple[str, str], ...] = field(repr=False)
    is_auth_required: bool

    @classmethod
    def capture(cls, request: RESTRequest):
        def pairs(value, *, headers=False):
            if value is None:
                return ()
            result = tuple(value.items())
            if any(type(k) is not str or type(v) is not str for k, v in result):
                raise ValueError('REQUEST_SNAPSHOT_INVALID')
            keys = [key.lower() if headers else key for key, _ in result]
            if len(set(keys)) != len(keys):
                raise ValueError('REQUEST_SNAPSHOT_INVALID')
            return result

        if (type(request.method) is not RESTMethod or type(request.url) is not str or not request.url
                or request.data is not None and type(request.data) is not str
                or type(request.is_auth_required) is not bool):
            raise ValueError('REQUEST_SNAPSHOT_INVALID')
        return cls(request.method, request.url, pairs(request.params), request.data,
                   pairs(request.headers, headers=True), request.is_auth_required)


class RESTRequestSendGuard(Protocol):
    async def authorize(self, request: RESTRequestSnapshot) -> bool:
        ...

    def check(self, request: RESTRequestSnapshot) -> bool:
        """Final synchronous one-use fence. Only literal True admits transport."""
        ...
