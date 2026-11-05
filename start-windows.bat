@echo off
setlocal
cd /d "%~dp0"
title Oferta Central - Launcher
powershell.exe -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0launcher.ps1"
if errorlevel 1 (
  echo.
  echo O launcher fechou com erro. Consulte launcher.log.
  pause
)
