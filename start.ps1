# Single entry point. Creates the Python virtualenv and installs dependencies
# on first run, checks the audio tools are present, then starts the app.
# No Docker or other services are needed — everything runs in-process on
# top of the SQLite database.
#
# Usage:  .\start.ps1   (or double-click start.bat)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvDir = Join-Path $here "venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$requirements = Join-Path $here "requirements.txt"
$stamp = Join-Path $venvDir ".requirements.installed"

function Find-Python {
    foreach ($candidate in @("py", "python3", "python")) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        # The Windows Store stub answers to "python" but only opens the Store.
        if ($cmd.Source -like "*WindowsApps*") { continue }
        $version = & $cmd.Source -c "import sys; print(sys.version_info >= (3, 10))" 2>$null
        if ($version -eq "True") { return $cmd.Source }
    }
    return $null
}

function Test-Tool([string]$name, [string]$fallback) {
    if (Get-Command $name -ErrorAction SilentlyContinue) { return $true }
    if ($fallback -and (Test-Path $fallback)) { return $true }
    return $false
}

# --- Python -----------------------------------------------------------------
if (-not (Test-Path $venvPython)) {
    $python = Find-Python
    if (-not $python) {
        Write-Host ""
        Write-Host "Python 3.10 or newer is required but was not found." -ForegroundColor Red
        Write-Host "Install it from https://www.python.org/downloads/windows/"
        Write-Host "(tick 'Add python.exe to PATH' in the installer), then run this again."
        Write-Host ""
        Read-Host "Press Enter to close"
        exit 1
    }
    Write-Host "First run: creating the Python environment (this takes a minute)..."
    & $python -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Could not create the virtualenv." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }
}

# Reinstall dependencies whenever requirements.txt is newer than the last install.
$needInstall = -not (Test-Path $stamp)
if (-not $needInstall) {
    $needInstall = (Get-Item $requirements).LastWriteTime -gt (Get-Item $stamp).LastWriteTime
}
if ($needInstall) {
    Write-Host "Installing Python dependencies..."
    & $venvPython -m pip install --quiet --upgrade pip
    & $venvPython -m pip install --quiet -r $requirements
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Dependency install failed. Check your internet connection and try again." -ForegroundColor Red
        Read-Host "Press Enter to close"
        exit 1
    }
    New-Item -ItemType File -Path $stamp -Force | Out-Null
}

# --- Audio tools (warn, don't block: the app reports these clearly at runtime too) ---
$missing = @()
if (-not (Test-Tool "mpv" "C:\Program Files\MPV Player\mpv.exe")) { $missing += "mpv     (winget install mpv)" }
if (-not (Test-Tool "ffmpeg" "")) { $missing += "ffmpeg  (winget install ffmpeg)" }
if ($missing.Count -gt 0) {
    Write-Host ""
    Write-Host "Warning: these audio tools were not found on PATH. Playback will not work until they are installed:" -ForegroundColor Yellow
    foreach ($m in $missing) { Write-Host "  - $m" -ForegroundColor Yellow }
    Write-Host ""
}

# --- Go ---------------------------------------------------------------------
& $venvPython (Join-Path $here "run.py") @args
