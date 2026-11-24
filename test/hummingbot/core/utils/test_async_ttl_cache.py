import asyncio
import time
import unittest
from weakref import ref

from hummingbot.core.utils import async_ttl_cache


class AsyncTTLCacheUnitTest(unittest.TestCase):

    @async_ttl_cache(ttl=3, maxsize=1)
    async def get_timestamp(self):
        return time.time()

    def test_async_ttl_cache(self):
        ret_1 = asyncio.get_event_loop().run_until_complete(self.get_timestamp())
        ret_2 = asyncio.get_event_loop().run_until_complete(self.get_timestamp())
        self.assertEqual(ret_1, ret_2)
        time.sleep(2)
        ret_3 = asyncio.get_event_loop().run_until_complete(self.get_timestamp())
        self.assertEqual(ret_2, ret_3)
        time.sleep(2)
        ret_4 = asyncio.get_event_loop().run_until_complete(self.get_timestamp())
        self.assertGreater(ret_4, ret_3)

    def test_reused_instance_representation_does_not_reuse_cached_result(self):
        class Source:
            def __init__(self, price):
                self.price = price

            def __repr__(self):
                return 'Source at a reused address'

            @async_ttl_cache()
            async def get_price(self):
                return self.price

        async def read():
            original = Source(10)
            self.assertEqual(10, await original.get_price())
            original_ref = ref(original)
            del original
            self.assertIsNone(original_ref())

            replacement = Source(20)
            self.assertEqual(20, await replacement.get_price())
            replacement.price = 30
            self.assertEqual(20, await replacement.get_price())

        asyncio.get_event_loop().run_until_complete(read())
