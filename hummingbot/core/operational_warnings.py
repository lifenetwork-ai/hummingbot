"""Bounded summaries for expected operational states, without global history."""
import time
from collections import OrderedDict


class OperationalWarnings:
    def __init__(self, *, interval=30, maximum=64, clock=None):
        self.interval = interval
        self.maximum = maximum
        self.clock = clock or time.monotonic
        self.states = OrderedDict()

    def observe(self, key):
        now = self.clock()
        state = self.states.get(key)
        if state is None:
            if len(self.states) >= self.maximum:
                self.states.popitem(last=False)
            self.states[key] = dict(start=now, last=now, total=1, suppressed=0)
            return True, dict(total=1, suppressed=0, seconds=0)
        self.states.move_to_end(key)
        state['total'] += 1
        if now - state['last'] >= self.interval:
            result = dict(total=state['total'], suppressed=state['suppressed'], seconds=now - state['start'])
            state.update(last=now, suppressed=0)
            return True, result
        state['suppressed'] += 1
        return False, dict(total=state['total'], suppressed=state['suppressed'], seconds=now - state['start'])

    def recover(self, key):
        state = self.states.pop(key, None)
        if state is None:
            return None
        return dict(total=state['total'], suppressed=state['suppressed'], seconds=self.clock() - state['start'])


class PositionCheckpointBusy(RuntimeError):
    """The ownership row was busy and the checkpoint rolled back before any writes."""


class PositionCheckpointPending(RuntimeError):
    """A controller report still needs a committed position checkpoint."""


class ExecutorArchiveBusy(RuntimeError):
    """The executor archive rolled back before any writes while acquiring ownership."""


class OrderAcknowledgementBusy(RuntimeError):
    """The first ownership fence rejected an ACK before it could write."""

    def __init__(self, error, cancellation):
        super().__init__('Order acknowledgement waits for the ownership row.')
        self.error = error
        self.cancellation = cancellation


class DrawdownValuationUnavailable(ValueError):
    def __init__(self, connector, pair, reason, age=None):
        self.connector, self.pair, self.reason, self.age = connector, pair, reason, age
        super().__init__('drawdown_valuation_unavailable')
