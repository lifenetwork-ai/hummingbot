# Dedicated LIFE/OKX worker profile, not a general-purpose Hummingbot image.
# Both Python package sets are build inputs; no dependency solver runs here.
FROM continuumio/miniconda3:25.3.1-1@sha256:4a2425c3ca891633e5a27280120f3fb6d5960a0f509b7594632cdd5bb8cbaea8 AS dependencies

# Freeze the signed Debian package index, including transitive build libraries.
# Archived Release metadata has expired; signature verification remains enabled.
RUN rm -f /etc/apt/sources.list.d/debian.sources && \
    printf '%s\n' \
      'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian/20261023T000000Z bookworm main' \
      'deb [check-valid-until=no] https://snapshot.debian.org/archive/debian-security/20261023T000000Z bookworm-security main' \
      > /etc/apt/sources.list && \
    apt-get update && apt-get install -y --no-install-recommends \
      gcc=4:12.2.0-3 g++=4:12.2.0-3 python3-dev=3.11.2-1+b1 libusb-1.0-0=2:1.0.26-1 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/hummingbot
COPY setup/locks/life-worker-linux-64.conda.lock /opt/life-locks/conda.lock
COPY setup/locks/life-worker-linux-64.pip.lock /opt/life-locks/pip.lock
RUN test "$(uname -m)" = x86_64 && \
    conda create -y -n hummingbot --file /opt/life-locks/conda.lock && conda clean -afy
ENV PATH=/opt/conda/envs/hummingbot/bin:$PATH
RUN python -m pip install --no-deps --only-binary=:all: --require-hashes -r /opt/life-locks/pip.lock && \
    python -m pip check

FROM dependencies AS builder
COPY bin/ bin/
COPY hummingbot/ hummingbot/
COPY scripts/ scripts/
COPY controllers/ controllers/
COPY migrations/ migrations/
COPY setup/locks/ setup/locks/
COPY setup.py LICENSE README.md ./
ARG HUMMINGBOT_BUILD_JOBS=2
ENV SOURCE_DATE_EPOCH=0 PYTHONHASHSEED=0
RUN HUMMINGBOT_BUILD_JOBS=$HUMMINGBOT_BUILD_JOBS python setup.py build_ext --inplace && \
    rm -rf build && find . -type f -name '*.cpp' -delete
RUN /opt/conda/bin/conda list -p /opt/conda/envs/hummingbot --explicit --sha256 > build-conda-explicit.txt && python -m pip freeze > build-pip-freeze.txt

# Removing packages in this intermediate stage avoids retaining their bytes in
# runtime layers. The original build environment and compiled ABI stay intact.
FROM dependencies AS runtime-dependencies
COPY setup/locks/life-worker-linux-64.runtime.conda.lock /opt/life-locks/runtime.conda.lock
COPY setup/life_runtime_dependencies.py /tmp/life_runtime_dependencies.py
RUN /opt/conda/bin/python /tmp/life_runtime_dependencies.py \
      --prefix /opt/conda/envs/hummingbot --locks /opt/life-locks --conda /opt/conda/bin/conda

FROM continuumio/miniconda3:25.3.1-1@sha256:4a2425c3ca891633e5a27280120f3fb6d5960a0f509b7594632cdd5bb8cbaea8 AS life-worker
COPY --from=runtime-dependencies /opt/conda/envs/hummingbot/ /opt/conda/envs/hummingbot/
COPY --from=runtime-dependencies /opt/life-locks/ /opt/life-locks/
COPY --from=builder /home/hummingbot/ /home/hummingbot/
RUN /opt/conda/bin/conda list -p /opt/conda/envs/hummingbot --explicit --sha256 > /home/hummingbot/conda-explicit.txt && \
    /opt/conda/envs/hummingbot/bin/python -m pip freeze > /home/hummingbot/pip-freeze.txt && \
    groupadd --gid 10001 life && \
    useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin life
WORKDIR /home/hummingbot
ENV PATH=/opt/conda/envs/hummingbot/bin:$PATH LIFE_ENV_FILE="" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
ENTRYPOINT ["python", "-m", "hummingbot.strategy_v2.life_liquidity.storage.postgres.worker"]
