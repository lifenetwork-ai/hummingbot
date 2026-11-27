import asyncio
from test.isolated_asyncio_wrapper_test_case import IsolatedAsyncioWrapperTestCase

import hummingbot.core.utils.tracking_nonce as tracking_nonce


class TrackingNonceTest(IsolatedAsyncioWrapperTestCase):

    def test_get_tracking_nonce(self):
        nonce = tracking_nonce.get_tracking_nonce()
        self.assertIsNotNone(nonce)
        new_nonce = tracking_nonce.get_tracking_nonce()
        self.assertGreater(new_nonce, nonce)

    def test_get_low_res_tracking_nonce(self):
        nonce = tracking_nonce.get_tracking_nonce_low_res()
        self.assertIsNotNone(nonce)
        new_nonce = tracking_nonce.get_tracking_nonce_low_res()
        self.assertGreater(new_nonce, nonce)

    async def test_get_concurrent_nonce_in_low_res(self):
        async def task():
            return tracking_nonce.get_tracking_nonce_low_res()
        ret = await asyncio.gather(task(), task())
        self.assertGreaterEqual(ret[1], ret[0])

    async def test_get_concurrent_nonce_in_high_res(self):
        async def task():
            return tracking_nonce.get_tracking_nonce()
        ret = await asyncio.gather(task(), task())
        self.assertGreaterEqual(ret[1], ret[0])
