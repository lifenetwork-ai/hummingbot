import subprocess
import sys
from pathlib import Path

import pytest

from scripts.test_mm_load import assets_for


def test_load_assets_include_standalone_activity_helper_and_all_existing_fixtures(tmp_path):
    assets_for(tmp_path)
    module = tmp_path / 'load_mm_activity.py'
    assert module.read_bytes() == Path('test/e2e/offline/load_mm_activity.py').read_bytes()
    subprocess.run([sys.executable, '-c', 'from load_mm_activity import ActivityRecorder, VenueQuotes, VenueSampler'], cwd=tmp_path, check=True)


@pytest.mark.parametrize('options', [[], ['--profile', 'all', '--incident-diagnostics'], ['--profile', 'mixed', '--incident-diagnostics']])
def test_activity_cli_cannot_relabel_extra_instrumentation_as_release_acceptance(options):
    result = subprocess.run([sys.executable, 'scripts/test_mm_load.py', '--mm-activity', *options], capture_output=True, text=True)
    assert result.returncode == 2
    assert 'MM activity requires --profile probe and --incident-diagnostics' in result.stderr
