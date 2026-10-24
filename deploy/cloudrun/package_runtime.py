"""Remove build-only files from the compiled environment before the final COPY.

Keep installed runtime versions and prefix paths intact. Conda package manifests
identify compiler/test/tooling files; numerical, connector and SDK dependencies
remain available. This script runs only in a disposable Docker build stage.
"""
import argparse
import importlib.util
import json
import shutil
from pathlib import Path, PurePosixPath

BUILD_ONLY_PACKAGES = frozenset({
    "autopep8", "aioresponses", "aiounittest", "binutils_impl_linux-64", "binutils_linux-64",
    "conda", "conda-build", "conda-index", "conda-launchers", "conda-libmamba-solver", "conda-lockfiles",
    "conda-package-handling", "conda-pypi", "conda-rattler-solver", "conda-self",
    "conda-package-streaming", "coverage", "cython", "diff-cover", "flake8",
    "gcc", "gcc_impl_linux-64", "gcc_linux-64", "gxx", "gxx_impl_linux-64", "gxx_linux-64",
    "kernel-headers_linux-64", "libgcc-devel_linux-64", "libstdcxx-devel_linux-64",
    "pre-commit", "py-rattler", "py-rattler-build", "pytest", "pytest-asyncio", "pytest-timeout", "rattler-build",
    "rust", "rust-std-x86_64-unknown-linux-gnu", "rust_linux-64", "sysroot_linux-64",
})


def regular_file_bytes(root):
    """Count hard-linked files once; symlinks do not add their target's size."""
    seen = set()
    total = 0
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            info = path.stat()
            identity = (info.st_dev, info.st_ino)
            if identity not in seen:
                total += info.st_size
                seen.add(identity)
    return total


def package_path(prefix, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Package manifest contains a path outside the runtime prefix.")
    path = prefix / relative
    if not path.parent.resolve().is_relative_to(prefix.resolve()):
        raise ValueError("Package manifest traverses a directory outside the runtime prefix.")
    return path


def remove_orphan_caches(root):
    for path in root.rglob("*.pyc"):
        try:
            source = Path(importlib.util.source_from_cache(str(path)))
        except ValueError:
            # Preserve distributions that intentionally ship only bytecode.
            continue
        # Valid runtime caches avoid recompiling SDKs on every read-only boot.
        # Caches for removed tooling must not leave optional namespace modules.
        if not source.is_file():
            path.unlink()


def remove_empty_directories(root):
    # Empty package directories can become namespace modules: Numba's optional
    # coverage import must fail cleanly after coverage has been removed.
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and not path.is_symlink():
            try:
                path.rmdir()
            except OSError:
                pass


def prepare_runtime(prefix, application):
    prefix, application = Path(prefix), Path(application)
    manifests = [json.loads(path.read_text()) for path in (prefix / "conda-meta").glob("*.json")]
    if not manifests or not (prefix / "bin/python").is_file():
        raise ValueError("A populated compiled Conda environment is required.")
    before = regular_file_bytes(prefix)
    retained_files = {name for item in manifests if item["name"] not in BUILD_ONLY_PACKAGES
                      for name in item.get("files", [])}
    removed = []
    retained = []
    for item in manifests:
        entry = {"name": item["name"], "version": item["version"]}
        if item["name"] not in BUILD_ONLY_PACKAGES:
            retained.append(entry)
            continue
        removed.append(entry)
        for name in item.get("files", []):
            if name not in retained_files:
                path = package_path(prefix, name)
                if path.is_file() or path.is_symlink():
                    path.unlink()
    # Runtime keeps shared libraries, Python sources, SDK metadata and assets.
    # Headers/static archives and the Conda manager are build/update concerns.
    for relative in ("include", "conda-meta", "share/doc", "share/man", "share/info"):
        shutil.rmtree(prefix / relative, ignore_errors=True)
    for path in prefix.rglob("*.a"):
        path.unlink()
    remove_orphan_caches(prefix)
    remove_orphan_caches(application)
    shutil.rmtree(application / "scripts-copy", ignore_errors=True)
    remove_empty_directories(prefix)
    remove_empty_directories(application)
    return {"environment_bytes_before": before, "environment_bytes_after": regular_file_bytes(prefix),
            "removed_build_packages": sorted(removed, key=lambda item: item["name"]),
            "retained_packages": sorted(retained, key=lambda item: item["name"])}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prefix", required=True, type=Path)
    parser.add_argument("--application", required=True, type=Path)
    args = parser.parse_args()
    report = prepare_runtime(args.prefix, args.application)
    (args.application / "cloudrun-runtime-packages.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key != "retained_packages"}))


if __name__ == "__main__":
    main()
