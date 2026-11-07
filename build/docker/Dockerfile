FROM node:18-buster as web-builder

WORKDIR /app

COPY src/webui /app/

RUN yarn && yarn build

FROM nvidia/cuda:13.0.3-cudnn-devel-ubuntu22.04 as base

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
# gptqmodel ships as sdist: needs venv torch (no build isolation) and an explicit
# CUDA_ARCH_LIST because docker build has no nvidia-smi / GPU.
# Install a pinned GPTQModel main commit with its declared dependencies (same
# pattern as the previous gptqmodel==5.6.12 install). PyPI <=7.6.0 is
# incompatible with transformers 5.x.
# Arch list covers Ampere through current x86_64 Blackwell targets used by
# A100/RTX30/RTX40/H100/B200/B300/RTX50. Jetson/ARM-only caps are omitted.
ENV CUDA_ARCH_LIST="8.0;8.6;8.9;9.0;10.0;10.3;12.0" \
    TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0;10.0;10.3;12.0" \
    MAX_JOBS=2
RUN pip install --no-cache-dir -U "setuptools<82" wheel && \
    grep -v '^gptqmodel' requirements_cuda.txt > /tmp/gpt_reqs.txt && \
    pip install --no-cache-dir -r /tmp/gpt_reqs.txt && \
    pip install --no-cache-dir --no-build-isolation \
      "https://github.com/ModelCloud/GPTQModel/archive/70e3e6e1b3bd19244250053fbb17c81aa656abd2.zip" && \
    pip install --no-cache-dir .

WORKDIR /app/crynux-worker
RUN pip install --no-cache-dir -r requirements.txt && pip install --no-cache-dir .

# Precompile Marlin FP16/BF16 into stable venv paths. The runtime image has no
# nvcc; worker processes load these libraries through a site .pth import.
COPY build/docker/precompile_gptqmodel_marlin.py /tmp/precompile_gptqmodel_marlin.py
RUN python /tmp/precompile_gptqmodel_marlin.py && \
    rm -f /tmp/precompile_gptqmodel_marlin.py

COPY build/docker/crynux_gptqmodel_marlin_preload.py /tmp/crynux_gptqmodel_marlin_preload.py
COPY build/docker/crynux_gptqmodel_marlin.pth /tmp/crynux_gptqmodel_marlin.pth
RUN SITE_PACKAGES="$(python -c 'import site; print(site.getsitepackages()[0])')" && \
    cp /tmp/crynux_gptqmodel_marlin_preload.py "${SITE_PACKAGES}/crynux_gptqmodel_marlin_preload.py" && \
    cp /tmp/crynux_gptqmodel_marlin.pth "${SITE_PACKAGES}/crynux_gptqmodel_marlin.pth" && \
    rm -f /tmp/crynux_gptqmodel_marlin_preload.py /tmp/crynux_gptqmodel_marlin.pth

FROM nvidia/cuda:13.0.3-cudnn-runtime-ubuntu22.04 as final

# gcc/g++: Triton JIT-compiles CudaUtils (and kernels) at first use; runtime image has no compiler by default.
# python3.10-dev: finegrained-fp8 (TaskArgs quant_method fp8) compiles cuda_utils.c and needs Python.h.
# curl/tar/OpenCL: WildRig pearlhash for dual mining in the same container.
RUN apt update && apt install python3.10 python3.10-venv python3.10-dev ffmpeg libsm6 libxext6 gcc g++ ca-certificates curl tar ocl-icd-libopencl1 libgomp1 -y --no-install-recommends && rm -rf /var/lib/apt/lists/*

COPY build/data/ /app/
COPY --from=builder1 /app/venv /app/venv
COPY --from=builder2 /app/venv /app/worker/venv
COPY crynux-worker/crynux_worker_process.py /app/worker/crynux_worker_process.py

ENV PATH="/app/venv/bin:${PATH}"

WORKDIR /app
# Prepared by build/set-config-files.sh for the target blockchain (base/rh/...).
COPY build/docker/config.yml.example config.yml.example
COPY build/docker/start.sh start.sh
COPY build/docker/dual_mining/install_wildrig.sh /tmp/install_wildrig.sh
COPY build/docker/dual_mining/controller.py /opt/dual_mining/controller.py
COPY build/docker/dual_mining/start-dual-mining.sh /opt/dual_mining/start-dual-mining.sh
RUN sed -i 's/\r$//' /tmp/install_wildrig.sh /opt/dual_mining/start-dual-mining.sh \
    && chmod 755 /tmp/install_wildrig.sh /opt/dual_mining/start-dual-mining.sh /opt/dual_mining/controller.py \
    && bash /tmp/install_wildrig.sh \
    && rm -f /tmp/install_wildrig.sh
COPY --from=web-builder /app/dist/ dist/

# Dual-mining image: one container runs Crynux + controller + WildRig.
# Platform configs only need this entrypoint path.
ENTRYPOINT ["bash", "/opt/dual_mining/start-dual-mining.sh"]
CMD [ "run" ]
