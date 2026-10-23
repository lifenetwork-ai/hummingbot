# Set the base image
FROM continuumio/miniconda3:25.3.1-1@sha256:4a2425c3ca891633e5a27280120f3fb6d5960a0f509b7594632cdd5bb8cbaea8 AS dependencies

# Install system dependencies
RUN apt-get update && \
    apt-get install -y sudo libusb-1.0 gcc g++ python3-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /home/hummingbot

# Create conda environment
COPY setup/environment.yml /tmp/environment.yml
RUN sed -i 's/  - defaults/  - nodefaults/;s/  - python>=3.10.12/  - python=3.13.15/;s/  - sqlalchemy>=1.4.49/  - sqlalchemy=2.1.1/' /tmp/environment.yml && \
    conda config --system --set channel_priority strict && \
    conda env create -f /tmp/environment.yml && \
    conda clean -afy && \
    rm /tmp/environment.yml

# All installation/build commands must use the environment, including noninteractive RUN.
ENV PATH=/opt/conda/envs/hummingbot/bin:$PATH

# activate hummingbot env when entering the CT
SHELL [ "/bin/bash", "-lc" ]
RUN echo "conda activate hummingbot" >> ~/.bashrc

COPY setup/pip_packages.txt /tmp/pip_packages.txt
RUN python3 -m pip install --no-deps -r /tmp/pip_packages.txt && \
    rm /tmp/pip_packages.txt

# Optional PG runtime/migration driver; file-only images retain their dependency set.
ARG LIFE_POSTGRES=false
COPY setup/life_postgres_requirements.txt /tmp/life_postgres_requirements.txt
RUN case "$LIFE_POSTGRES" in \
      true) python3 -m pip install -r /tmp/life_postgres_requirements.txt ;; \
      false) ;; \
      *) exit 2 ;; \
    esac && rm /tmp/life_postgres_requirements.txt

# Dependency installation stays cached when application source changes.
FROM dependencies AS builder
COPY bin/ bin/
COPY hummingbot/ hummingbot/
COPY scripts/ scripts/
COPY controllers/ controllers/
COPY migrations/ migrations/
COPY setup.py .
COPY LICENSE .
COPY README.md .

ARG HUMMINGBOT_BUILD_JOBS=2
RUN HUMMINGBOT_BUILD_JOBS=$HUMMINGBOT_BUILD_JOBS python3 setup.py build_ext --inplace && \
    rm -rf build/ && \
    find . -type f -name "*.cpp" -delete

# Capture the resolved dependency set in the artifact (no claim of a transitive lock).
RUN conda list -n hummingbot --explicit > /home/hummingbot/conda-explicit.txt && \
    python -m pip freeze > /home/hummingbot/pip-freeze.txt


# Build final image using artifacts from builder
FROM continuumio/miniconda3:25.3.1-1@sha256:4a2425c3ca891633e5a27280120f3fb6d5960a0f509b7594632cdd5bb8cbaea8 AS release

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
COPY --from=dependencies /opt/conda/envs/hummingbot/ /opt/conda/envs/hummingbot/
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

# Opt-in native foreground worker. Keep the ordinary image default below.
FROM release AS life-worker
RUN /opt/conda/envs/hummingbot/bin/python -c "import asyncpg, psycopg" && \
    groupadd --gid 10001 life && \
    useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin life
ENV LIFE_ENV_FILE="" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER 10001:10001
ENTRYPOINT ["python", "-m", "hummingbot.strategy_v2.life_liquidity.storage.postgres.worker"]
CMD []

# Preserve `docker build .` and --target release semantics for existing users.
FROM release AS default
