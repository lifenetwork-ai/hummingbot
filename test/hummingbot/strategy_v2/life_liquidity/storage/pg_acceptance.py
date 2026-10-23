"""Serial offline acceptance map. Run artifacts belong under /tmp only.

This runner never deploys, contacts an exchange, edits a plan or closes a phase.
Successful subprocesses cannot waive missing implementation or product gates.
"""
import argparse
import fcntl
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[5]
BASE = 'test/hummingbot/strategy_v2/life_liquidity'
STORAGE = BASE + '/storage'
BUDGETS = 'docs/plans/evidence/life_o8_offline_budgets.json'
BUDGET_SHA = 'e1be70f238efdaad68086e3ae43fdc8ca4447a4c6da6dc36c03260ca7e6be5c3'  # noqa: documentation


@dataclass(frozen=True)
class Gate:
    name: str
    owner: str
    requirements: tuple[str, ...]
    kind: str
    selectors: tuple[str, ...] = ()


GATES = (
    Gate('file_contracts', 'storage', ('PG1', 'PG8.1', 'PG8.6'), 'pytest', (STORAGE, '--ignore=' + STORAGE + '/postgres')),
    Gate('native', 'storage', ('PG2', 'PG3', 'PG4', 'PG5', 'PG6.5', 'PG8.1', 'PG8.2'), 'postgres', ('all',)),
    Gate('physical_pitr', 'storage', ('PG6.5', 'PG8.2'), 'postgres', ('pg6_pitr',)),
    Gate('foreground', 'worker', ('PG7.2', 'PG7.7'), 'pytest', tuple(STORAGE + '/' + name for name in (
        'test_worker_supervisor.py', 'test_cloud_rollout.py', 'test_postgres_headless.py',
        'test_headless_service.py', 'test_service_startup.py', 'test_service_runner.py'))),
    Gate('linux_image', 'worker', ('PG7.1', 'PG7.2', 'PG7.7'), 'image'),
    Gate('infrastructure', 'deployment definitions', ('PG7.3', 'PG7.4', 'PG7.5', 'PG7.6'), 'terraform'),
    Gate('product_o8', 'strategy', ('PG8.3', 'PG8.5', 'PG8.6', 'O.8'), 'product'),
)

# These are implementation/qualification gaps, not environment approvals. Keep
# them here until corresponding implementations AND executable gates exist.
OPEN_REQUIREMENTS = {
    'PG0': 'Remaining offline design/fixture/cost comparison decisions in the plan.',
    'PG6.1': 'Complete stopped legacy/segmented File graph and recorder cut with old-writer/key fence.',
    'PG6.2': 'Resumable native historical import, original basis and all permanent identities.',
    'PG6.3': 'Exact financial/identity/decision parity and atomic unarmed activation.',
    'PG6.4': 'Before-activity discard and reconciled post-activity controlled rollback rehearsal.',
    'PG6.5': 'Post-cut reconciliation, independently signed exclusion and controlled new generation after quarantine.',
    'PG6.6': 'Import/reconciliation and 1k/10k retained-history capacity and recovery headroom.',
    'PG7.1': 'Complete dependency reproducibility and actual pinned Linux artifact qualification.',
    'PG7.7': 'Actual worker/container forced loss with original unknown financial work.',
    'PG7.8': 'Complete bounded native metrics and calibrated local alert fixtures.',
    'PG8.3': 'Every mandatory O.8 outcome plus native PostgreSQL 0/1k/10k sender/watchdog variants.',
    'PG8.4': 'Resource/query/byte/WAL measurements and adverse pool/concurrency headroom.',
    'PG8.6': 'Complete existing coverage/completion acceptance and all new PG gates.',
}


def eligible(name):
    p = Path(name)
    if p.name.startswith('.env') and p.name != '.env.example':
        return False
    return (name in ('Dockerfile', 'Makefile', 'pyproject.toml', '.env.example', '.pre-commit-config.yaml')
            or name.startswith(('hummingbot/', 'controllers/', 'migrations/life_liquidity/', 'setup/',
                                'deploy/life_cloud_run/', BASE + '/', 'test/hummingbot/strategy_v2/executors/',
                                'docs/examples/life_liquidity/'))
            or name in (BUDGETS, 'docs/plans/evidence/life_o8_acceptance_manifest.json',
                        'docs/plans/LIFE_OKX_IMPLEMENTATION_PLAN_TDD.md', 'docs/plans/LIFE_POSTGRES_CLOUD_RUN_IMPLEMENTATION_PLAN_TDD.md'))


def source_manifest(root):
    names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=root).decode().split('\0')
    # Host native tests load ignored Cython artifacts. Python source hashes alone
    # would miss replacement of an extension during a long qualification.
    names += [str(p.relative_to(root)) for p in (root / 'hummingbot').rglob('*.so')]
    result = {}
    for name in sorted(set(n for n in names if n and eligible(n))):
        path = root / name
        if (path.is_symlink() or not path.is_file() or '..' in Path(name).parts or Path(name).is_absolute()
                or not path.resolve().is_relative_to(root.resolve())
                or any((root / Path(*Path(name).parts[:i])).is_symlink() for i in range(1, len(Path(name).parts)))):
            raise ValueError('SOURCE_UNSAFE')
        result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


@contextmanager
def qualification_lock(path=None):
    path = Path('/tmp') / ('life-pg-acceptance-' + str(os.getuid()) + '.lock') if path is None else path
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_uid != os.getuid() or stat.S_IMODE(status.st_mode) != 0o600:
            raise ValueError('ACCEPTANCE_LOCK_UNSAFE')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def xml_counts(path):
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == 'testsuite' else list(root.findall('testsuite'))
    counts = {k: sum(int(s.get(k, '0')) for s in suites) for k in ('tests', 'errors', 'failures', 'skipped')}
    if not suites or counts['tests'] <= 0 or len(root.findall('.//testcase')) != counts['tests']:
        raise ValueError('TEST_REPORT_INCOMPLETE')
    return counts


def gate_result(gate, folder, code):
    row = dict(owner=gate.owner, requirements=gate.requirements, exit_code=code, qualified=False)
    try:
        if gate.kind == 'terraform':
            log = (folder / 'integration.log').read_text()
            expected = sum(len(re.findall(r'^run\s+"', p.read_text(), re.M)) for p in (ROOT / 'deploy/life_cloud_run/tests').glob('*.tftest.hcl'))
            row['qualified'] = code == 0 and expected > 0 and re.search(r'^Success! ' + str(expected) + r' passed, 0 failed\.$', log, re.M) is not None
        elif gate.kind == 'product':
            report = json.loads((folder / 'summary.json').read_text())
            tests = json.loads((folder / 'tests.json').read_text())
            row['qualified'] = (code == 0 and report.get('offline_complete') is True and report.get('unresolved_review') == []
                                and bool(tests) and report.get('test_count') == len(tests)
                                and report.get('skipped') == report.get('failed') == 0
                                and report.get('test_results_sha256') == hashlib.sha256((folder / 'tests.json').read_bytes()).hexdigest()
                                and all(t == dict(setup='passed', call='passed', teardown='passed') for t in tests.values()))
        else:
            row['counts'] = xml_counts(folder / 'integration.xml')
            row['qualified'] = code == 0 and not any(row['counts'][k] for k in ('errors', 'failures', 'skipped'))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ET.ParseError):
        row['report_invalid'] = True
    return row


def verdict(rows, *, source_unchanged, remaining):
    return source_unchanged and not remaining and set(rows) == {g.name for g in GATES} and all(r.get('qualified') is True for r in rows.values())


def command(gate, folder, *, image, terraform, candidate=False):
    python = sys.executable
    if gate.kind == 'postgres':
        return [python, '-m', BASE.replace('/', '.') + '.storage.run_postgres', '--suite', gate.selectors[0], '--output', str(folder)]
    if gate.kind == 'image':
        return [python, '-m', BASE.replace('/', '.') + '.storage.run_worker_image', '--image', image, '--output', str(folder)]
    if gate.kind == 'product':
        # Explicit candidate runs retain all tests but never qualify closure.
        return [python, '-m', BASE.replace('/', '.') + '.o8_acceptance', *(['--candidate'] if candidate else []), '--output-dir', str(folder)]
    if gate.kind == 'terraform':
        return [terraform]
    folder.mkdir()
    return [python, '-m', 'pytest', '-q', '--import-mode=importlib', *gate.selectors, '--junitxml=' + str(folder / 'integration.xml')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--linux-image', required=True)
    parser.add_argument('--terraform', required=True, type=Path)
    parser.add_argument('--candidate', action='store_true', help='Run open product regressions without claiming completion')
    args = parser.parse_args()
    folder = args.output.resolve()
    if (not folder.is_relative_to(Path('/tmp').resolve()) or not re.fullmatch(r'sha256:[0-9a-f]{64}', args.linux_image)
            or not args.terraform.is_absolute() or not args.terraform.is_file()):
        parser.error('Use a new /tmp output, immutable local image ID and explicit Terraform binary')
    if hashlib.sha256((ROOT / BUDGETS).read_bytes()).hexdigest() != BUDGET_SHA:
        parser.error('Frozen O.8 budgets changed')
    with qualification_lock():
        return run(args)


def run(args):
    folder = args.output.resolve()
    folder.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = source_manifest(ROOT)
    env = {k: v for k, v in os.environ.items() if not k.startswith(('TF_CLI_ARGS', 'TF_VAR_'))}
    env.update(LIFE_ENV_FILE='', LIFE_AUTO_MIGRATE='false', PYTEST_ADDOPTS='', TF_IN_AUTOMATION='1', CHECKPOINT_DISABLE='1')
    report = dict(format=1, offline_complete=False, trading_permission=False, source=source,
                  python=sys.version, platform=platform.platform(), image=args.linux_image,
                  resource_profile=dict(postgres_cpu=2, postgres_memory_bytes=1073741824,
                                        image_cpu=2, image_memory_bytes=2147483648, external_load_qualified=False),
                  terraform_sha256=hashlib.sha256(args.terraform.read_bytes()).hexdigest(),
                  gates={}, remaining=OPEN_REQUIREMENTS, source_unchanged=True)

    def persist():
        report['offline_complete'] = verdict(report['gates'], source_unchanged=report['source_unchanged'], remaining=report['remaining'])
        (folder / 'acceptance.json').write_text(json.dumps(report, sort_keys=True, indent=2) + '\n')
    persist()
    for gate in GATES:
        if source_manifest(ROOT) != source:
            report['source_unchanged'] = False
            persist()
            return 2
        destination = folder / gate.name
        argv = command(gate, destination, image=args.linux_image, terraform=str(args.terraform), candidate=args.candidate)
        report['gates'][gate.name] = dict(owner=gate.owner, requirements=gate.requirements, qualified=False, state='RUNNING')
        persist()  # Process loss retains an incomplete gate, never a green one.
        print(json.dumps(dict(event='GATE_STARTED', gate=gate.name)), flush=True)
        with (folder / (gate.name + '.log')).open('w') as stream:
            if gate.kind == 'terraform':
                destination.mkdir()
                calls = (('version', '-json'), ('init', '-backend=false', '-input=false', '-no-color'),
                         ('fmt', '-check', '-recursive'), ('validate', '-no-color'), ('test', '-no-color'))
                code = 0
                with (destination / 'integration.log').open('w') as log:
                    for call in calls:
                        if call[0] == 'version':
                            version = subprocess.check_output([*argv, *call], cwd=ROOT, env=env)
                            if json.loads(version)['terraform_version'] != '1.11.4':
                                code = 2
                                break
                            continue
                        code = subprocess.run([*argv, '-chdir=' + str(ROOT / 'deploy/life_cloud_run'), *call], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
                        if code:
                            break
            else:
                code = subprocess.run(argv, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT).returncode
        report['gates'][gate.name] = gate_result(gate, destination, code)
        report['source_unchanged'] = source_manifest(ROOT) == source
        persist()
        print(json.dumps(dict(event='GATE_FINISHED', gate=gate.name, qualified=report['gates'][gate.name]['qualified'])), flush=True)
    return 0 if report['offline_complete'] else 2


if __name__ == '__main__':
    sys.exit(main())
