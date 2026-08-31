$ErrorActionPreference = "Stop"

$root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$python = Join-Path $root ".venv\Scripts\python.exe"
$configExample = Join-Path $root "config\settings.example.json"
$backendDist = Join-Path $root "electron\backend-dist"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing Python virtual environment: $python"
}
if (-not (Test-Path -LiteralPath $configExample)) {
    throw "Missing example config: $configExample"
}

Push-Location $root
try {
    if (Test-Path -LiteralPath $backendDist) {
        Remove-Item -LiteralPath $backendDist -Recurse -Force
    }
    & $python -m PyInstaller `
        --noconfirm `
        --clean `
        --name ToolDownloadVideoBackend `
        --distpath "electron\backend-dist" `
        --workpath "build\backend" `
        --specpath "build" `
        --collect-all yt_dlp `
        --collect-all curl_cffi `
        --collect-submodules app `
        --hidden-import backend.main `
        --add-data "$configExample;config" `
        main.py
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
