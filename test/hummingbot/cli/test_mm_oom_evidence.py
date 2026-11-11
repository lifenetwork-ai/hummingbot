"""SIGKILL/exit137 alone must not masquerade as a real memory fault."""
from types import SimpleNamespace

import pytest

from scripts.test_mm_oom import kernel_oom_proven


def test_child_oom_requires_kernel_counter_increment():
    assert kernel_oom_proven({'oom_kill': 2}, {'oom_kill': 3})
    assert not kernel_oom_proven({'oom_kill': 2}, {'oom_kill': 2})
    assert not kernel_oom_proven({}, {})


def test_container_oom_requires_both_docker_cause_and_physical_exit():
    assert kernel_oom_proven({}, {}, container_state=dict(Status='exited', ExitCode=137, OOMKilled=True))
    assert not kernel_oom_proven({}, {}, container_state=dict(Status='exited', ExitCode=137, OOMKilled=False))
    assert not kernel_oom_proven({}, {}, container_state=dict(Status='running', ExitCode=137, OOMKilled=True))
    assert not kernel_oom_proven({}, {}, container_state=dict(Status='exited', ExitCode=1, OOMKilled=True))


def test_oom_process_observer_retries_only_sigkill_and_preserves_real_role_identity(monkeypatch):
    from scripts import test_mm_oom as runner
    replies = iter([SimpleNamespace(returncode=137, stdout=''),
                    SimpleNamespace(returncode=0, stdout=(
                        '33 python\0-m\0hummingbot.cli.service\0--role\0engine\0\n'
                        '34 python\0-m\0hummingbot.cli.service\0--role\0api\0\n')),
                    SimpleNamespace(returncode=1, stdout='', stderr='observer failed')])
    monkeypatch.setattr(runner, 'docker', lambda *a, **k: next(replies))
    monkeypatch.setattr(runner, 'OBSERVER_OOM_EXITS', {})
    assert runner.children('fixture') == {}
    assert runner.OBSERVER_OOM_EXITS == {'fixture': 1}
    assert runner.children('fixture') == {'engine': 33, 'api': 34}
    with pytest.raises(RuntimeError, match='exit code 1'):
        runner.children('fixture')
