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


@dataclass(frozen=True)
class DatabaseSpan:
    quote: int
    operation: str
    started_ms: float
    elapsed_ms: float
    phases_ms: tuple
    outcome: str


class QuoteFlowTrace:
    def __init__(self, monkeypatch, *, capacity=512):
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError('invalid trace bound')
        self.spans = deque(maxlen=capacity)
        self.details = deque(maxlen=capacity)
        self.database_spans = deque(maxlen=capacity)
        self.database_dropped = 0
        self.background_leases = deque(maxlen=capacity)
        self.background_leases_dropped = 0
        self.revocations = deque(maxlen=16)
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
            label = phase(kwargs) if callable(phase) else phase
            if label is None:
                return await original(instance, *args, **kwargs)
            token = None
            if starts_quote:
                trace._next += 1
                token = trace._quote.set(trace._next)
            quote = trace._quote.get()
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
        self.wrap(PgLiquidityService, 'execute_hedge', 'hedge_total', starts_quote=True)
        self.wrap(PgSpotQuoteProducer, 'propose', 'proposal')
        self.wrap(PgQuoteRunnerJournal, 'preflight_publication', 'publication_preflight')
        self.wrap(PgQuoteAccountSource, 'capture_and_publish_intent_ready', 'capture_publication')
        self.wrap(PgQuoteRunnerJournal, 'async_on_runner_create_action_dispatched', 'runner_dispatch')
        self.wrap(PgQuoteRunnerJournal, 'prepare_quote_action', 'prepare_transaction')
        self.wrap(PgQuoteRunnerJournal, 'arm_quote_action', 'arm_transaction')
        self.wrap(PgSpotMarketSource, 'collect', lambda k: 'final_collection' if k.get('pending_quote') is not None
                  else 'prepare_collection' if k.get('preparing_quote') is not None else 'proposal_collection')
        self.wrap(PgJointAccountReconciler, '_collection_context', 'native_context')
        self.wrap(PgJointAccountReconciler, '_quote_collection_opening', 'native_opening')
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
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.hedge_source import PgHedgeSource
        self.wrap(PgHedgeApplication, 'allocate_spot', 'capital_carry_allocation')
        self.wrap(PgHedgeApplication, 'execute', 'hedge_execution')
        self.wrap(PgHedgeSource, 'collect', 'hedge_collection')
        self.wrap(PgHedgeSource, 'refresh', 'hedge_refresh')

        self.quota()
        self.database()
        self.revocation()
        return self

    def revocation(self):
        import re
        import sys
        import traceback

        from hummingbot.strategy_v2.life_liquidity.storage.contracts import StorageError
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.service import PgLiquidityService
        original, trace = PgLiquidityService.revoke, self

        def revoked(service):
            if not service._revoked:
                error = sys.exception()
                code = str(error) if isinstance(error, StorageError) else None
                if code is not None and re.fullmatch('[A-Z0-9_]{1,128}', code) is None:
                    code = None
                # Test-only fixed source locations, never locals/arguments,
                # raw exceptions, request bytes or financial identities.
                frames = [(frame.name, frame.lineno) for frame in traceback.extract_stack(limit=8)]
                trace.revocations.append(dict(quote=trace._quote.get(), elapsed_ms=round((perf_counter() - trace._origin) * 1000, 3),
                                              code=code, frames=frames))
            return original(service)
        self._patch.setattr(PgLiquidityService, 'revoke', revoked)

    def database(self):
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.diagnostics import _current_trace, _Trace
        from hummingbot.strategy_v2.life_liquidity.storage.postgres.runtime import PgRuntime
        operations = {
            'spot_capture_quote_action': 'capture',
            'spot_dispatch_claimed_action': 'dispatch',
            'spot_action_prepare': 'prepare',
            'spot_arm_quote': 'arm',
            'quote_birth_issue': 'capture',
            'quote_birth_prepare': 'prepare',
        }

        def admission(kwargs):
            name = operations.get(kwargs.get('operation'))
            return None if name is None else 'db_' + name

        def commit(_):
            current = _current_trace.get()
            name = None if current is None else operations.get(current.operation)
            return None if name is None else 'commit_' + name
        self.wrap(PgRuntime, '_admit', admission)
        self.wrap(PgRuntime, '_commit', commit)
        original, trace = _Trace.finish, self

        def finished(instance, outcome):
            value = original(instance, outcome)
            quote = trace._quote.get()
            if quote > 0:
                if len(trace.database_spans) == trace.database_spans.maxlen:
                    trace.database_dropped += 1
                # Project only the original diagnostic's fixed operation and
                # timings. PID, arguments, SQL and driver errors are excluded.
                trace.database_spans.append(DatabaseSpan(
                    quote, value.operation, (instance.started - trace._origin) * 1000,
                    value.elapsed_ms, value.phases_ms, value.outcome))
            elif value.operation in ('process_lease_claim', 'process_lease_heartbeat', 'process_lease_lookup'):
                if len(trace.background_leases) == trace.background_leases.maxlen:
                    trace.background_leases_dropped += 1
                trace.background_leases.append(DatabaseSpan(
                    0, value.operation, (instance.started - trace._origin) * 1000,
                    value.elapsed_ms, value.phases_ms, value.outcome))
            return value
        self._patch.setattr(_Trace, 'finish', finished)

    def database_summary(self, quote, *, started_ms=None, ended_ms=None):
        result = {}
        for sample in self.database_spans:
            if sample.quote != quote:
                continue
            if (started_ms is not None and sample.started_ms < started_ms
                    or ended_ms is not None and sample.started_ms + sample.elapsed_ms > ended_ms):
                continue
            row = result.setdefault(sample.operation, dict(count=0, errors=0, elapsed_ms=0.0, phases_ms={}))
            row['count'] += 1
            row['errors'] += sample.outcome not in ('success', 'confirmed_after_release_error')
            row['elapsed_ms'] += sample.elapsed_ms
            for phase, elapsed in sample.phases_ms:
                row['phases_ms'][phase] = row['phases_ms'].get(phase, 0.0) + elapsed
        for row in result.values():
            row['elapsed_ms'] = round(row['elapsed_ms'], 3)
            row['phases_ms'] = {k: round(v, 3) for k, v in row['phases_ms'].items()}
        return result

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
                value = FlowSpan(quote, label, (start - trace._origin) * 1000,
                                 (perf_counter() - start) * 1000, outcome)
                if label == 'http_order':
                    if len(trace.spans) == trace.spans.maxlen:
                        trace.dropped += 1
                    trace.spans.append(value)
                else:
                    if len(trace.details) == trace.details.maxlen:
                        trace.details_dropped += 1
                    trace.details.append(value)
        self._patch.setattr(session, 'request', tracked)

    def rows(self):
        # Fixed phase labels and local quote ordinals only; no business IDs,
        # request parameters/bodies, SQL or raw exceptions leave the helper.
        return [(v.quote, v.phase, round(v.started_ms, 3), round(v.elapsed_ms, 3), v.outcome)
                for v in sorted((*self.spans, *self.details), key=lambda v: v.started_ms)]

    def summaries(self):
        """Monotonic birth-interval bounds, not a SQL timestamp or a verdict.

        Native birth is inside the physical CAPTURE admission. Its successful
        return follows confirmed COMMIT/pool release. Those two boundaries bound
        elapsed time to HTTP entry without comparing host/DB wall clocks. Failed
        ARM exposes only its *start*, never a fabricated HTTP/denial timestamp.
        Phase durations overlap; they must not be added to the interval bounds.
        An evicted/multiple capture or endpoint produces no interval estimate.
        """
        spans = sorted((*self.spans, *self.details), key=lambda v: v.started_ms)
        groups = {}
        for value in spans:
            if value.quote > 0:
                groups.setdefault(value.quote, []).append(value)
        result = []
        for quote, group in sorted(groups.items()):
            phases = {}
            for value in group:
                phase = phases.setdefault(value.phase, dict(count=0, elapsed_ms=0.0, errors=0))
                phase['count'] += 1
                phase['elapsed_ms'] += value.elapsed_ms
                phase['errors'] += value.outcome != 'success'
            for value in phases.values():
                value['elapsed_ms'] = round(value['elapsed_ms'], 3)
            complete = self.dropped == 0
            summary = dict(quote=quote, phases=phases, retention_complete=complete,
                           details_complete=self.details_dropped == 0,
                           database_complete=self.database_dropped == 0,
                           background_lease_complete=self.background_leases_dropped == 0,
                           database_by_operation=self.database_summary(quote))
            roots = [v for v in group if v.phase in ('quote_total', 'hedge_total')]
            if self.background_leases_dropped == 0 and len(roots) == 1:
                # Parallel wall-time overlap is not serial critical-path cost,
                # SQL CPU time or proof that the heartbeat blocked the sender.
                start, end = roots[0].started_ms, roots[0].started_ms + roots[0].elapsed_ms
                overlap = [(v, max(0, min(end, v.started_ms + v.elapsed_ms) - max(start, v.started_ms)))
                           for v in self.background_leases]
                overlap = [(v, ms) for v, ms in overlap if ms > 0]
                summary['background_lease_overlap'] = dict(
                    count=len(overlap), elapsed_ms=round(sum(v.elapsed_ms for v, _ in overlap), 3),
                    overlap_ms=round(sum(ms for _, ms in overlap), 3))
            capture = [v for v in group if v.phase == 'db_capture']
            arms = [v for v in group if v.phase == 'db_arm']
            if len(capture) == len(arms) == 1:
                summary['database_capture_through_arm'] = self.database_summary(
                    quote, started_ms=capture[0].started_ms, ended_ms=arms[0].started_ms + arms[0].elapsed_ms)
            endpoints = [v for v in group if v.phase == 'http_order']
            label = 'capture_to_http_entry_ms'
            if not endpoints:
                endpoints = [v for v in group if v.phase == 'db_arm']
                label = 'capture_to_arm_start_ms'
            if complete and len(capture) == len(endpoints) == 1 and capture[0].outcome == 'success':
                first, last = capture[0], endpoints[0]
                lower = last.started_ms - first.started_ms - first.elapsed_ms
                if lower >= 0:
                    summary[label] = dict(lower=round(lower, 3), upper=round(last.started_ms - first.started_ms, 3))
            result.append(summary)
        return result
