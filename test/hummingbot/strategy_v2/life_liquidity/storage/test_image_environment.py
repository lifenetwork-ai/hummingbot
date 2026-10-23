import json
from pathlib import Path
from tempfile import TemporaryDirectory
from test.hummingbot.strategy_v2.life_liquidity.storage.image_environment import image_environment, require_native

import pytest

IMAGE = 'sha256:' + 'a' * 64


def docker_response(*, architecture='aarch64', cpus=8, image=IMAGE, running=''):
    calls = []

    def docker(*args):
        calls.append(args)
        if args[:2] == ('image', 'inspect'):
            return json.dumps(dict(id=image, os='linux', architecture='amd64'))
        if args[0] == 'info':
            return json.dumps(dict(os='linux', architecture=architecture, cpus=cpus, memory_bytes=8589934592))
        if args[0] == 'ps':
            return running
        raise AssertionError(args)
    return docker, calls


@pytest.mark.parametrize('architecture,mode', [('x86_64', 'native'), ('amd64', 'native'),
                                               ('aarch64', 'emulated'), ('arm64', 'emulated')])
def test_environment_distinguishes_daemon_architecture_from_image(architecture, mode):
    docker, calls = docker_response(architecture=architecture, running='abc123\ndef456\n')
    profile = image_environment(IMAGE, docker)
    assert profile['execution_mode'] == mode
    assert profile['daemon']['cpus'] == 8
    assert profile['existing_containers_at_start'] == 2
    assert profile['image'] == dict(id=IMAGE, os='linux', architecture='amd64')
    assert profile['worker_limits'] == dict(cpus=2, memory_bytes=2147483648)
    assert profile['postgres_limits'] == dict(cpus=2, memory_bytes=1073741824)
    assert all('{{json .}}' not in c for c in calls)


@pytest.mark.parametrize('changes', [dict(architecture='unknown'), dict(cpus=True), dict(cpus=0), dict(image='sha256:' + 'b' * 64)])
def test_invalid_environment_cannot_be_reported_as_native(changes):
    docker, _ = docker_response(**changes)
    with pytest.raises(ValueError, match='IMAGE_ENVIRONMENT_INVALID'):
        image_environment(IMAGE, docker)


def test_native_requirement_rejects_emulation_without_changing_limits():
    docker, _ = docker_response()
    with pytest.raises(ValueError, match='NATIVE_IMAGE_REQUIRED'):
        require_native(image_environment(IMAGE, docker))
    native, _ = docker_response(architecture='x86_64')
    require_native(image_environment(IMAGE, native))


def test_runner_records_emulation_and_refuses_before_creating_database(monkeypatch, capsys):
    from test.hummingbot.strategy_v2.life_liquidity.storage import run_postgres
    docker, calls = docker_response()
    monkeypatch.setattr(run_postgres, 'docker', docker)
    with TemporaryDirectory(prefix='life-image-profile-', dir='/tmp') as temporary:
        output = Path(temporary) / 'qualification'
        monkeypatch.setattr('sys.argv', ['run_postgres', '--suite', 'image_native', '--linux-image', IMAGE,
                                         '--require-native-image', '--output', str(output)])
        with pytest.raises(SystemExit) as error:
            run_postgres.main()
        assert error.value.code == 2
        assert 'NATIVE_IMAGE_REQUIRED' in capsys.readouterr().err
        assert json.loads((output / 'image_environment.json').read_text())['execution_mode'] == 'emulated'
    assert [c[0] for c in calls] == ['image', 'info', 'ps']
