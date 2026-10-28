from collections import deque

import pytest

from scripts import test_e2e_offline


class CommandHarness:
    mm_command = test_e2e_offline.Harness.mm_command

    def __init__(self, reads):
        self.reads = deque(reads)
        self.posts = []
        self.gets = 0
        self.metrics = {}

    def state(self):
        return dict(control_version=0)

    def admin(self, port, path, data=None, key=None):
        if data is not None:
            self.posts.append((path, data, key))
            return 202, dict(id='accepted-command')
        if path == '/status':
            return 200, dict(phase='running', error=None)
        self.gets += 1
        return self.reads.popleft()


def bounded_poll(check, *, timeout, description):
    assert timeout == 60
    for _ in range(3):
        if check():
            return True
    raise AssertionError('original completion deadline exhausted')


def test_receipt_transport_miss_is_polled_without_new_mutation_or_deadline(monkeypatch):
    monkeypatch.setattr(test_e2e_offline, 'poll', bounded_poll)
    harness = CommandHarness([(None, None), (200, dict(status='applying')),
                              (200, dict(status='succeeded'))])
    receipt = harness.mm_command(8080, 'start', market_mode='spot')
    assert receipt['id'] == 'accepted-command' and harness.gets == 3
    assert len(harness.posts) == 2 and harness.posts[0] == harness.posts[1]
    assert harness.metrics['command_receipt_transport_retries'] == 1


def test_receipt_transport_misses_cannot_extend_original_completion_deadline(monkeypatch):
    monkeypatch.setattr(test_e2e_offline, 'poll', bounded_poll)
    harness = CommandHarness([(None, None)] * 3)
    with pytest.raises(AssertionError, match='original completion deadline exhausted'):
        harness.mm_command(8080, 'start', market_mode='spot')
    assert len(harness.posts) == 2 and harness.gets == 3


def test_receipt_definitive_http_failure_is_not_hidden_by_polling(monkeypatch):
    monkeypatch.setattr(test_e2e_offline, 'poll', bounded_poll)
    harness = CommandHarness([(503, dict(error='unavailable'))])
    with pytest.raises(AssertionError, match='receipt unavailable HTTP 503'):
        harness.mm_command(8080, 'start', market_mode='spot')
    assert harness.gets == 1 and len(harness.posts) == 2
