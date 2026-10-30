"""One foreground service supervising independent API and trading processes."""
import logging
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from hummingbot.cli.process_supervision import ChildSupervisor, HealthWatchdog
from hummingbot.client.config.runtime_config import RuntimeConfigurationError, RuntimeSettings, postgres_requested

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProcessLayout:
    settings: RuntimeSettings

    @property
    def engine(self):
        private_port = self.settings.port + 1 if self.settings.port < 65535 else 65534
        return replace(self.settings, port=private_port, database_pool_size=self.settings.database_pool_size - 2)

    @property
    def api(self):
        return replace(self.settings, enable_auto_migration=False, database_pool_size=2)


def child_environment(role, source):
    if role not in ChildSupervisor.ROLES:
        raise ValueError('Unknown service process role')
    result = {key: value for key, value in source.items() if key not in ('ENV_FILE', 'HBOT_ENV_FILE')}
    if role == 'api':
        # Normalize the supported public legacy routing setting before removing
        # HBOT_* credentials, so reference discovery uses the engine's site.
        if not result.get('OKX_SITE') and source.get('HBOT_OKX_REGISTRATION_SUB_DOMAIN'):
            result['OKX_SITE'] = source['HBOT_OKX_REGISTRATION_SUB_DOMAIN']
        # All connector credentials use HBOT_*; retain only diagnostic transport
        # controls used on the isolated offline network. Children never reload .env.
        result = {key: value for key, value in result.items()
                  if not key.startswith('HBOT_') or key.startswith(('HBOT_OFFLINE_', 'HBOT_LOAD_', 'HBOT_MEMORY_'))}
    else:
        result['ADMIN_API_ENABLED'] = 'false'
    return result


def child_command(role):
    return [sys.executable, '-m', 'hummingbot.cli.service', '--role', role]


def probe_path(watchdog, role):
    row = watchdog.observations.get(role)
    child = watchdog.supervisor.children[role]
    return '/live' if row and row['child'] is child and row['healthy_once'] else '/startup'


def probe_health(port, path):
    started = time.monotonic()
    status, reason = None, 'connection_error'
    try:
        with urlopen(f'http://127.0.0.1:{port}{path}', timeout=.3) as response:
            status = response.status
            reason = 'healthy' if status == 200 else 'http_status'
    except HTTPError as error:
        status, reason = error.code, 'http_status'
        error.close()
    except (OSError, URLError) as error:
        status, reason = None, 'connection_error'
        if isinstance(error, TimeoutError) or isinstance(getattr(error, 'reason', None), TimeoutError):
            reason = 'timeout'
    # Never read/log the response body, URL, headers or exception text.
    return status == 200, dict(reason=reason, status=status, elapsed_ms=round((time.monotonic() - started) * 1000, 3))


def failure_exit_code(error):
    from hummingbot.model.db_migration.postgres.runner import PostgreSQLBootstrapError
    if isinstance(error, RuntimeConfigurationError):
        return 78
    if isinstance(error, PostgreSQLBootstrapError) and not error.retryable:
        return 78
    return 1


def failure_reason(error):
    from hummingbot.model.db_migration.postgres.runner import PostgreSQLBootstrapError
    if isinstance(error, RuntimeConfigurationError):
        return 'service_configuration_invalid'
    if isinstance(error, PostgreSQLBootstrapError):
        return 'bootstrap_unavailable' if error.retryable else 'schema_incompatible'
    return 'service_internal_failure'


def log_child(event):
    diagnostics = ' '.join(f'{key}={event[key]}' for key in (
        'first_reason', 'failed_probes', 'failure_seconds', 'max_probe_gap_seconds',
        'probe_status', 'probe_elapsed_ms', 'max_probe_elapsed_ms') if key in event)
    LOGGER.log(getattr(logging, event['severity']),
               'Service child event=%s role=%s exit_code=%s retry_after_s=%s %s',
               event['event'], event['role'], event.get('exit_code'), event.get('retry_after_seconds'), diagnostics,
               extra=dict(event=event['event'], scope=event['role'], error_type=event.get('error_type'),
                          reason_code=event.get('reason_code')))


def run_supervisor():
    from hummingbot.cli.service import ShutdownDeadline
    from hummingbot.client.config.admin_config import AdminSettings
    from hummingbot.logger.service_logging import configure_service_logging
    deadline = ShutdownDeadline(threading.Event(), seconds=9)
    owner = None
    code = 0
    deadline.install()
    try:
        configure_service_logging()
        if not postgres_requested():
            raise RuntimeConfigurationError('The foreground service requires STORAGE_BACKEND=postgres; use hbot start for file mode.')
        layout = ProcessLayout(RuntimeSettings.from_environment())
        AdminSettings.from_environment()  # Reject invalid auth before launching either child.

        def launch(role):
            if deadline.stop.is_set():
                owner.stopping = True
                raise OSError('Service is stopping')
            child = subprocess.Popen(child_command(role), env=child_environment(role, os.environ))
            if deadline.stop.is_set():
                owner.stopping = True
            return child

        owner = ChildSupervisor(launch, emit=log_child)
        watchdog = HealthWatchdog(owner)
        next_probe = 0
        LOGGER.info('Service supervisor starting', extra={'event': 'service_supervisor_starting'})
        while not deadline.stop.is_set() and not owner.stopping:
            owner.tick()
            if time.monotonic() >= next_probe and not owner.stopping:
                for role in owner.ROLES:
                    if deadline.stop.is_set():
                        break
                    port = layout.engine.port if role == 'engine' else layout.api.port
                    healthy, details = probe_health(port, probe_path(watchdog, role))
                    watchdog.observe(role, healthy, **details)
                next_probe = time.monotonic() + 1
            deadline.stop.wait(.05)
        code = owner.exit_code
    except Exception as error:
        LOGGER.error('Service supervisor stopped reason=%s type=%s', failure_reason(error), type(error).__name__,
                     extra={'event': 'service_failed', 'scope': 'supervisor', 'reason_code': failure_reason(error),
                            'error_type': type(error).__name__})
        code = failure_exit_code(error)
    finally:
        deadline.request()
        if owner is not None:
            owner.shutdown(budget=7.5)
            code = max(code, owner.exit_code)
        LOGGER.info('Service supervisor exited code=%s', code, extra={'event': 'service_exit', 'scope': 'supervisor'})
        deadline.close()
    return code
