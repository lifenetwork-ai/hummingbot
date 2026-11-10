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


class DrawdownValuationUnavailable(ValueError):
    def __init__(self, connector, pair, reason, age=None):
        self.connector, self.pair, self.reason, self.age = connector, pair, reason, age
        super().__init__('drawdown_valuation_unavailable')
