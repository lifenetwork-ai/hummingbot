"""Read retries are bounded and never classify a mutation for the caller."""
from types import SimpleNamespace

import pytest

from hummingbot.cli.service_availability import ServiceAvailability
from hummingbot.core.utils.read_retry import retry_delay, transient_read


@pytest.mark.parametrize("status,expected", [(401, False), (403, False), (404, False), (408, True), (429, True), (503, True)])
def test_existing_rest_error_status_classification(status, expected):
    assert transient_read(OSError(f"HTTP status is {status}. private response")) is expected


def test_retry_delay_caps_jitter_and_respects_available_retry_after():
    assert .8 <= retry_delay(1) <= 1
    assert 24 <= retry_delay(60) <= 30
    assert retry_delay(1, SimpleNamespace(headers={"Retry-After": "45"})) == 45
    assert .8 <= retry_delay(1, SimpleNamespace(headers={"Retry-After": "NaN"})) <= 1
    assert not transient_read(ValueError("invalid configuration"))


def test_incident_collections_are_bounded_and_recovery_clears_old_failures():
    state = ServiceAvailability()
    for index in range(100):
        state.fail(str(index), "optional_failure", ValueError("private"))
    assert len(state.snapshot()["incidents"]) <= 16
    for key in list(state.incidents):
        state.clear(key)
    assert state.snapshot()["incidents"] == []
    assert not state.logged and len(state.healthy) <= 16
