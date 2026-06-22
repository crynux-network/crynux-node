#!/bin/bash
set -e

while getopts ":s:u:p:t:" opt; do
  case $opt in
    s) IDENTITY="$OPTARG"
    ;;
    u) APPLE_USER="$OPTARG"
      ;;
    p) APPLE_PASS="$OPTARG"
      ;;
    t) APPLE_TEAM_ID="$OPTARG"
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

echo "Preparing the dist project and install the environments"
## Prepare the dist project and install the environments
./build/macos/prepare.sh -w ./build/crynux_node || exit 1

echo "Preparing completed"

## Build the dist bundle
cd ./build/crynux_node || exit 1

if [ "$IDENTITY" ]; then
  echo "Packaging using identity: $IDENTITY"
  ./package.sh -s "$IDENTITY" || exit 1
else
  echo "Packaging using local developer identity"
  ./package.sh || exit 1
fi

if [ "$IDENTITY" ]; then
  ## Sign the DMG file
  echo "Signing the DMG file"
  codesign -s "$IDENTITY" "dist/Crynux Node.dmg" || exit 1
fi

if [ "$IDENTITY" ]; then
  echo "Notarizing the DMG file"
  ## Sign the app and send it to apple for notarization
  python notarize.py \
    --package "dist/Crynux Node.dmg" \
    --username "$APPLE_USER" \
    --team "$APPLE_TEAM_ID" \
    --password "$APPLE_PASS" || exit 1
fi

VERSION=3.1.0

if [ "$IDENTITY" ]; then
  mv "dist/Crynux Node.dmg" "dist/crynux-node-lithium-v${VERSION}-mac-arm64-signed.dmg" || exit 1
else
  mv "dist/Crynux Node.dmg" "dist/crynux-node-lithium-v${VERSION}-mac-arm64-unsigned.dmg" || exit 1
fi
