$ErrorActionPreference = "Stop"

$root = Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")
$python = Join-Path $root ".venv\Scripts\python.exe"
$configExample = Join-Path $root "config\settings.example.json"
$backendDist = Join-Path $root "electron\backend-dist"
$ffmpeg = Join-Path $root "tools\ffmpeg\bin\ffmpeg.exe"
$ffprobe = Join-Path $root "tools\ffmpeg\bin\ffprobe.exe"
$node = (Get-Command node -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing Python virtual environment: $python"
}
if (-not (Test-Path -LiteralPath $configExample)) {
    throw "Missing example config: $configExample"
}
if (-not (Test-Path -LiteralPath $ffmpeg) -or -not (Test-Path -LiteralPath $ffprobe)) {
    Write-Host "Bundled FFmpeg is missing; downloading the verified Windows build..."
    & (Join-Path $PSScriptRoot "install-ffmpeg.ps1")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
if (-not (Test-Path -LiteralPath $node)) {
    throw "A supported Node.js runtime is required to bundle YouTube's challenge solver."
}
if (-not (Test-Path -LiteralPath $ffmpeg) -or -not (Test-Path -LiteralPath $ffprobe)) {
    throw "FFmpeg installation did not produce ffmpeg.exe and ffprobe.exe."
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
        --add-binary "$ffmpeg;tools/ffmpeg/bin" `
        --add-binary "$ffprobe;tools/ffmpeg/bin" `
        --add-binary "$node;tools/node" `
        main.py
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}
finally {
    Pop-Location
}
