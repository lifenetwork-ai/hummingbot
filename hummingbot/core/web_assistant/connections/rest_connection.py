from typing import Awaitable, Callable, Optional

import aiohttp

from hummingbot.core.web_assistant.connections.data_types import RESTRequest, RESTResponse
from hummingbot.core.web_assistant.connections.request_guard import RESTRequestSendGuard, RESTRequestSnapshot


class RESTConnection:
    def __init__(self, aiohttp_client_session: aiohttp.ClientSession):
        self._client_session = aiohttp_client_session

    async def call(self, request: RESTRequest,
                   pre_send_check: Optional[Callable[[], None]] = None,
                   async_pre_send_check: Optional[Callable[[], Awaitable[None]]] = None,
                   request_send_guard: Optional[RESTRequestSendGuard] = None) -> RESTResponse:
        # Freeze the actual post-processor/auth result, including the serialized
        # body and signed headers. Callback closures cannot substitute a second
        # mutable dictionary for the request actually passed to aiohttp.
        snapshot = None if request_send_guard is None else RESTRequestSnapshot.capture(request)
        # Both gates run after throttling/authentication. The synchronous gate
        # follows the last authorization await, so local stop/expiry can revoke
        # a DB result before the first request await. DB/exchange are not atomic.
        if async_pre_send_check is not None:
            await async_pre_send_check()
        if request_send_guard is not None and await request_send_guard.authorize(snapshot) is not True:
            raise PermissionError('REQUEST_AUTHORIZATION_DENIED')
        # A synchronous check here runs after RESTAssistant's throttler and
        # immediately before the first network await.
        if pre_send_check is not None:
            pre_send_check()
        if request_send_guard is not None and request_send_guard.check(snapshot) is not True:
            raise PermissionError('REQUEST_FINAL_CHECK_DENIED')
        aiohttp_resp = await self._client_session.request(
            method=request.method.value if snapshot is None else snapshot.method.value,
            url=request.url if snapshot is None else snapshot.url,
            params=request.params if snapshot is None else dict(snapshot.params) or None,
            data=(request.data if snapshot is None else
                  None if snapshot.data is None else snapshot.data.encode('utf-8')),
            headers=request.headers if snapshot is None else dict(snapshot.headers) or None,
            # A redirect must not carry the signed order/credentials to a new
            # endpoint without a new authorization (which is one-use).
            **({"allow_redirects": False} if snapshot is not None else {}),
        )

        resp = await self._build_resp(aiohttp_resp)
        return resp

    @staticmethod
    async def _build_resp(aiohttp_resp: aiohttp.ClientResponse) -> RESTResponse:
        resp = RESTResponse(aiohttp_resp)
        return resp
