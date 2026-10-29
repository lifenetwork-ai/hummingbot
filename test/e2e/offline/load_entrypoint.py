"""Instrument real child roles while retaining the shipping supervisor."""
import importlib.util
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

if importlib.util.find_spec('hummingbot.cli.service_processes') and '--role' not in sys.argv:
    from hummingbot.cli import service, service_processes
    service_processes.child_command = lambda role: [sys.executable, __file__, '--role', role]
    service.main()
else:
    runpy.run_path(str(Path(__file__).with_name('load_service.py')), run_name='__main__')
