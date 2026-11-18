@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python -m cax_pipeline.cli --serve
if errorlevel 1 pause
