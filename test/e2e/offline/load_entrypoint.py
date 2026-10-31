"""Instrument real child roles while retaining the shipping supervisor."""
import importlib.util
import os
import runpy
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

if importlib.util.find_spec('hummingbot.cli.service_processes') and '--role' not in sys.argv:
    from hummingbot.cli import service, service_processes
    if os.environ.get('HBOT_LOAD_INCIDENT_DIRECTORY'):
        from load_incidents import ProbeIncidentPublisher
        publisher = ProbeIncidentPublisher('/tmp/load-incident-trigger.json')
        original_probe = service_processes.probe_health

        def probe_health(port, path):
            healthy, details = original_probe(port, path)
            try:
                publisher.observe('api' if port == int(os.environ.get('PORT', '8080')) else 'engine', healthy, details)
            except OSError:
                pass  # Test diagnostics must not change the shipping health result.
            return healthy, details

        service_processes.probe_health = probe_health
    service_processes.child_command = lambda role: [sys.executable, __file__, '--role', role]
    service.main()
else:
    runpy.run_path(str(Path(__file__).with_name('load_service.py')), run_name='__main__')
