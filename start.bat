@echo off
title Canva PDF Cleaner
cd /d "%~dp0"
where python >nul 2>&1
if errorlevel 1 (
  echo Python not found. Install Python from https://python.org
  pause
  exit /b 1
)
python app.py
if errorlevel 1 pause
