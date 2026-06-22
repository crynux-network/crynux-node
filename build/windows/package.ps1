# Package the app using pyinstaller
# Generate a directory that contains the releasing app
# Example call: .\build\windows\package.ps1

param(
    [string]$BLOCKCHAIN = ""
)

$ErrorActionPreference = "Stop"

function Check-ExitCode {
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Last command failed with exit code $LASTEXITCODE"
        exit 1
    }
}

./venv/Scripts/Activate.ps1
pyinstaller crynux.spec
Check-ExitCode


./worker/venv/Scripts/Activate.ps1

# # change in controlnet_aux/zoe/zoedepth/models/layers/attractor.py
$TAR_FILE = "worker/venv/Lib/site-packages/controlnet_aux/zoe/zoedepth/models/layers/attractor.py"

if(Test-Path $TAR_FILE -PathType Leaf) {
    (Get-Content $TAR_FILE) | ForEach-Object {$_ -replace "@torch.jit.script", "#@torch.jit.script"} | Set-Content -Path $TAR_FILE
}

pyinstaller crynux_worker_process.spec
Check-ExitCode

Move-Item -Path "dist/crynux_worker_process" "dist/Crynux Node/crynux_worker_process"

Copy-Item -Recurse "data" "dist/Crynux Node/data"

New-Item -ItemType Directory -Path "dist/Crynux Node/webui"
Copy-Item -Recurse "webui/dist" "dist/Crynux Node/webui/dist"

Copy-Item -Recurse "res" "dist/Crynux Node/res"

# Create the archive file
$VERSION = "3.1.0"
$BLOCKCHAIN_SUFFIX = ""
if ($BLOCKCHAIN -ne "") {
    $BLOCKCHAIN_SUFFIX = "-$BLOCKCHAIN"
}
$RELEASE_NAME = "crynux-node-lithium-v${VERSION}${BLOCKCHAIN_SUFFIX}-windows-x64"

Move-Item -Path "dist/Crynux Node" "dist/$RELEASE_NAME"

Compress-Archive -Path "dist/$RELEASE_NAME" -DestinationPath "dist/$RELEASE_NAME.zip"
