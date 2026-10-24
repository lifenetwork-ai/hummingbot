import importlib.util
import json
from pathlib import Path

import pytest

from deploy.cloudrun.package_runtime import prepare_runtime


def package(prefix, name, files):
    metadata = prefix / "conda-meta"
    metadata.mkdir(exist_ok=True)
    for filename in files:
        target = prefix / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("package content")
    (metadata / f"{name}.json").write_text(json.dumps({"name": name, "version": "1", "files": files}))


def test_runtime_removes_toolchains_and_retains_runtime_and_shared_files(tmp_path):
    prefix, application = tmp_path / "env", tmp_path / "application"
    prefix.mkdir()
    application.mkdir()
    package(prefix, "python", ["bin/python", "lib/python3.13/os.py"])
    package(prefix, "gcc_impl_linux-64", ["bin/gcc", "lib/shared.so", "lib/gcc/libcompiler.a"])
    package(prefix, "libgcc", ["lib/shared.so", "lib/libgcc_s.so"])
    package(prefix, "numpy", ["lib/python3.13/site-packages/numpy/__init__.py", "lib/blas.so"])
    package(prefix, "coverage", ["lib/python3.13/site-packages/coverage/__init__.py",
                                 "lib/python3.13/site-packages/coverage/htmlfiles/index.html"])
    for filename in ("include/python.h", "share/doc/compiler.txt", "lib/libpython.a"):
        target = prefix / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("build-only data")
    source = prefix / "lib/python3.13/os.py"
    cache = Path(importlib.util.cache_from_source(str(source)))
    cache.parent.mkdir()
    cache.write_bytes(b"cache")
    sourceless = prefix / "lib/python3.13/only_bytecode.pyc"
    sourceless.write_bytes(b"preserve")
    orphan = Path(importlib.util.cache_from_source(str(prefix / "lib/python3.13/site-packages/coverage/__init__.py")))
    orphan.parent.mkdir()
    orphan.write_bytes(b"generated cache not included in package manifest")

    report = prepare_runtime(prefix, application)

    assert (prefix / "bin/python").is_file()
    assert (prefix / "lib/shared.so").is_file()
    assert (prefix / "lib/libgcc_s.so").is_file()
    assert (prefix / "lib/blas.so").is_file()
    assert source.is_file() and sourceless.is_file()
    assert cache.exists()
    assert not orphan.exists()
    assert not (prefix / "bin/gcc").exists()
    assert not (prefix / "include").exists()
    assert not (prefix / "lib/python3.13/site-packages/coverage").exists()
    assert not list(prefix.rglob("*.a"))
    assert report["environment_bytes_after"] < report["environment_bytes_before"]
    assert [item["name"] for item in report["removed_build_packages"]] == ["coverage", "gcc_impl_linux-64"]


@pytest.mark.parametrize("filename", ["../outside", "/outside", "link/outside"])
def test_package_metadata_cannot_delete_outside_runtime_prefix(tmp_path, filename):
    prefix, application, outside = tmp_path / "env", tmp_path / "app", tmp_path / "outside"
    prefix.mkdir()
    application.mkdir()
    package(prefix, "python", ["bin/python"])
    outside.write_text("must survive")
    (prefix / "link").symlink_to(tmp_path, target_is_directory=True)
    (prefix / "conda-meta/gcc.json").write_text(json.dumps({"name": "gcc", "version": "1", "files": [filename]}))
    with pytest.raises(ValueError, match="outside the runtime prefix"):
        prepare_runtime(prefix, application)
    assert outside.read_text() == "must survive"
