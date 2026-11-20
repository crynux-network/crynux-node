FROM node:18-buster AS web-builder

WORKDIR /app

COPY src/webui /app/

RUN yarn && yarn build

FROM nvidia/cuda:13.0.3-cudnn-devel-ubuntu22.04 AS node-build-base

RUN apt update && apt install python3.10 python3.10-venv python3.10-dev python3-pip curl build-essential libssl-dev libffi-dev ffmpeg libsm6 libxext6 -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

FROM node-build-base AS builder1

RUN curl -O -L "https://golang.org/dl/go1.21.0.linux-amd64.tar.gz" && tar -C /usr/local -xzf go1.21.0.linux-amd64.tar.gz
ENV PATH="${PATH}:/usr/local/go/bin"
ENV CGO_ENABLED="1"

WORKDIR /app

RUN python3 -m venv venv
ENV PATH="/app/venv/bin:${PATH}"

WORKDIR /app/crynux-node

COPY src src
COPY pyproject.toml pyproject.toml
COPY setup.py setup.py
COPY requirements_docker.txt requirements.txt
COPY MANIFEST.in MANIFEST.in
COPY go.mod go.mod
COPY go.sum go.sum
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir .

# Pinned crynux-node-base with worker heavy deps + Marlin.
# After rebuilding base, replace the tag with the new short commit from GHCR.
FROM ghcr.io/crynux-network/crynux-node-base:REPLACE_AFTER_BASE_BUILD AS worker-venv

ENV PATH="/app/venv/bin:${PATH}"

WORKDIR /app

COPY stable-diffusion-task stable-diffusion-task
COPY gpt-task gpt-task
COPY crynux-worker crynux-worker

WORKDIR /app/stable-diffusion-task
RUN pip install --no-cache-dir --no-deps .

WORKDIR /app/gpt-task
RUN pip install --no-cache-dir --no-deps .

WORKDIR /app/crynux-worker
RUN pip install --no-cache-dir --no-deps .

FROM nvidia/cuda:13.0.3-cudnn-runtime-ubuntu22.04 AS final

# gcc/g++: Triton JIT-compiles CudaUtils (and kernels) at first use; runtime image has no compiler by default.
# python3.10-dev: finegrained-fp8 (TaskArgs quant_method fp8) compiles cuda_utils.c and needs Python.h.
RUN apt update && apt install python3.10 python3.10-venv python3.10-dev ffmpeg libsm6 libxext6 gcc g++ -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

COPY build/data/ /app/
COPY --from=builder1 /app/venv /app/venv
COPY --from=worker-venv /app/venv /app/worker/venv
COPY crynux-worker/crynux_worker_process.py /app/worker/crynux_worker_process.py

ENV PATH="/app/venv/bin:${PATH}"

WORKDIR /app
COPY build/docker/config.yml.example config.yml.example
COPY build/docker/start.sh start.sh
COPY --from=web-builder /app/dist/ dist/

ENTRYPOINT ["bash", "start.sh"]
CMD [ "run" ]
