"""API startup may exclude its fixed import graph, never later request cycles."""
import os
import subprocess
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_startup_boundary_keeps_runtime_cycles_collectable_and_never_refreezes():
    script = textwrap.dedent('''
        import gc, weakref
        from hummingbot.cli.api_gc import prepare_api_gc

        class Cycle:
            def __init__(self): self.link = self

        static = Cycle()
        assert gc.is_tracked(static)
        enabled, thresholds = gc.isenabled(), gc.get_threshold()
        prepare_api_gc()
        assert id(static) not in {id(value) for value in gc.get_objects()}
        assert gc.get_freeze_count() > 0
        assert gc.isenabled() == enabled and gc.get_threshold() == thresholds
        frozen = gc.get_freeze_count()
        for _ in range(5):
            objects = [Cycle() for _ in range(1000)]
            refs = [weakref.ref(value) for value in objects]
            prepare_api_gc()  # Cannot permanently retain a later request graph.
            del objects
            gc.collect(2)
            assert all(ref() is None for ref in refs)
            assert gc.get_freeze_count() == frozen
        assert gc.isenabled() == enabled and gc.get_threshold() == thresholds
        print('static excluded; 5000 runtime cycles reclaimed; no refreeze')
    ''')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('HBOT_OKX') and key not in ('DATABASE_URL', 'ENV_FILE', 'HBOT_ENV_FILE')}
    result = subprocess.run([sys.executable, '-c', script], env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert '5000 runtime cycles reclaimed' in result.stdout


def test_failed_import_does_not_freeze_partial_startup(monkeypatch):
    from hummingbot.cli import api_gc

    def unavailable(name):
        raise ImportError('incomplete API import')

    monkeypatch.setattr(api_gc, '_prepared', False)
    monkeypatch.setattr(api_gc, 'import_module', unavailable)
    monkeypatch.setattr(api_gc.gc, 'freeze', lambda: pytest.fail('froze a failed startup'))
    with pytest.raises(ImportError, match='incomplete API import'):
        api_gc.prepare_api_gc()
    assert not api_gc._prepared


@pytest.mark.asyncio
async def test_api_prepares_once_before_constructing_host_or_database(monkeypatch):
    from hummingbot.cli import admin_service, api_gc, service
    from hummingbot.logger import service_logging

    order = []
    # Logging is covered separately; this unit must not reconfigure pytest's
    # global logger/warning state when checking only the GC/host boundary.
    monkeypatch.setattr(service_logging, 'configure_service_logging', lambda: None)
    monkeypatch.setattr(api_gc, 'prepare_api_gc', lambda: order.append('prepare'))
    deadline = SimpleNamespace(install=lambda: None, request=lambda: None, close=lambda: None)
    monkeypatch.setattr(service, 'ShutdownDeadline', lambda stop: deadline)

    def construct(*args, **kwargs):
        assert order == ['prepare']
        order.append('host')
        return SimpleNamespace(run=AsyncMock(), closed=True)

    monkeypatch.setattr(admin_service, 'AdminService', construct)
    assert await admin_service.run_admin_service(SimpleNamespace(), engine_port=8091) == 0
    assert order == ['prepare', 'host']
