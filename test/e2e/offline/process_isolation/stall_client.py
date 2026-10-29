import asyncio
import json
import statistics
import time
from pathlib import Path

import aiohttp


async def main():
    auth = aiohttp.BasicAuth('operator', 'offline-admin')
    async with aiohttp.ClientSession(auth=auth, timeout=aiohttp.ClientTimeout(total=6)) as client:
        async with client.post('http://bot:8081/diagnostic/stall') as response:
            assert response.status == 200
        origin = time.monotonic()
        results = []

        async def read(index):
            due = origin + index / 20
            await asyncio.sleep(max(0, due - time.monotonic()))
            try:
                async with client.get('http://bot:8080/api/v1/admin/status') as response:
                    body = await response.json()
                    results.append(dict(index=index, elapsed_ms=(time.monotonic() - due) * 1000,
                                        status=response.status, owner_available=body.get('owner_available'), phase=body.get('phase')))
            except Exception as error:
                results.append(dict(index=index, error=type(error).__name__, elapsed_ms=(time.monotonic() - due) * 1000))
        await asyncio.gather(*(read(i) for i in range(100)))
        values = sorted(row['elapsed_ms'] for row in results)
        result = dict(injected_loop_stall_seconds=2.5, requests=100, errors=sum(row.get('status') != 200 for row in results),
                      p50_ms=statistics.median(values), p99_ms=values[98], max_ms=max(values), samples=results)
        Path('/tmp/stall-result.json').write_text(json.dumps(result))
        print(json.dumps({k: v for k, v in result.items() if k != 'samples'}))


asyncio.run(main())
