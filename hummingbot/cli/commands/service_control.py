"""Versioned CLI control; these commands never start an engine or send exchange orders."""
import time
from pathlib import Path

from hummingbot.cli import bot
from hummingbot.cli.output import ExitCode, fail
from hummingbot.model.bot_state_store import ControlConflict


def request_control(*, desired_state, file=None, config_type=None, replace=False, timeout=0,
                    expected_version=None):
    store = bot.state_store()
    store.ensure()
    row = store.read()
    if desired_state == "running" and row["desired_state"] == "running" and not replace:
        fail("a bot is already requested/running; stop it or use --replace", ExitCode.ERROR)
    expected = row["control_version"] if expected_version is None else expected_version
    try:
        version = store.control(expected_version=expected, desired_state=desired_state,
                                config_file_path=file, config_type=config_type)
    except ControlConflict as error:
        fail(str(error), ExitCode.CONFIG_ERROR)
    record = {"config": file or row["config_file_path"], "desired_state": desired_state,
              "control_version": version, "status": "requested"}
    if timeout <= 0:
        return record
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        latest = store.read()
        if latest["control_version"] != version:
            fail("request was superseded by another control command; inspect status", ExitCode.CONFIG_ERROR)
        status = bot.read_status() or {}
        if latest["acknowledged_version"] == version and not status.get("stale", True):
            if status.get("error"):
                fail("service could not apply the configuration; inspect status/logs", ExitCode.ERROR)
            running = bool(status.get("running"))
            if running == (desired_state == "running"):
                return {**record, "status": "running" if running else "stopped", "stopped": not running}
        time.sleep(min(0.2, max(0, deadline - time.monotonic())))
    fail(f"request {version} is persisted; service acknowledgment timed out (inspect status)", ExitCode.TIMEOUT)


def launch_service_config(*, file, v1, v2, controller, replace, foreground, password_stdin,
                          auto_set_permissions, timeout, expected_version=None):
    from hummingbot.cli.commands._common import one_type
    from hummingbot.cli.strategy_configs import resolve_config_type
    if foreground or password_stdin or auto_set_permissions is not None:
        fail("PostgreSQL start controls the service; run hbot serve for the foreground process. "
             "Credentials use the environment and deployed configuration is read-only.", ExitCode.CONFIG_ERROR)
    if file is None:
        loaded = bot.read_loaded()
        if not loaded:
            fail("no configuration selected; import a YAML configuration first", ExitCode.CONFIG_ERROR)
        file, stype = loaded["file"], loaded["type"]
    else:
        explicit = one_type(v1, v2, controller, required=False)
        snapshot = bot.read_status() or {}
        catalogue = snapshot.get("available_configs") or {}
        matches = [kind for kind, files in catalogue.items() if file in files]
        try:
            if explicit and explicit in matches:
                stype = explicit
            elif len(matches) == 1 and not explicit:
                stype = matches[0]
            elif catalogue:
                raise ValueError("Configuration/type is absent or ambiguous in the deployed config set.")
            else:
                stype = resolve_config_type(file, explicit)
        except (ValueError, FileNotFoundError) as error:
            fail(str(error), ExitCode.CONFIG_ERROR)
    if Path(file).is_absolute() or ".." in Path(file).parts:
        fail("use a stable relative YAML configuration identity", ExitCode.CONFIG_ERROR)
    catalogue = (bot.read_status() or {}).get("available_configs") or {}
    if catalogue and file not in catalogue.get(stype, []):
        fail("selected configuration is not in the service's deployed config set", ExitCode.CONFIG_ERROR)
    return request_control(desired_state="running", file=file, config_type=stype, replace=replace,
                           timeout=timeout, expected_version=expected_version)
