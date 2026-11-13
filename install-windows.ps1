$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
    throw 'Python 3.10+ is required (install Python for Windows and the py launcher).'
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw 'Node.js 22+, npm and the MSVC build tools are required to build the Electron desktop app.'
}
$gitBash = Join-Path $env:ProgramFiles 'Git\bin\bash.exe'
if (-not (Test-Path $gitBash) -and -not (Get-Command bash.exe -ErrorAction SilentlyContinue)) {
    throw 'Install Git for Windows, including Git Bash, before continuing.'
}

Push-Location desktop
try {
    npm ci
    if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed.' }
    py -3 scripts/build_backend.py
    if ($LASTEXITCODE -ne 0) { throw 'Python backend build failed.' }
    npm run pack
    if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
} finally { Pop-Location }
Write-Host 'Built desktop\release\win-unpacked\feiyu-desktop.exe'
Write-Host 'Keep the entire win-unpacked directory together; Python is bundled inside.'
