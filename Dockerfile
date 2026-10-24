# Reuse the existing Miniconda build, with the reviewed base pinned by digest.
ARG BASE_IMAGE=continuumio/miniconda3@sha256:eca594d684f495c1a02beff33a9fab53aec8c5830eaf431bb149912dc6c9e4c1
ARG CLOUDRUN_BASE_IMAGE=debian:trixie-slim@sha256:62bd423aeee912639ca8ee681c2b762cb3e25137e0a92b20be455898a9f034ad
FROM ${BASE_IMAGE} AS dependencies

# Install system dependencies
RUN apt-get update && \
    apt-get install -y sudo libusb-1.0 gcc g++ python3-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/hummingbot

# Create conda environment
COPY setup/environment.yml /tmp/environment.yml
RUN conda env create -f /tmp/environment.yml && \
    conda clean -afy && \
    rm /tmp/environment.yml

ENV PATH=/opt/conda/envs/hummingbot/bin:$PATH

FROM dependencies AS builder

# Copy remaining files
COPY bin/ bin/
COPY hummingbot/ hummingbot/
COPY scripts/ scripts/
COPY controllers/ controllers/
COPY docs/api/openapi.json docs/api/openapi.json
COPY scripts/ scripts-copy/
COPY setup.py .
COPY LICENSE .
COPY README.md .

# activate hummingbot env when entering the CT
SHELL [ "/bin/bash", "-lc" ]
RUN echo "conda activate hummingbot" >> ~/.bashrc

COPY setup/pip_packages.txt /tmp/pip_packages.txt
RUN python3 -m pip install --no-deps -r /tmp/pip_packages.txt && \
    rm /tmp/pip_packages.txt


ARG BUILD_JOBS=2
RUN HBOT_BUILD_JOBS=${BUILD_JOBS} python3 setup.py build_ext --inplace -j ${BUILD_JOBS} && \
    rm -rf build/ && \
    find . -type f -name "*.cpp" -delete


# Build final image using artifacts from builder
FROM ${BASE_IMAGE} AS release

# Dockerfile author / maintainer
LABEL maintainer="Fede Cardoso @dardonacci <federico@hummingbot.org>"

# Build arguments
ARG BRANCH=""
ARG COMMIT=""
ARG BUILD_DATE=""
LABEL branch=${BRANCH}
LABEL commit=${COMMIT}
LABEL date=${BUILD_DATE}

# Set ENV variables
ENV COMMIT_SHA=${COMMIT}
ENV COMMIT_BRANCH=${BRANCH}
ENV BUILD_DATE=${BUILD_DATE}

ENV INSTALLATION_TYPE=docker

# Install system dependencies
RUN apt-get update && \
    apt-get install -y sudo libusb-1.0 && \
    rm -rf /var/lib/apt/lists/*

# Create mount points
RUN mkdir -p /home/hummingbot/conf /home/hummingbot/conf/connectors /home/hummingbot/conf/strategies /home/hummingbot/conf/controllers /home/hummingbot/conf/scripts /home/hummingbot/logs /home/hummingbot/data /home/hummingbot/certs /home/hummingbot/scripts /home/hummingbot/controllers

WORKDIR /home/hummingbot

# Copy all build artifacts from builder image
COPY --from=builder /opt/conda/ /opt/conda/
COPY --from=builder /home/ /home/

# Put the hummingbot env on PATH so non-login shells (e.g. `docker exec … hbot`) find the env's python
# + console scripts without `conda activate`, and expose the `hbot` CLI there (mirrors make install).
# This lets the image run as a single-bot container: `docker run … hbot start <config>`,
# `docker exec … hbot status`.
ENV PATH=/opt/conda/envs/hummingbot/bin:$PATH
RUN ln -sf /home/hummingbot/bin/hbot /opt/conda/envs/hummingbot/bin/hbot

# Setting bash as default shell because we have .bashrc with customized PATH (setting SHELL affects RUN, CMD and ENTRYPOINT, but not manual commands e.g. `docker run image COMMAND`!)
SHELL [ "/bin/bash", "-lc" ]

# Set the default command to run when starting the container

CMD conda activate hummingbot && ./bin/hummingbot_quickstart.py 2>> ./logs/errors.log

# Reuse the compiled dependency versions while excluding build/test toolchains.
# Pruning in a separate stage ensures discarded files never enter runtime layers.
FROM builder AS cloudrun-artifacts
COPY deploy/cloudrun/package_runtime.py /tmp/package_runtime.py
RUN /opt/conda/envs/hummingbot/bin/python /tmp/package_runtime.py \
        --prefix /opt/conda/envs/hummingbot --application /home/hummingbot && \
    rm /tmp/package_runtime.py

# Same Debian/glibc family as the pinned compiler image, without base Conda.
# Only reviewed non-secret deployment assets and the application environment ship.
FROM ${CLOUDRUN_BASE_IMAGE} AS cloudrun
ARG BRANCH=""
ARG COMMIT=""
ARG BUILD_DATE=""
LABEL branch=${BRANCH} commit=${COMMIT} date=${BUILD_DATE}
ENV COMMIT_SHA=${COMMIT} \
    COMMIT_BRANCH=${BRANCH} \
    BUILD_DATE=${BUILD_DATE} \
    INSTALLATION_TYPE=docker \
    PATH=/opt/conda/envs/hummingbot/bin:$PATH
RUN apt-get update && \
    apt-get install -y --no-install-recommends ca-certificates libusb-1.0-0 && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /home/hummingbot
COPY --from=cloudrun-artifacts /opt/conda/envs/hummingbot/ /opt/conda/envs/hummingbot/
COPY --from=cloudrun-artifacts /home/hummingbot/ /home/hummingbot/
RUN mkdir -p conf/connectors conf/strategies conf/controllers conf/scripts logs data certs && \
    ln -sf /home/hummingbot/bin/hbot /opt/conda/envs/hummingbot/bin/hbot
COPY deploy/cloudrun/conf/ conf/
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NUMBA_CACHE_DIR=/tmp/numba \
    HOME=/tmp/hummingbot
USER 10001:10001
EXPOSE 8080
STOPSIGNAL SIGTERM
ENTRYPOINT []
CMD ["python", "-m", "hummingbot.cli.service"]

# Keep an unqualified upstream docker build's original quickstart behavior.
# Cloud builds always explicitly select --target cloudrun.
FROM release AS legacy
