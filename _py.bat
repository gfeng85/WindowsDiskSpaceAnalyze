@echo off
rem 定位可用的 Python 解释器，结果写入 PYEXE（供其他批处理 call 使用）
set "PYEXE="
where python >nul 2>nul
if not errorlevel 1 set "PYEXE=python"
if not defined PYEXE (
    where py >nul 2>nul
    if not errorlevel 1 set "PYEXE=py -3"
)
if not defined PYEXE (
    if exist "C:\Users\gaofeng\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PYEXE=C:\Users\gaofeng\.workbuddy\binaries\python\versions\3.13.12\python.exe"
)
if not defined PYEXE (
    echo [ERROR] Python not found. Please install Python 3.9+ and add it to PATH.
    echo         https://www.python.org/downloads/
)
