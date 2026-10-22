@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
call "%~dp0_py.bat"
if not defined PYEXE (
    pause
    exit /b 1
)
%PYEXE% "%~dp0run.py" menu
echo.
pause
