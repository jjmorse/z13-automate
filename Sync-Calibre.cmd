@echo off
title Sync Calibre to NAS
echo Syncing local Calibre library to the NAS (one-way, local wins)...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Sync-CalibreLibrary.ps1"
echo.
echo Done. Exit code %errorlevel%  ^(0-7 = OK, 8+ = error^).
echo Press any key to close.
pause >nul
