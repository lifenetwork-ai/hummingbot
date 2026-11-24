"""The engine excludes its startup import graph from runtime GC scans."""
import os
import subprocess
import sys
import textwrap


def test_engine_freezes_imports_before_creating_runtime_objects():
    script = textwrap.dedent('''
        import gc
        import os
        import weakref
        from importlib import import_module

        from hummingbot.cli import service

        class Cycle:
            def __init__(self):
                self.link = self

        enabled, thresholds = gc.isenabled(), gc.get_threshold()
        unreachable = Cycle()
        startup_cycle = weakref.ref(unreachable)
        del unreachable

        async def run_service(**kwargs):
            application = import_module('hummingbot.client.hummingbot_application')
            assert gc.is_tracked(application)
            assert not any(obj is application for obj in gc.get_objects())
            assert startup_cycle() is None
            assert gc.isenabled() == enabled and gc.get_threshold() == thresholds

            from hummingbot.cli.engine_gc import prepare_engine_gc
            frozen = gc.get_freeze_count()
            runtime = Cycle()
            runtime_cycle = weakref.ref(runtime)
            prepare_engine_gc()
            del runtime
            gc.collect(2)
            assert runtime_cycle() is None
            assert gc.get_freeze_count() == frozen
            return 0

        os.environ['DATABASE_URL'] = 'postgresql://unused/test'
        os.environ['ADMIN_API_ENABLED'] = 'false'
        service.run_service = run_service
        service.serve_role('engine')
    ''')
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('HBOT_OKX') and key not in ('ENV_FILE', 'HBOT_ENV_FILE')}
    result = subprocess.run([sys.executable, '-c', script], env=env, text=True, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
