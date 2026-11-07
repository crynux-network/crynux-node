#!/bin/bash
# Single-container entry for Crynux + Pearl dual mining.
# Dockerfile / platform configs should point ENTRYPOINT at this script only.

set -eu

cd /app

if [ ! -f config/config.yml ]; then
  mkdir -p config
  cp config.yml.example config/config.yml
fi

CONTROLLER_PID=""

cleanup() {
  if [ -n "${CONTROLLER_PID}" ] && kill -0 "${CONTROLLER_PID}" 2>/dev/null; then
    echo "[start-dual-mining] stopping dual_controller pid=${CONTROLLER_PID}"
    kill -TERM "${CONTROLLER_PID}" 2>/dev/null || true
    wait "${CONTROLLER_PID}" 2>/dev/null || true
  fi
  if [ -n "${CRYNUX_PID:-}" ] && kill -0 "${CRYNUX_PID}" 2>/dev/null; then
    echo "[start-dual-mining] stopping crynux pid=${CRYNUX_PID}"
    kill -TERM "${CRYNUX_PID}" 2>/dev/null || true
    wait "${CRYNUX_PID}" 2>/dev/null || true
  fi
}

trap cleanup TERM INT EXIT

echo "[start-dual-mining] starting dual_controller"
/app/venv/bin/python3 /opt/dual_mining/controller.py &
CONTROLLER_PID=$!

CMD_ARG="${1:-run}"
echo "[start-dual-mining] starting crynux_server (${CMD_ARG})"
/app/venv/bin/python3 -m crynux_server.main "${CMD_ARG}" &
CRYNUX_PID=$!

wait "${CRYNUX_PID}"
EXIT_CODE=$?
cleanup
trap - TERM INT EXIT
exit "${EXIT_CODE}"
