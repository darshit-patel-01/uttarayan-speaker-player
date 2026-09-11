@echo off
REM Single entry point: sets up Python on first run, then starts the app.
REM No Docker needed.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
