# Example call: bash package.sh -s 124AC5EE

while getopts ":s:" opt; do
  case $opt in
    s) IDENTITY="$OPTARG"
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

## Package the worker
source worker/venv/bin/activate
# change in controlnet_aux/zoe/zoedepth/models/layers/attractor.py
TAR_FILE=worker/venv/lib/python3.10/site-packages/controlnet_aux/zoe/zoedepth/models/layers/attractor.py
sed -i.bak "s/@torch.jit.script/#@torch.jit.script/g" $TAR_FILE

if [ "$IDENTITY" ]; then
  pyinstaller crynux_worker_process.spec -- --identity "$IDENTITY"
else
  pyinstaller crynux_worker_process.spec
fi

## Package the node
## The worker, webui, res, data will be collected into the node package
## as described in the crynux.spec file

source venv/bin/activate
if [ "$IDENTITY" ]; then
  pyinstaller crynux.spec -- --identity "$IDENTITY"
else
  pyinstaller crynux.spec
fi

## Embed the onedir worker next to other binaries in the .app bundle
cp -R "dist/crynux_worker_process" "dist/Crynux Node.app/Contents/Frameworks/crynux_worker_process"

if [ "$IDENTITY" ]; then
  codesign --deep --force --options runtime \
    --entitlements entitlements.plist \
    -s "$IDENTITY" \
    "dist/Crynux Node.app" || exit 1
fi

cd dist

[ -e Crynux.dmg ] && rm Crynux.dmg
create-dmg \
    --volname "Crynux Node" --volicon "../res/icon.icns" \
    --window-pos 200 120 --window-size 800 400 --icon-size 100 \
    --icon "Crynux Node.app" 200 190 --hide-extension "Crynux Node.app" \
    --app-drop-link 600 185 \
    "Crynux Node.dmg" "Crynux Node.app"
