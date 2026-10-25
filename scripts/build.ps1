param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $repoRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name LG700Tool --distpath dist --workpath build --specpath build src/lg700_tool.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }
    Write-Host 'Built dist\LG700Tool.exe. Put an external ffmpeg.exe beside it for video conversion.'
} finally {
    Pop-Location
}
