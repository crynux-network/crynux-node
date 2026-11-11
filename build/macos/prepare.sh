#!/bin/bash
set -e

# Build package from source
# Example call: bash build/macos/prepare.sh -w ~/crynux_app

while getopts ":w:" opt; do
  case $opt in
    w) WORK_DIR="$OPTARG"
    ;;
    \?) echo "Invalid option -$OPTARG" >&2
    exit 1
    ;;
  esac

  case $OPTARG in
    -*) echo "Option $opt needs a valid argument"
    exit 1
    ;;
  esac
done

if [[ -z $WORK_DIR ]]; then
  echo "Please specify the working directory"
  exit 1
fi

# https://github.com/AnyLifeZLB/FaceVerificationSDK/blob/main/install_newest_mediapipe_on_macos.md
export SYSTEM_VERSION_COMPAT=0

# Must use arm64 version of Python
# CONDA_SUBDIR=osx-arm64 conda create -n crynux python=3.10
# python > 3.10.2 is required
arch=$(python3.10 -c "import platform;print(platform.uname())")
if [[ $arch == *"x86_64"* ]]; then
  echo "Please use the python in arm64 arch"
  exit 1
fi

if [[ -d "$WORK_DIR" ]]; then
  echo "Deleting old package folder"
  rm -r $WORK_DIR
fi

echo "Creating package folder"
mkdir $WORK_DIR

WORK_DIR=$(realpath $WORK_DIR)
echo "Package folder: $WORK_DIR"

GIT_DIR=$(pwd)
GIT_DIR=$(realpath $GIT_DIR)
echo "Workspace folder: $GIT_DIR"


if ! [ -x "$(command -v brew)" ]; then
  echo 'Error: brew is not installed.'
  exit 1
fi

function check_or_install {
  if ! [ -x "$(command -v $1)" ]
  then
    echo "$1 is not installed. Installing..."
    brew install "$1"
  fi
}

check_or_install create-dmg

# 1. Prepare the WebUI dist

echo "[Prepare] Building the WebUI..."

mkdir "$WORK_DIR/webui"

cd "$GIT_DIR/src/webui"

corepack enable
yarn --immutable && yarn build

if [ ! -f "dist/index.html" ]; then
  echo "Error: WebUI build failed, 'dist/index.html' not found." >&2
  exit 1
fi

cp -R "$GIT_DIR/src/webui/dist" "$WORK_DIR/webui/dist"

echo "[Prepare] WebUI build completed"

# 2. Prepare the server
echo "[Prepare] Preparing the server..."

cd $WORK_DIR
python3.10 -m venv venv
export PATH="$WORK_DIR/venv/bin:${PATH}"
mkdir "$WORK_DIR/crynux-node"
cd "$WORK_DIR/crynux-node"
cp -R $GIT_DIR/src src
cp -R $GIT_DIR/res ../res
cp $GIT_DIR/pyproject.toml pyproject.toml
cp $GIT_DIR/setup.py setup.py
cp $GIT_DIR/requirements_desktop.txt requirements.txt
cp $GIT_DIR/MANIFEST.in MANIFEST.in
cp $GIT_DIR/go.mod go.mod
cp $GIT_DIR/go.sum go.sum
pip install -r requirements.txt && pip install .[app]

echo "[Prepare] Server prepared"

# 3. Prepare the worker
echo "[Prepare] Preparing the worker..."

cd $GIT_DIR
git submodule update --init --recursive
cd $WORK_DIR
mkdir "$WORK_DIR/worker"
cp $GIT_DIR/crynux-worker/crynux_worker_process.py worker/

python3.10 -m venv worker/venv
source "$WORK_DIR/worker/venv/bin/activate"
pip install pyinstaller==6.22.3

echo "[Prepare] Preparing the stable-diffusion-task..."

cp -R $GIT_DIR/stable-diffusion-task stable-diffusion-task
cd stable-diffusion-task
pip install -r requirements_macos.txt
pip install .
cd $WORK_DIR

echo "[Prepare] Preparing the gpt-task..."

cp -R $GIT_DIR/gpt-task gpt-task
cd gpt-task
pip install -r requirements_macos.txt
pip install .

echo "[Prepare] Preparing the crynux-worker..."

cp -R $GIT_DIR/crynux-worker crynux-worker
cd crynux-worker
pip install -r requirements.txt
pip install .

echo "[Prepare] Worker prepared"

if pip show triton > /dev/null 2>&1; then
    echo "Uninstalling triton..."
    pip uninstall triton -y
fi

echo "[Prepare] Triton checked"

# 4. Prepare the data
echo "Preparing the data..."

cd $WORK_DIR

cp -R $GIT_DIR/build/data .
cp $GIT_DIR/build/macos/config.yml.example data/config/config.yml
cp $GIT_DIR/build/macos/* .

echo "Data prepared"
