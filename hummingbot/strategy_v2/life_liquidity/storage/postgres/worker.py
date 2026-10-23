"""Managed foreground process, bounded probes and a physical shutdown deadline.

This process consumes injected environment only. No backend/bootstrap/activation
is inferred. Existing headless launch owns migration, native service and SDKs.
"""
import asyncio
import json
import math
import os
import re
import signal
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from time import monotonic


def event(code, **values):
    # Only fixed reason codes and a bounded primitive allowlist are emitted.
    print(json.dumps(dict(component='life_worker', event=code, **values), sort_keys=True, separators=(',', ':')), flush=True)


@dataclass(frozen=True)
class WorkerConfig:
    config_path: str
    source_factory: str
    tick_seconds: float
    startup_timeout_s: float
    shutdown_timeout_s: float
    status_host: str
    status_port: int
    telemetry_interval_s: float

    def __post_init__(self):
        if (type(self.config_path) is not str or not Path(self.config_path).is_absolute() or len(self.config_path) > 4096
                or type(self.source_factory) is not str or re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z]\w*', self.source_factory) is None
                or self.status_host not in ('127.0.0.1', '0.0.0.0') or type(self.status_port) is not int or not 0 <= self.status_port < 65536
                or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in (
                    self.tick_seconds, self.startup_timeout_s, self.shutdown_timeout_s, self.telemetry_interval_s))
                or self.tick_seconds > 10 or self.startup_timeout_s > 600 or not self.shutdown_timeout_s < 9
                or self.telemetry_interval_s > 300):
            raise ValueError('WORKER_CONFIG_INVALID')

    @classmethod
    def from_environment(cls, env):
        try:
            # No implicit repository .env access in a managed release.
            if env['LIFE_ENV_FILE'] != '':
                raise ValueError()
            return cls(env['LIFE_SERVICE_CONFIG'], env['LIFE_SOURCE_FACTORY'], float(env['LIFE_TICK_SECONDS']),
                       float(env['LIFE_STARTUP_TIMEOUT_SECONDS']), float(env['LIFE_SHUTDOWN_TIMEOUT_SECONDS']),
                       env['LIFE_STATUS_HOST'], int(env['LIFE_STATUS_PORT']), float(env['LIFE_TELEMETRY_INTERVAL_SECONDS']))
        except (KeyError, TypeError, ValueError, OverflowError):
            raise ValueError('WORKER_CONFIG_INVALID') from None


class WorkerSupervisor:
    def __init__(self, config, *, launch=None):
        if type(config) is not WorkerConfig:
            raise ValueError('WORKER_CONFIG_INVALID')
        self.config, self.launch = config, launch
        self.stop, self.finished = asyncio.Event(), threading.Event()
        self.service, self.service_task, self.server = None, None, None
        self.ready = asyncio.Event()
        self.started, self.loop_lag_ms, self._clients = False, 0.0, set()
        self._sampling, self._stop_waiter, self._deadline = None, None, None
        self._started_at = monotonic()

    def request_stop(self):
        if self.service is not None:
            self.service.revoke()  # Synchronous; cannot wait behind SQL or SDK IO.
        if not self.stop.is_set():
            event('STOPPING')
            self.stop.set()

    async def _started(self, service):
        self.service, self.started = service, True
        if self.stop.is_set():
            service.revoke()
        self.ready.set()
        event('STARTED', trading_permission=False)

    def status(self):
        reason, database_healthy, ready = 'STATUS_CURRENT', False, False
        try:
            runtime = None if self.service is None else self.service.runtime
            database_healthy = bool(runtime and runtime.healthy)
            ready = bool(self.service and not self.stop.is_set() and self.service._trading_ready())
        except Exception:
            reason, database_healthy, ready = 'STATUS_UNAVAILABLE', False, False
        return dict(started=self.started, stopping=self.stop.is_set(), database_healthy=database_healthy, reason_code=reason,
                    local_trading_ready=ready,
                    trading_permission=False, loop_lag_ms=round(self.loop_lag_ms, 3),
                    uptime_seconds=round(monotonic() - self._started_at, 3))

    async def _startup_deadline(self):
        try:
            async with asyncio.timeout(self.config.startup_timeout_s):
                await self.ready.wait()
        except TimeoutError:
            self.request_stop()
            raise RuntimeError('WORKER_STARTUP_TIMEOUT') from None

    async def _sample(self):
        delay = self.config.telemetry_interval_s
        while True:
            before = monotonic()
            await asyncio.sleep(delay)
            self.loop_lag_ms = max(0.0, (monotonic() - before - delay) * 1000)
            event('STATUS', **self.status())

    async def _client(self, reader, writer):
        task = asyncio.current_task()
        if len(self._clients) >= 32:
            writer.close()
            return
        self._clients.add(task)
        try:
            async with asyncio.timeout(1):
                raw = await reader.readuntil(b'\r\n\r\n')
                if len(raw) > 4096:
                    return
                line = raw.split(b'\r\n', 1)[0]
                code, body = '404 Not Found', b'{}'
                if line in (b'GET /live HTTP/1.1', b'GET /live HTTP/1.0'):
                    # A deliberately unarmed process is live. Startup failure
                    # exits; DB/trading health never triggers a restart loop.
                    code, body = '200 OK', b'{"live":true}'
                elif line in (b'GET /startup HTTP/1.1', b'GET /startup HTTP/1.0'):
                    code = '200 OK' if self.started else '503 Service Unavailable'
                elif line in (b'GET /status HTTP/1.1', b'GET /status HTTP/1.0'):
                    code, body = '200 OK', json.dumps(self.status(), separators=(',', ':')).encode()
                writer.write(('HTTP/1.1 ' + code + '\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: '
                              + str(len(body)) + '\r\n\r\n').encode() + body)
                await writer.drain()
        except (TimeoutError, asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self._clients.discard(task)

    async def run(self):
        if self.launch is None:
            from hummingbot.strategy_v2.life_liquidity.storage.postgres.headless import launch
            self.launch = launch
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.config import load_config

        # Supervision never reads a local secret file; central runner still owns
        # migration idempotency. Separate automatic migration Jobs set false.
        migration = load_config(no_env_file=True)
        self.server = await asyncio.start_server(self._client, self.config.status_host, self.config.status_port, limit=4096)
        self._sampling = asyncio.create_task(self._sample())
        self._stop_waiter = asyncio.create_task(self.stop.wait())
        startup = asyncio.create_task(self._startup_deadline())
        self.service_task = asyncio.create_task(self.launch(
            config_path=self.config.config_path, factory_spec=self.config.source_factory, stop=self.stop,
            tick_seconds=self.config.tick_seconds, startup_timeout_s=self.config.startup_timeout_s,
            shutdown_timeout_s=self.config.shutdown_timeout_s, migration_config=migration, on_started=self._started))
        self.service_task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        try:
            done, _ = await asyncio.wait((self.service_task, self._stop_waiter, startup), return_when=asyncio.FIRST_COMPLETED)
            if startup in done:
                await startup  # Raises on timeout, without cancelling physical work.
                done, _ = await asyncio.wait((self.service_task, self._stop_waiter), return_when=asyncio.FIRST_COMPLETED)
            if self.service_task in done:
                await self.service_task
                if not self.stop.is_set():
                    raise RuntimeError('WORKER_UNEXPECTED_STOP')
            else:
                async with asyncio.timeout(self.config.shutdown_timeout_s):
                    await asyncio.shield(self.service_task)
            event('STOPPED', trading_permission=False)
        finally:
            self.request_stop()
            self.server.close()
            await self.server.wait_closed()
            for task in (self._sampling, self._stop_waiter, startup, *self._clients):
                task.cancel()
            await asyncio.gather(self._sampling, self._stop_waiter, startup, *self._clients, return_exceptions=True)
            # A timed-out original service_task is deliberately not cancelled,
            # refunded, or replaced. The foreground process must terminate.

    def arm_exit_deadline(self):
        if self._deadline is not None:
            return

        def expire():
            if not self.finished.wait(self.config.shutdown_timeout_s):
                os._exit(2)  # Physical process stop; no terminality inference.
        self._deadline = threading.Thread(target=expire, name='life-worker-exit-deadline', daemon=True)
        self._deadline.start()


def main():
    try:
        config = WorkerConfig.from_environment(os.environ)
    except ValueError:
        event('CONFIG_INVALID')
        return 1
    loop, supervisor = asyncio.new_event_loop(), WorkerSupervisor(config)
    asyncio.set_event_loop(loop)

    def stopping():
        # The external process boundary owns this physical deadline. Never
        # install os._exit in reusable service/repository shutdown APIs.
        supervisor.arm_exit_deadline()
        supervisor.request_stop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stopping)
    code = 0
    try:
        event('STARTING', trading_permission=False)
        loop.run_until_complete(supervisor.run())
    except BaseException:
        event('FAILED', trading_permission=False)
        code = 1
    finally:
        # asyncio.run's unbounded cancellation join could outlive a platform's
        # SIGKILL window. Original physical cleanup retains all dependencies;
        # a timed-out process exits without falsely reporting graceful closure.
        if (supervisor.service_task is not None and not supervisor.service_task.done()
                or any(not task.done() for task in asyncio.all_tasks(loop))):
            os._exit(2)
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(sig)
        loop.close()
        supervisor.finished.set()
    # Foreground-only process boundary. Do not hand an expired physical deadline
    # to Python atexit's unbounded ThreadPoolExecutor join after a failed close.
    os._exit(code)


if __name__ == '__main__':
    sys.exit(main())
