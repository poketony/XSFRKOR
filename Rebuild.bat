@echo off
setlocal
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "MODE=rebuild"
set "FONT="
if /i "%~1"=="--verify" set "MODE=verify"
if not "%~1"=="" if /i not "%~1"=="--verify" set "FONT=%~f1"
if defined FONT (
    python -B "%~dp0tools\rebuild_assets.py" %MODE% --font "%FONT%"
) else (
    python -B "%~dp0tools\rebuild_assets.py" %MODE%
)
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" echo [FAILED] Do not use incomplete output. Check the error above.
if /i not "%~2"=="--no-pause" pause
exit /b %RESULT%
