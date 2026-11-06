from types import SimpleNamespace

import pytest

from scripts import test_postgres as runner


def test_postgres_runner_waits_for_published_loopback_port_before_constructing_dsn(monkeypatch):
    mappings = iter(['', '127.0.0.1:54321\n'])
    calls = []
    monkeypatch.setattr(runner.os, 'environ', {'DATABASE_URL': 'do-not-use', 'K_SERVICE': 'cloud', 'HBOT_OKX_API_KEY': 'dummy'})
    monkeypatch.setattr(runner.subprocess, 'check_output', lambda *args, **kwargs: next(mappings))
    monkeypatch.setattr(runner.time, 'sleep', lambda _: None)

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')
    monkeypatch.setattr(runner.subprocess, 'run', run)
    assert runner.main() == 0
    invocation = next(kwargs for command, kwargs in calls if '-m' in command and 'pytest' in command)
    assert invocation['env'] == {'HBOT_TEST_POSTGRES_URL': invocation['env']['HBOT_TEST_POSTGRES_URL']}
    assert '@127.0.0.1:54321/hbot_test_port' in invocation['env']['HBOT_TEST_POSTGRES_URL']
    assert calls[-1][0][:4] == ['docker', 'rm', '-f', '-v']


def test_postgres_runner_port_startup_timeout_fails_without_running_or_skipping_tests(monkeypatch):
    calls = []
    stamps = iter([0, 31])
    monkeypatch.setattr(runner.subprocess, 'check_output', lambda *args, **kwargs: '')
    monkeypatch.setattr(runner.time, 'monotonic', lambda: next(stamps))
    monkeypatch.setattr(runner.time, 'sleep', lambda _: None)
    monkeypatch.setattr(runner.subprocess, 'run', lambda command, **kwargs: calls.append(command) or SimpleNamespace(returncode=0))
    assert runner.main() == 1
    assert not any('-m' in command and 'pytest' in command for command in calls)
    assert calls[-1][:4] == ['docker', 'rm', '-f', '-v']


@pytest.mark.parametrize('mapping', ['0.0.0.0:5432', '127.0.0.1:0', '127.0.0.1:invalid', '127.0.0.1:5432\n127.0.0.1:5433'])
def test_postgres_runner_refuses_unexpected_or_non_loopback_mapping(monkeypatch, mapping):
    monkeypatch.setattr(runner.subprocess, 'check_output', lambda *args, **kwargs: mapping)
    with pytest.raises(RuntimeError, match='published loopback port'):
        runner.published_port('disposable-test')
