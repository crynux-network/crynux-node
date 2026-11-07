#!/bin/bash
# Install pinned WildRig Multi into the dual-mining image.
set -eu

WILDRIG_VERSION="${WILDRIG_VERSION:-0.51.9}"
WILDRIG_URL="${WILDRIG_URL:-https://github.com/andru-kun/wildrig-multi/releases/download/${WILDRIG_VERSION}/wildrig-multi-linux-${WILDRIG_VERSION}.tar.gz}"
WILDRIG_SHA256="${WILDRIG_SHA256:-7444c6c48b70006c2ec045bd69a37393c2fc293c7e47cdf561c9561408d5bb44}"
WILDRIG_HOME="${WILDRIG_HOME:-/opt/wildrig}"

mkdir -p "${WILDRIG_HOME}"
curl -fsSL "${WILDRIG_URL}" -o /tmp/wildrig.tar.gz
echo "${WILDRIG_SHA256}  /tmp/wildrig.tar.gz" | sha256sum -c -
tar -xzf /tmp/wildrig.tar.gz -C "${WILDRIG_HOME}"
rm -f /tmp/wildrig.tar.gz

if [ -f "${WILDRIG_HOME}/wildrig-multi" ]; then
  ln -sf "${WILDRIG_HOME}/wildrig-multi" /usr/local/bin/wildrig
elif [ -f "${WILDRIG_HOME}/wildrig" ]; then
  ln -sf "${WILDRIG_HOME}/wildrig" /usr/local/bin/wildrig
else
  BIN="$(find "${WILDRIG_HOME}" -type f -name 'wildrig*' ! -name '*.sh' ! -name '*.bat' | head -n 1)"
  test -n "${BIN}"
  ln -sf "${BIN}" /usr/local/bin/wildrig
fi
chmod a+x /usr/local/bin/wildrig
