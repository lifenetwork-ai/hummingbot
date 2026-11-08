"""A failing shutdown gate must retain bounded Docker state before cleanup."""
import json
import subprocess
from unittest.mock import Mock

import pytest

from scripts import test_e2e_offline as fixture


@pytest.mark.parametrize('exit_code,oom_killed,poll_timeout', [
    (0, False, False), (1, False, False), (137, True, False), (0, False, True),
])
def test_shutdown_retains_state_without_hiding_original_failure(
        tmp_path, monkeypatch, exit_code, oom_killed, poll_timeout):
    harness = fixture.Harness('unused-image', tmp_path, tmp_path / 'report', 'live')
    name = 'hbot-e2e-fixture-engine'
    state = dict(Status='running' if poll_timeout else 'exited', ExitCode=exit_code,
                 OOMKilled=oom_killed, Pid=32 if poll_timeout else 0,
                 StartedAt='2026-11-08T18:18:19Z', FinishedAt='2026-11-08T18:19:09Z',
                 Error='', Health={'Log': [{'Output': 'must-not-copy-health-body'}]})

    def docker(*args, **kwargs):
        if args[0] == 'inspect':
            return subprocess.CompletedProcess(args, 0, json.dumps([
                dict(State=state, Config={'Env': ['SENSITIVE=must-not-copy-env']})]), '')
        return subprocess.CompletedProcess(args, 0, 'fixture shutdown log', '')

    poll = Mock(side_effect=AssertionError('Timed out: PID 1 shutdown')) if poll_timeout else Mock(return_value=True)
    monkeypatch.setattr(fixture, 'docker', docker)
    monkeypatch.setattr(fixture, 'poll', poll)
    if exit_code or oom_killed or poll_timeout:
        with pytest.raises(AssertionError):
            harness.terminate(name)
    else:
        harness.terminate(name)
    row = json.loads((harness.report / (name + '-shutdown-state.json')).read_text())
    assert row['container'] == name
    assert row['elapsed_seconds'] >= 0
    assert row['state']['ExitCode'] == exit_code
    assert row['state']['OOMKilled'] is oom_killed
    assert row['state']['Status'] == state['Status']
    assert row['state']['StartedAt'] == state['StartedAt']
    assert row['state']['FinishedAt'] == state['FinishedAt']
    assert 'must-not-copy' not in json.dumps(row)
    assert poll.call_args.kwargs['timeout'] == 9.5
