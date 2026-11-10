"""SIGKILL/exit137 alone must not masquerade as a real memory fault."""
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
