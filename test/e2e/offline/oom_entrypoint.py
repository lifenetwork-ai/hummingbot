"""Test-only cgroup OOM in a shipping supervisor/API/engine process."""
import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path.cwd()))

ROLE = sys.argv[sys.argv.index('--role') + 1] if '--role' in sys.argv else 'supervisor'
REQUEST = Path('/tmp/oom-' + ROLE + '.request')
ARMED = Path('/tmp/oom-' + ROLE + '-armed.json')
RELEASE = Path('/tmp/oom-' + ROLE + '.release')
wake, lock = threading.Event(), threading.Lock()


def consume(boundary, **identity):
    with lock:
        if not REQUEST.exists() or json.loads(REQUEST.read_text())['boundary'] != boundary:
            return False
        REQUEST.unlink()
        ARMED.write_text(json.dumps(dict(pid=os.getpid(), role=ROLE, boundary=boundary, **identity)))
        wake.set()
        return True


def pressure():
    while not wake.wait(.05):
        consume('now')
    # Refuse an unbounded/host allocator. No privileged cgroup or VM changes.
    limit = int(Path('/sys/fs/cgroup/memory.max').read_text())
    assert 64 * 1024**2 <= limit <= 2 * 1024**3
    deadline = time.monotonic() + 15
    while not RELEASE.exists():
        assert time.monotonic() < deadline, 'Parent never released the OOM barrier'
        time.sleep(.02)
    RELEASE.unlink()
    Path('/proc/self/oom_score_adj').write_text('1000')
    chunks = []
    for _ in range((limit * 2) // (8 * 1024**2)):
        chunk = bytearray(8 * 1024**2)
        for offset in range(0, len(chunk), 4096):
            chunk[offset] = 1  # Commit physical pages, not just virtual address space.
        chunks.append(chunk)
        time.sleep(.01)
    raise AssertionError('Allocator reached its cap without a kernel OOM kill')


def main():
    from hummingbot.cli import service, service_processes
    threading.Thread(target=pressure, daemon=True).start()
    if ROLE == 'supervisor':
        service_processes.child_command = lambda role: [sys.executable, __file__, '--role', role]
    else:
        from hummingbot.core.web_assistant.connections.rest_connection import RESTConnection
        original = RESTConnection.call

        async def call(self, request):
            response = await original(self, request)
            if (request.method.value == 'POST' and urlsplit(request.url).path == '/api/v5/trade/order'
                    and consume('create', order=json.loads(request.data)['clOrdId'])):
                await asyncio.Event().wait()  # Accepted at venue, not acknowledged by connector.
            return response

        RESTConnection.call = call
        from sqlalchemy import event
        from sqlalchemy.orm import Session

        from hummingbot.model.trade_fill import TradeFill

        def after_flush(session, context):
            trade = next((row for row in session.new if isinstance(row, TradeFill)), None)
            if trade is not None and consume('fill', order=trade.order_id, trade=trade.exchange_trade_id):
                threading.Event().wait(20)  # SQL has flushed; COMMIT must not happen.

        event.listen(Session, 'after_flush', after_flush)
    service.main()


if __name__ == '__main__':
    main()
