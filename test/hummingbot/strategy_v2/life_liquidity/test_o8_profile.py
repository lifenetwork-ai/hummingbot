"""Diagnostic counters must preserve I/O and retain failed-stage evidence."""
import os
from pathlib import Path
from test.hummingbot.strategy_v2.life_liquidity.o8_profile import StageRecorder

import pytest

from hummingbot.strategy_v2.life_liquidity import checkpoint_archive, lazy_archive


def test_profile_counts_aliased_reads_once_and_restores_io(tmp_path):
    source = tmp_path / "example.json"
    source.write_bytes(b"12345")
    original = (Path.read_bytes, checkpoint_archive.read_limited, lazy_archive.read_limited, os.fsync)
    recorder = StageRecorder(profile=True)
    with pytest.MonkeyPatch.context() as patch:
        with recorder.instruments(patch), recorder.stage("reads"):
            assert source.read_bytes() == b"12345"
            assert checkpoint_archive.read_limited(source, 5) == b"12345"
            assert lazy_archive.read_limited(source, 5) == b"12345"
    assert original == (Path.read_bytes, checkpoint_archive.read_limited, lazy_archive.read_limited, os.fsync)
    row = recorder.export(tmp_path)[0]
    assert row["reads"]["successful_calls"] == 3
    assert row["reads"]["returned_bytes"] == 15
    assert row["reads"]["distinct_paths"] == 1
    assert (tmp_path / row["raw_profile"]).is_file()


def test_profile_preserves_capacity_errors_and_failed_stage(tmp_path):
    source = tmp_path / "example.json"
    source.write_bytes(b"12345")
    recorder = StageRecorder(profile=True)
    with pytest.MonkeyPatch.context() as patch, recorder.instruments(patch):
        with pytest.raises(ValueError, match="ARCHIVE_CAPACITY_EXCEEDED"), recorder.stage("failed_read"):
            checkpoint_archive.read_limited(source, 4)
    assert recorder.current is None
    row = recorder.export(tmp_path)[0]
    assert row["name"] == "failed_read"
    assert row["reads"]["successful_calls"] == 0
    assert row["wall_ms"] > 0


def test_timing_mode_does_not_install_io_or_cpu_instrumentation(tmp_path):
    original = (Path.read_bytes, checkpoint_archive.read_limited, os.fsync)
    recorder = StageRecorder()
    with pytest.MonkeyPatch.context() as patch, recorder.instruments(patch), recorder.stage("timing"):
        assert original == (Path.read_bytes, checkpoint_archive.read_limited, os.fsync)
    row = recorder.export(tmp_path)[0]
    assert "raw_profile" not in row
    assert row["reads"]["successful_calls"] == 0
