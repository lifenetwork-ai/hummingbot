# Cloud Run runtime image

Build the service artifact with `make build-cloudrun`; `CLOUDRUN_IMAGE` selects
its local tag. The upstream `release` / `legacy` targets retain their original
development/interactive packaging. See the [deployment guide](../deploy/cloudrun/README.md)
for service configuration and release verification.

The `cloudrun` target reuses the builder's compiled Cython extensions and Python
environment at `/opt/conda/envs/hummingbot`. A packaging stage removes compiler,
Conda manager and test tooling using installed package manifests, then copies
the prepared environment and application into the pinned Debian slim runtime.
Runtime dependency versions remain those resolved by the builder. Native
libraries and connector SDKs required by discovery and strategies are retained.
The installed package inventory is `/home/hummingbot/cloudrun-runtime-packages.json`.

The service runs as UID 10001 with packaged SQL and non-secret configuration.
Runtime roots can be read-only; temporary state and Numba cache use `/tmp`.
Valid installed Python bytecode caches are retained, while orphan caches of
removed modules are discarded. Rebuilding dependencies belongs in the builder.

## Images and containers

Images are stored artifacts; containers are processes started from them.
`docker image ls` lists images and `docker ps` lists running containers. Docker's
uncompressed image size, registry download size and process RAM are different
measurements. Multiple tags with the same image ID share stored layers.

`hummingbot-cloudrun:local` is the ordinary local build tag. Offline E2E briefly
uses a loopback registry and a `127.0.0.1:PORT/hummingbot:offline` tag to resolve
an immutable manifest, then removes that registry. Verification creates temporary
database/exchange/client containers, including overlapping old/new bot revisions
for ownership takeover tests. Normal operation needs one service container per
logical bot, connected to PostgreSQL. A local database may use its own container;
Cloud SQL is an external database service.

Builder cache does not ship as part of the runtime image. Keep image selection
separate from cache cleanup; avoid broad prune commands affecting other projects.

## Verification

The offline harness enforces its image-size budget and checks native imports,
Numba JIT, TA-Lib, TLS trust, connector discovery, CLI and packaged migrations in
a network-disabled, read-only container. Release evidence must match the chosen
image/manifest. Packaging changes require fresh image verification.

Historical sizes, package inventories and startup measurements are in the
[image optimization report](../reports/cloudrun-image-optimization.md) and
[PostgreSQL verification report](../reports/postgres-verification.md).
