@echo off
rem ---------------------------------------------------------------
rem  DiskScope - regenerate report from existing snapshots
rem ---------------------------------------------------------------
cd /d "%~dp0"
call "%~dp0_py.bat"
if not defined PYEXE (
    pause
    exit /b 1
)
echo.
echo Generating report from the latest two snapshots...
echo.
%PYEXE% "%~dp0run.py" report --open
echo.
pause
