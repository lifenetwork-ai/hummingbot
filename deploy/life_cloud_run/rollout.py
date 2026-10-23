"""Explicit release command: stop allocation, migrate in a separate Job, start.

Not invoked by worker startup or tests. Uses the operator/deployer identity;
worker IAM has neither migration-secret access nor Job execution permission.
No command is sent to a shell and child output may contain credentials, so only
fixed stage/result codes are logged. Exchange fencing/activation remains separate.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from hummingbot.strategy_v2.life_liquidity.storage.postgres.config import _dotenv

KEYS = frozenset(('LIFE_AUTO_MIGRATE', 'LIFE_CLOUD_PROJECT', 'LIFE_CLOUD_REGION', 'LIFE_CLOUD_MIGRATION_JOB',
                  'LIFE_CLOUD_TF_MODULE', 'LIFE_CLOUD_TF_VARS_FILE', 'LIFE_CLOUD_COMMAND_TIMEOUT_SECONDS'))


@dataclass(frozen=True)
class RolloutConfig:
    project: str
    region: str
    migration_job: str
    module: Path
    variables: Path
    auto_migrate: bool
    timeout_s: int

    def __post_init__(self):
        if (any(type(v) is not str or re.fullmatch(r'[a-z][a-z0-9-]{0,62}', v) is None for v in (self.project, self.region, self.migration_job))
                or any(not isinstance(v, Path) or not v.is_absolute() or v.resolve() != v for v in (self.module, self.variables))
                or type(self.auto_migrate) is not bool or type(self.timeout_s) is not int or not 1 <= self.timeout_s <= 7200):
            raise ValueError('ROLLOUT_CONFIG_INVALID')

    @classmethod
    def load(cls, path, environ):
        values = _dotenv(path, keys=KEYS)
        values.update({k: environ[k] for k in KEYS if k in environ})
        try:
            enabled = values['LIFE_AUTO_MIGRATE'].lower()
            if enabled not in ('true', 'false'):
                raise ValueError()
            return cls(values['LIFE_CLOUD_PROJECT'], values['LIFE_CLOUD_REGION'], values['LIFE_CLOUD_MIGRATION_JOB'],
                       Path(values['LIFE_CLOUD_TF_MODULE']), Path(values['LIFE_CLOUD_TF_VARS_FILE']), enabled == 'true',
                       int(values['LIFE_CLOUD_COMMAND_TIMEOUT_SECONDS']))
        except (KeyError, TypeError, ValueError):
            raise ValueError('ROLLOUT_CONFIG_INVALID') from None


def frozen_inputs(config):
    data = config.module / '.terraform'
    workspace = data / 'environment'
    if (data.is_symlink() or workspace.is_symlink()
            or workspace.exists() and (not workspace.is_file() or workspace.stat().st_size > 256
                                       or workspace.read_text().strip() != 'default')):
        raise ValueError('ROLLOUT_WORKSPACE_INVALID')
    # Terraform also loads JSON definitions and implicit variable files. They
    # must not bypass the same freeze applied to the explicit var-file.
    paths = set(config.module.glob('*.tf')) | set(config.module.glob('*.tf.json'))
    paths.update(config.module.glob('*.auto.tfvars'))
    paths.update(config.module.glob('*.auto.tfvars.json'))
    for name in ('terraform.tfvars', 'terraform.tfvars.json'):
        path = config.module / name
        if path.exists() or path.is_symlink():
            paths.add(path)
    if workspace.exists():
        paths.add(workspace)
    paths.update((config.module / '.terraform.lock.hcl', config.variables))
    paths = sorted(paths)
    if len(paths) < 3 or any(p.is_symlink() or not p.is_file() or p.stat().st_size > 1024 * 1024 for p in paths):
        raise ValueError('ROLLOUT_SOURCE_INVALID')
    return tuple((str(p), hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths)


def verify_rollout_identity(config, runner, env):
    """Read only the public release identity from the just-applied state."""
    command = ('terraform', '-chdir=' + str(config.module), 'output', '-json', 'life_rollout_identity')
    with tempfile.TemporaryFile() as output:
        result = runner(command, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.DEVNULL,
                        timeout=config.timeout_s, check=False, env=dict(env))
        if result.returncode:
            raise RuntimeError('ROLLOUT_IDENTITY_READ_FAILED')
        if output.seek(0, 2) > 8192:
            raise ValueError('ROLLOUT_IDENTITY_TOO_LARGE')
        output.seek(0)
        try:
            body = json.loads(output.read(8193))
            if (type(body) is not dict or set(body) != {
                    'project', 'region', 'migration_job', 'worker_instances', 'migration_image', 'worker_image'}
                    or (body['project'], body['region'], body['migration_job']) != (config.project, config.region, config.migration_job)
                    or type(body['worker_instances']) is not int or body['worker_instances'] != 0
                    or body['migration_image'] != body['worker_image'] or type(body['worker_image']) is not str
                    or re.fullmatch(r'[a-z0-9.-]+-docker.pkg.dev/[^ ]+@sha256:[0-9a-f]{64}', body['worker_image']) is None):
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError):
            raise ValueError('ROLLOUT_IDENTITY_UNQUALIFIED') from None


def rollout(config, *, runner=subprocess.run):
    env = dict(os.environ)
    if any(k.startswith(('TF_CLI_ARGS', 'TF_VAR_')) or k in ('TF_WORKSPACE', 'TF_DATA_DIR') for k in env):
        raise ValueError('ROLLOUT_AMBIENT_TERRAFORM_OVERRIDE')
    env.update(TF_IN_AUTOMATION='1', CHECKPOINT_DISABLE='1')
    frozen = frozen_inputs(config)
    prefix = ('terraform', '-chdir=' + str(config.module), 'apply', '-input=false', '-auto-approve', '-var-file=' + str(config.variables))
    commands = [('DRAIN_WORKER_ALLOCATION', (*prefix, '-var=worker_instances=0'))]
    if config.auto_migrate:
        commands.append(('MIGRATE', ('gcloud', 'run', 'jobs', 'execute', config.migration_job, '--project=' + config.project,
                                     '--region=' + config.region, '--wait', '--quiet')))
    commands.append(('START_UNARMED_WORKER', (*prefix, '-var=worker_instances=1')))
    for stage, command in commands:
        if frozen_inputs(config) != frozen:
            raise ValueError('ROLLOUT_SOURCE_CHANGED')
        result = runner(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=config.timeout_s, check=False, env=dict(env))
        if result.returncode:
            raise RuntimeError('ROLLOUT_STAGE_FAILED_' + stage)
        print(json.dumps(dict(component='life_rollout', event=stage, trading_permission=False)), flush=True)
        if stage == 'DRAIN_WORKER_ALLOCATION':
            if frozen_inputs(config) != frozen:
                raise ValueError('ROLLOUT_SOURCE_CHANGED')
            verify_rollout_identity(config, runner, env)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', required=True, type=Path)
    args = parser.parse_args()
    try:
        rollout(RolloutConfig.load(args.env_file, os.environ))
    except BaseException:
        print(json.dumps(dict(component='life_rollout', event='FAILED', trading_permission=False)), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
