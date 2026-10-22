@echo off
rem ---------------------------------------------------------------
rem  DiskScope - install scheduled scan task (every 6 hours, SYSTEM)
rem ---------------------------------------------------------------
cd /d "%~dp0"
call "%~dp0_py.bat"
if not defined PYEXE (
    pause
    exit /b 1
)
echo.
echo Installing scheduled task: scan every 6 hours as SYSTEM...
echo.
%PYEXE% "%~dp0run.py" install-task --every-hours 6
echo.
pause
