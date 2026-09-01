$ErrorActionPreference = "Stop"

$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$target = Join-Path $root "tools\ffmpeg\bin"
$archiveUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$hashUrl = "$archiveUrl.sha256"
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("tool-download-video-ffmpeg-" + [guid]::NewGuid().ToString("N"))
$archive = Join-Path $tempRoot "ffmpeg.zip"
$expanded = Join-Path $tempRoot "expanded"

New-Item -ItemType Directory -Path $tempRoot,$expanded -Force | Out-Null
try {
    Invoke-WebRequest -Uri $archiveUrl -OutFile $archive
    $expectedLine = (Invoke-WebRequest -Uri $hashUrl).Content.Trim()
    $expected = ($expectedLine -split "\s+")[0].ToLowerInvariant()
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash.ToLowerInvariant()
    if (-not $expected -or $actual -ne $expected) {
        throw "FFmpeg archive checksum mismatch."
    }

    Expand-Archive -LiteralPath $archive -DestinationPath $expanded -Force
    $ffmpeg = Get-ChildItem -LiteralPath $expanded -Recurse -Filter "ffmpeg.exe" -File | Select-Object -First 1
    $ffprobe = Get-ChildItem -LiteralPath $expanded -Recurse -Filter "ffprobe.exe" -File | Select-Object -First 1
    if (-not $ffmpeg -or -not $ffprobe) {
        throw "Downloaded archive does not contain ffmpeg.exe and ffprobe.exe."
    }

    New-Item -ItemType Directory -Path $target -Force | Out-Null
    Copy-Item -LiteralPath $ffmpeg.FullName -Destination (Join-Path $target "ffmpeg.exe") -Force
    Copy-Item -LiteralPath $ffprobe.FullName -Destination (Join-Path $target "ffprobe.exe") -Force
    Set-Content -LiteralPath (Join-Path $target "SOURCE.txt") -Encoding UTF8 -Value @(
        "Source: $archiveUrl"
        "SHA256: $actual"
        "Installed: $([DateTimeOffset]::Now.ToString('o'))"
    )
    Write-Host "FFmpeg installed in $target"
}
finally {
    $resolvedTemp = [System.IO.Path]::GetFullPath($tempRoot)
    $systemTemp = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath())
    if ($resolvedTemp.StartsWith($systemTemp, [System.StringComparison]::OrdinalIgnoreCase) -and (Test-Path -LiteralPath $resolvedTemp)) {
        Remove-Item -LiteralPath $resolvedTemp -Recurse -Force
    }
}
