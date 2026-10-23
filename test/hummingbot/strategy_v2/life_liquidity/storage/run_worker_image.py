"""Qualify a locally built immutable Linux worker image; no external network."""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', args.image):
        parser.error('Use a locally built immutable image ID')
    output = args.output.resolve()
    if not output.is_relative_to(Path('/tmp').resolve()):
        parser.error('Use /tmp for disposable qualification output')
    output.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, 'LIFE_PG_WORKER_IMAGE': args.image, 'LIFE_ENV_FILE': '', 'LIFE_AUTO_MIGRATE': 'false'}
    command = [sys.executable, '-m', 'pytest', '-q', str(Path(__file__).with_name('worker_image_cases.py')),
               '--junitxml=' + str(output / 'integration.xml')]
    with (output / 'integration.log').open('w') as stream:
        result = subprocess.run(command, env=env, stdout=stream, stderr=subprocess.STDOUT)
    print((output / 'integration.log').read_text())
    return result.returncode


if __name__ == '__main__':
    sys.exit(main())
