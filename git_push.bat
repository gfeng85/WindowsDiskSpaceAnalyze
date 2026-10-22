@echo off
rem Push DiskScope to GitHub (https://github.com/gfeng85/WindowsDiskSpaceAnalyze)
rem NOTE: data/ reports/ .workbuddy/ are excluded by .gitignore - do NOT remove that.
setlocal
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo [!] git not found in PATH. Install Git for Windows first.
    pause & exit /b 1
)

if not exist .git (
    echo [1/4] git init
    git init || (pause & exit /b 1)
    git branch -M main
)

echo [2/4] git add -A
git add -A

echo [3/4] sanity check: local data must NOT be staged
git status --porcelain | findstr /R "^A  *data/ ^A  *reports/ \.workbuddy/" >nul
if not errorlevel 1 (
    echo [!] ERROR: data/ or reports/ or .workbuddy/ staged! Check .gitignore, ABORT.
    git reset
    pause & exit /b 1
)
git status --short

echo [4/4] commit and push
git commit -m "DiskScope v1.0: Windows disk space monitoring and growth attribution tool" || (
    echo (nothing to commit or commit failed - see output above^)
)
git remote get-url origin >nul 2>nul
if errorlevel 1 git remote add origin https://github.com/gfeng85/WindowsDiskSpaceAnalyze.git
git push -u origin main
echo.
echo Done. If push failed with auth error, make sure the repo exists on GitHub
echo and your credential manager is logged in.
pause
