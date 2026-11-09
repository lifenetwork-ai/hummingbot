"""A failed isolated fixture cannot change the following packaged scenarios."""
from types import SimpleNamespace

import pytest

from scripts import mm_quote_refresh_checks


@pytest.mark.parametrize('failed', [False, True])
def test_rolling_fixture_restores_original_catalogue_even_after_failure(tmp_path, monkeypatch, failed):
    original = b'# exact original bytes\nmarkets: {}\n'
    path = tmp_path / 'life_mm_profiles.yaml'
    path.write_bytes(original)
    harness = SimpleNamespace(assets=tmp_path)

    def run(candidate):
        assert candidate is harness
        path.write_text('temporary acceptance policy')
        if failed:
            raise RuntimeError('fixture failure')
        return 'completed'

    monkeypatch.setattr(mm_quote_refresh_checks, '_run', run)
    if failed:
        with pytest.raises(RuntimeError, match='fixture failure'):
            mm_quote_refresh_checks.scenario(harness)
    else:
        assert mm_quote_refresh_checks.scenario(harness) == 'completed'
    assert path.read_bytes() == original
