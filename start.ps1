# Single entry point: starts Docker Desktop if needed, waits for the
# daemon to be ready, then runs the app using the project's venv.
# Usage:  .\start.ps1        (add --stop-kafka to also stop Kafka on exit)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$dockerDesktop = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
$venvPython = Join-Path $here "venv\Scripts\python.exe"

function Test-DockerReady {
    docker info *> $null
    return $LASTEXITCODE -eq 0
}

if (-not (Test-DockerReady)) {
    Write-Host "Docker daemon not running, starting Docker Desktop..."
    if (-not (Test-Path $dockerDesktop)) {
        Write-Error "Docker Desktop not found at '$dockerDesktop'. Start it manually, then re-run this script."
        exit 1
    }
    Start-Process -FilePath $dockerDesktop

    Write-Host "Waiting for Docker daemon to be ready..."
    $deadline = (Get-Date).AddMinutes(3)
    while (-not (Test-DockerReady)) {
        if ((Get-Date) -gt $deadline) {
            Write-Error "Timed out waiting for Docker Desktop to start."
            exit 1
        }
        Start-Sleep -Seconds 2
    }
}
Write-Host "Docker is ready."

if (-not (Test-Path $venvPython)) {
    Write-Error "venv not found at '$venvPython'. Create it first: python -m venv venv; venv\Scripts\pip install -r requirements.txt"
    exit 1
}

& $venvPython (Join-Path $here "run.py") @args
