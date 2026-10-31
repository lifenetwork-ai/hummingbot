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
        from load_incidents import ProbeIncidentPublisher, ProbeStackSampler
        publisher = ProbeIncidentPublisher('/tmp/load-incident-trigger.json')
        stack_sampler = ProbeStackSampler()
        supervisors = []

        class DiagnosticSupervisor(service_processes.ChildSupervisor):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                supervisors[:] = [self]

        service_processes.ChildSupervisor = DiagnosticSupervisor
        original_probe = service_processes.probe_health

        def probe_health(port, path):
            healthy, details = original_probe(port, path)
            role = 'api' if port == int(os.environ.get('PORT', '8080')) else 'engine'
            try:
                publisher.observe(role, healthy, details)
                child = supervisors[0].children.get(role) if supervisors else None
                dumped = stack_sampler.observe(role, healthy, child)
                if dumped:
                    service_processes.LOGGER.warning(
                        'Offline diagnostic stack dump requested role=%s failed_probes=%s dump_count=%s',
                        role, dumped['failed_probes'], dumped['dump_count'],
                        extra={'event': 'load_stack_dump', 'scope': role})
            except OSError:
                pass  # Test diagnostics must not change the shipping health result.
            return healthy, details

        service_processes.probe_health = probe_health
    service_processes.child_command = lambda role: [sys.executable, __file__, '--role', role]
    service.main()
else:
    runpy.run_path(str(Path(__file__).with_name('load_service.py')), run_name='__main__')
