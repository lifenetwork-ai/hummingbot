"""Bounded local Docker facts; functional emulation is not native latency proof."""
import json
import re

WORKER_CPUS, WORKER_MEMORY_BYTES = 2, 2147483648
POSTGRES_CPUS, POSTGRES_MEMORY_BYTES = 2, 1073741824


def image_environment(image, docker):
    """Inspect only explicit non-secret fields, before creating test containers.

    The daemon architecture matters: the CLI host may use a remote Docker
    daemon. Existing-container count is a start observation, not an isolation
    guarantee or a measurement of competing host/VM work during the test.
    """
    try:
        if re.fullmatch(r'sha256:[0-9a-f]{64}', image) is None:
            raise ValueError()
        artifact = json.loads(docker('image', 'inspect', image, '--format',
                                     '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}}}'))
        daemon = json.loads(docker('info', '--format',
                                   '{"os":{{json .OSType}},"architecture":{{json .Architecture}},'
                                   '"cpus":{{json .NCPU}},"memory_bytes":{{json .MemTotal}}}'))
        architectures = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}
        if (artifact != dict(id=image, os='linux', architecture='amd64')
                or set(daemon) != {'os', 'architecture', 'cpus', 'memory_bytes'} or daemon['os'] != 'linux'
                or daemon['architecture'] not in architectures
                or any(type(daemon[k]) is not int or daemon[k] <= 0 for k in ('cpus', 'memory_bytes'))):
            raise ValueError()
        running = docker('ps', '--format', '{{.ID}}').splitlines()
        if any(re.fullmatch(r'[0-9a-f]{1,64}', value) is None for value in running):
            raise ValueError()
        return dict(image=artifact, daemon=daemon,
                    execution_mode='native' if architectures[daemon['architecture']] == artifact['architecture'] else 'emulated',
                    existing_containers_at_start=len(running),
                    worker_limits=dict(cpus=WORKER_CPUS, memory_bytes=WORKER_MEMORY_BYTES),
                    postgres_limits=dict(cpus=POSTGRES_CPUS, memory_bytes=POSTGRES_MEMORY_BYTES))
    except (KeyError, TypeError, ValueError):
        raise ValueError('IMAGE_ENVIRONMENT_INVALID') from None


def require_native(profile):
    if profile['execution_mode'] != 'native':
        raise ValueError('NATIVE_IMAGE_REQUIRED')
