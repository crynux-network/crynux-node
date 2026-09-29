FROM node:18-buster as web-builder

WORKDIR /app

COPY src/webui /app/

RUN yarn && yarn build

FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04 as base

RUN apt update && apt install python3.10 python3.10-venv python3.10-dev python3-pip curl build-essential libssl-dev libffi-dev ffmpeg libsm6 libxext6 -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

FROM base as builder1

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

FROM base as builder2

WORKDIR /app

RUN python3 -m venv venv
ENV PATH="/app/venv/bin:${PATH}"

COPY stable-diffusion-task stable-diffusion-task
COPY gpt-task gpt-task
COPY crynux-worker crynux-worker

WORKDIR /app/stable-diffusion-task
RUN pip install --no-cache-dir -r requirements_cuda.txt && pip install --no-cache-dir .

WORKDIR /app/gpt-task
RUN pip install --no-cache-dir -r requirements_cuda.txt && pip install --no-cache-dir .

WORKDIR /app/crynux-worker
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir .

FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04 as final

RUN apt update && apt install python3.10 python3.10-venv ffmpeg libsm6 libxext6 -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

COPY build/data/ /app/
COPY --from=builder1 /app/venv /app/venv
COPY --from=builder2 /app/venv /app/worker/venv
COPY crynux-worker/crynux_worker_process.py /app/worker/crynux_worker_process.py

ENV PATH="/app/venv/bin:${PATH}"

WORKDIR /app
COPY build/docker/config.yml.example config.yml.example
COPY build/docker/start.sh start.sh
COPY --from=web-builder /app/dist/ dist/

ENTRYPOINT ["bash", "start.sh"]
CMD [ "run" ]
