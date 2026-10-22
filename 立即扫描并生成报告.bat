@echo off
rem ---------------------------------------------------------------
rem  DiskScope - scan C: and generate report
rem  (Chinese/English text is printed by Python; keep this file ASCII)
rem ---------------------------------------------------------------
cd /d "%~dp0"
call "%~dp0_py.bat"
if not defined PYEXE (
    pause
    exit /b 1
)
echo.
echo [1/2] Scanning... first full scan of C: usually takes 1-5 minutes.
echo       If not running as Administrator, a UAC prompt will appear
echo       and the scan continues in a new elevated window.
echo.
%PYEXE% "%~dp0run.py" scan --report --open
echo.
pause
