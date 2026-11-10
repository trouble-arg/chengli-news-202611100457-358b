@echo off
cd /d "%~dp0"
start "" http://127.0.0.1:8765
where python >nul 2>nul
if %errorlevel%==0 (python aggregator.py) else (py -3 aggregator.py)
