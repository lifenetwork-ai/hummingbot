"""Opt-in bounded flow measurements for parent and real service child alike."""
import json
import os
import sys
from contextlib import contextmanager


def report(trace, *, boundary='SCOPE_EXIT'):
    print('bounded_process_flow=' + json.dumps(dict(
        process_id=os.getpid(), boundary=boundary,
        rows=trace.rows(), active_rows=trace.active_rows(), summaries=trace.summaries(), revocations=list(trace.revocations),
        dropped=trace.dropped, details_dropped=trace.details_dropped,
        database_dropped=trace.database_dropped, inflight_dropped=trace.inflight_dropped)), file=sys.stderr, flush=True)


@contextmanager
def diagnostic_flow():
    if os.environ.get('LIFE_PG_PROCESS_FLOW_TRACE') != '1':
        yield None
        return
    from test.hummingbot.strategy_v2.life_liquidity.storage.postgres.flow_trace import QuoteFlowTrace

    from pytest import MonkeyPatch
    with MonkeyPatch.context() as patch:
        trace = QuoteFlowTrace(patch, capacity=4096).install()
        primary = None
        try:
            yield trace
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                report(trace)
            except Exception:
                if primary is None:
                    raise
                primary.add_note('PROCESS_FLOW_REPORT_FAILED')
