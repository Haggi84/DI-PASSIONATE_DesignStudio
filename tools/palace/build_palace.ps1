# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2025-2026  <Jochen Zeitler>
#
# Builds the Palace image (Dockerfile beside this script) once:
#   powershell -ExecutionPolicy Bypass -File build_palace.ps1
# Needs Docker Desktop (WSL 2 backend). Takes about half an hour to an hour;
# the log is build.log beside this script.
param([string]$Image = "pkgdesigner/palace:0.18.1", [string]$Version = "v0.18.1", [int]$Jobs = 0)
# Not "Stop": docker writes its progress to stderr, which PowerShell 5.1 would
# turn into a terminating error.
$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

function Test-Docker { docker info --format "{{.ServerVersion}}" *> $null; return $LASTEXITCODE -eq 0 }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "Docker is not installed: https://docs.docker.com/desktop/setup/install/windows-install/" -ForegroundColor Red
    exit 1
}
if (-not (Test-Docker)) {
    $desktop = Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    if (Test-Path $desktop) {
        Write-Host "Starting Docker Desktop ..."
        Start-Process $desktop
        for ($i = 0; $i -lt 60 -and -not (Test-Docker); $i++) { Start-Sleep 3 }
    }
    if (-not (Test-Docker)) { Write-Host "The Docker engine is not running." -ForegroundColor Red; exit 1 }
}

# Compiling Palace's dependencies takes about 1.3 GB per job: fit the jobs
# to the memory Docker has, not only to the cores.
if ($Jobs -le 0) {
    $cpus = [int](docker info --format "{{.NCPU}}")
    $memGB = [double](docker info --format "{{.MemTotal}}") / 1GB
    $Jobs = [math]::Max(1, [math]::Min($cpus, [math]::Floor($memGB / 1.3)))
}
Write-Host "Building $Image (Palace $Version) with $Jobs jobs; log: $PSScriptRoot\build.log"
$t = Get-Date
docker build -t $Image --build-arg PALACE_VERSION=$Version --build-arg JOBS=$Jobs --progress=plain . 2>&1 |
    Tee-Object -FilePath build.log | Select-String -Pattern "^#\d+ \[|ERROR|error:" | ForEach-Object { $_.Line }
if ($LASTEXITCODE -ne 0) { Write-Host "Build failed - see build.log" -ForegroundColor Red; exit 1 }
Write-Host ("Built {0} in {1:N0} min." -f $Image, ((Get-Date) - $t).TotalMinutes) -ForegroundColor Green
docker run --rm $Image --help | Select-Object -First 3
