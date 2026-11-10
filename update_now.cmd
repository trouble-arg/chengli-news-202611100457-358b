@echo off
cd /d "%~dp0"
where python >nul 2>nul
if %errorlevel%==0 (python aggregator.py --refresh-only) else (py -3 aggregator.py --refresh-only)
