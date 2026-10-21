"""Test-only bounded monotonic flow spans; never records arguments or authority."""
import asyncio
from collections import deque
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from time import perf_counter
from urllib.parse import urlsplit


@dataclass(frozen=True)
class FlowSpan:
    quote: int
    phase: str
    started_ms: float
    elapsed_ms: float
    outcome: str


class QuoteFlowTrace:
    def __init__(self, monkeypatch, *, capacity=512):
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError('invalid trace bound')
        self.spans = deque(maxlen=capacity)
        self.details = deque(maxlen=capacity)
        self.dropped = self.details_dropped = 0
        self._origin = perf_counter()
        self._quote = ContextVar('test_quote_flow', default=0)
        self._next = 0
        self._patch = monkeypatch

    def wrap(self, cls, name, phase, *, starts_quote=False):
        original = getattr(cls, name)
        trace = self

        @wraps(original)
        async def tracked(instance, *args, **kwargs):
            token = None
            if starts_quote:
                trace._next += 1
                token = trace._quote.set(trace._next)
            quote = trace._quote.get()
            label = phase(kwargs) if callable(phase) else phase
            start, outcome = perf_counter(), 'success'
            try:
                return await original(instance, *args, **kwargs)
            except BaseException as error:
                outcome = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'error'
                raise
            finally:
                elapsed = (perf_counter() - start) * 1000
                if len(trace.spans) == trace.spans.maxlen:
                    trace.dropped += 1
                trace.spans.append(FlowSpan(quote, label, (start - trace._origin) * 1000, elapsed, outcome))
                if token is not None:
                    trace._quote.reset(token)
        self._patch.setattr(cls, name, tracked)

    def install(self):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_source import PgJointAccountReconciler
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_account_source import PgQuoteAccountSource
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.recorder_application import PgRecorderApplication
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.runner_journal import PgQuoteRunnerJournal
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.service import PgLiquidityService
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_market import PgSpotMarketSource
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.spot_producer import PgSpotQuoteProducer
        self.wrap(PgLiquidityService, 'quote', 'quote_total', starts_quote=True)
        self.wrap(PgSpotQuoteProducer, 'propose', 'proposal')
        self.wrap(PgQuoteRunnerJournal, 'preflight_publication', 'publication_preflight')
        self.wrap(PgQuoteAccountSource, 'capture_and_publish_intent_ready', 'capture_publication')
        self.wrap(PgQuoteRunnerJournal, 'async_on_runner_create_action_dispatched', 'runner_dispatch')
        self.wrap(PgQuoteRunnerJournal, 'prepare_quote_action', 'prepare_transaction')
        self.wrap(PgQuoteRunnerJournal, 'arm_quote_action', 'arm_transaction')
        self.wrap(PgSpotMarketSource, 'collect', lambda k: 'final_collection' if k.get('pending_quote') is not None
                  else 'prepare_collection' if k.get('preparing_quote') is not None else 'proposal_collection')
        self.wrap(PgJointAccountReconciler, '_collection_context', 'native_context')
        self.wrap(PgRecorderApplication, 'drain', 'reporting_drain')
        self.wrap(PgRecorderApplication, 'flush_deferred', 'reporting_flush')
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.joint_event_application import (
            PgJointEventApplication,
        )
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.quote_transport import PgQuoteRequestGuard
        self.wrap(PgJointEventApplication, 'drain', 'monetary_drain')
        self.wrap(PgQuoteRequestGuard, 'authorize', 'request_authorization')
        self.wrap(PgQuoteRequestGuard, 'acknowledge', 'guarded_ack')
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_application import PgHedgeApplication
        self.wrap(PgHedgeApplication, 'allocate_spot', 'capital_carry_allocation')

        self.quota()
        return self

    def quota(self):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.shared_sdk_quota import PgSharedSdkQuota, _QuotaView
        trace = self

        def patch(cls, name, label):
            original = getattr(cls, name)

            @asynccontextmanager
            async def tracked(instance, *args, **kwargs):
                quote, start = trace._quote.get(), perf_counter()
                outcome = 'error'
                try:
                    async with original(instance, *args, **kwargs):
                        acquired = perf_counter()
                        outcome = 'success'
                        if len(trace.details) == trace.details.maxlen:
                            trace.details_dropped += 1
                        trace.details.append(FlowSpan(quote, label, (start - trace._origin) * 1000,
                                                      (acquired - start) * 1000, outcome))
                        yield
                except BaseException as error:
                    if outcome != 'success':
                        if len(trace.details) == trace.details.maxlen:
                            trace.details_dropped += 1
                        trace.details.append(FlowSpan(quote, label, (start - trace._origin) * 1000,
                                                      (perf_counter() - start) * 1000,
                                                      'cancelled' if isinstance(error, asyncio.CancelledError) else 'error'))
                    raise
            self._patch.setattr(cls, name, tracked)
        patch(PgSharedSdkQuota, '_access', 'collection_lock_wait')
        patch(_QuotaView, '_slot', 'sdk_quota_wait')

    def http(self, session):
        original, trace = session.request, self

        async def tracked(**kwargs):
            start, quote = perf_counter(), trace._quote.get()
            outcome = 'success'
            try:
                return await original(**kwargs)
            except BaseException as error:
                outcome = 'cancelled' if isinstance(error, asyncio.CancelledError) else 'error'
                raise
            finally:
                path = urlsplit(kwargs['url']).path
                label = 'http_order' if path == '/api/v5/trade/order' and kwargs['method'] == 'POST' else 'http_source'
                if len(trace.details) == trace.details.maxlen:
                    trace.details_dropped += 1
                trace.details.append(FlowSpan(quote, label, (start - trace._origin) * 1000,
                                              (perf_counter() - start) * 1000, outcome))
        self._patch.setattr(session, 'request', tracked)

    def rows(self):
        # Fixed phase labels and local quote ordinals only; no business IDs,
        # request parameters/bodies, SQL or raw exceptions leave the helper.
        return [(v.quote, v.phase, round(v.started_ms, 3), round(v.elapsed_ms, 3), v.outcome)
                for v in sorted((*self.spans, *self.details), key=lambda v: v.started_ms)]
