@echo off
REM Single entry point: starts Docker Desktop if needed, then runs the app.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
