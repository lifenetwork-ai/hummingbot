# Cloud Run image size and Docker references

The runtime image was reduced from **4,637,764,625 bytes (4.64 GB)** to
**1,363,640,076 bytes (1.36 GB)**, a **70.60% reduction**. These are Docker's
uncompressed image/layer sizes, not registry download sizes or process RAM.
Measurements and package inventories are saved in
`build/cloudrun-image-optimization/`.

## Why the old image was large

The old cloud target inherited the entire upstream Miniconda release image and
copied all of `/opt/conda` from the builder in a 3.83 GB layer. This included the
base Conda installation and the complete build/test environment. The application
itself was about 20 MB.

The installed environment included roughly 664 MiB of Rust compiler files,
159 MiB of Rust standard libraries, 473 MiB of Linux sysroot, 339 MiB of GCC,
49 MiB of binutils and 102 MiB of Rattler build/solver Python packages. Package
sizes can overlap at shared paths; they must not be summed as independent Docker
layers. Headers, static archives, docs and Python bytecode added more build data.

## Packaging changes

- The existing builder still creates the same environment and compiled Cython
  extensions. Runtime dependency versions are retained; no second package solve
  or pip upgrade changes the SDK/numerical versions during packaging.
- A separate `cloudrun-artifacts` stage removes explicit compiler, Conda manager,
  build/test packages, headers, static archives and documentation using the
  installed Conda file manifests. Files also owned by retained packages survive.
  Python sources and valid runtime bytecode caches remain; orphaned caches of
  removed modules are discarded and bytecode-only distributions are preserved.
  Empty package directories are removed so optional imports such as Numba's coverage integration fail cleanly.
- Only the prepared application environment at its original prefix
  `/opt/conda/envs/hummingbot` and the application are copied to a digest-pinned
  Debian trixie-slim runtime. This matches the Debian/glibc family of the builder
  and avoids copying the base Conda installation. Build-time deletions do not
  become hidden bytes in earlier runtime layers.
- The foreground entrypoint, UID 10001, read-only deployment configuration,
  packaged SQL, health checks, signal handling and runtime `/tmp` cache paths are
  preserved. The packaged runtime inventory is
  `/home/hummingbot/cloudrun-runtime-packages.json`.

The environment's regular-file footprint falls from 3,322,499,698 to
1,264,264,546 bytes. Retained Python distribution versions were compared before
and after; all match, including 16 explicitly checked native/runtime/connector
dependencies. Numerical libraries and optional connector SDKs are retained because
connector discovery and existing strategies use them.

Build the optimized service using `make build-cloudrun`. The upstream `release` /
`legacy` targets continue to serve the original development/interactive image;
their full environment is separate from this runtime target. Updating runtime
dependencies or rebuilding extensions belongs in the builder/new image workflow.

## Images, tags and running containers

Docker images are stored artifacts. Containers are processes started from them.
Use `docker ps` for running processes; a row in `docker image ls` does not mean a
bot is running. At the start of this review there were no Hummingbot containers
running.

`hummingbot-cloudrun:local` is the ordinary build tag. The E2E harness temporarily
pushes to a loopback-only registry to resolve a genuine immutable registry manifest
digest, then removes the registry and runs test containers by that digest.
`127.0.0.1:PORT/hummingbot:offline` is its preparation tag. It is not a second bot
or a cloud deployment. Tags with the same image ID share the same stored image;
the listed full sizes should not be added together.

The references observed in the original question were different revisions:
`127.0.0.1:59141/...` pointed to previous image `85f1bde14797`, while the build tag
pointed to `98fb93cb91ee`. That build also had alias `127.0.0.1:57409/...` with the
same `98fb93cb91ee` ID. The previous revision was retained to test application
rollback. The upgrade scenario deliberately runs old/new revisions together to
verify that the PostgreSQL ownership fence permits only one authorized sender.
Temporary registry/exchange/PG/CLI and image-retention containers are removed after
verification. Normal operation starts one service container for the logical bot.

Docker build cache is separate from runtime image size. Retaining compilers in
builder cache speeds future Cython builds and does not ship them to Cloud Run.
Do not use broad prune commands to reclaim another project's images or data.

## Runtime cache and startup tradeoff

The first 1.19 GB trial removed all source-backed bytecode caches. It passed all
five E2E scenarios, but local startup rose to 15.3–17.2s while the full-image
predecessor started in about 7.6s in the same upgrade scenario. A read-only service
cannot persist recompilation caches across process replacement.

The final package therefore retains valid runtime caches and removes only
orphaned caches of deleted modules. This adds about 169 MB while preserving the
major size reduction. The trial report remains at
`build/e2e-offline/20261025-004244-f71b33/result.json`; it is not the final selected
runtime. Final startup and release evidence are linked below.

## Verification

The E2E runner now rejects a current deployment image over **1.5 GiB**. Its new
network-disabled, read-only runtime smoke step checks:

- Cython clock/order-book/PMM imports and V2 orchestration imports;
- connector discovery, including OKX spot/perpetual and 63 registered connector settings;
- real Numba JIT and TA-Lib calculation after compiler removal;
- TLS trust-store availability, `hbot --version`, and all nine SQL scripts;
- absence of GCC, Rust, Conda, Cython and pytest tooling in the runtime.

Native regression: **950 passed**. Packager tests cover shared ownership, bytecode
preservation, empty optional-package cleanup and rejection of paths escaping the
prefix; measured packager coverage is **90.18%**. Two pre-existing config tests
expected migration default `true` although the committed centralized default was
already `false`. Their expectations/docs were aligned with the declared `false`
value, and explicit true/false override tests were added. Production migration
defaults were not changed by the image optimization; deployment examples continue
to explicitly enable automatic migration.

All five final-image E2E scenarios passed with `release_scope_complete=true`:
`build/e2e-offline/20261025-011532-ec2194/result.json`. Final local startup samples
are **6.884–8.407s**; the new image starts in **7.636s** in the upgrade scenario,
versus **8.912s** for the full predecessor and **7.608s** on rollback. These are
local workload samples, not Cloud Run measurements. The synthetic deployment
review passes against the exact tested manifest.

Current image E2E and manifest evidence are recorded in
[postgres-verification.md](postgres-verification.md). PostgreSQL SQL/storage code
is unchanged by packaging; the preceding release's 72 native PostgreSQL integration
tests are historical evidence, not a rerun claimed here. The new full-image suite
exercises the actual PostgreSQL driver and migrations/recovery in the slim image.
