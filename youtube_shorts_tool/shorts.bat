@echo off
REM One-command Shorts pipeline: shorts <youtube-url> [extra flags]
REM Resolves paths relative to this script so it works from any directory.

setlocal
set "HERE=%~dp0"

if "%~1"=="" (
    echo Usage: shorts ^<youtube-url^> [--no-upload] [--max-clips N] ...
    exit /b 1
)

"%HERE%.venv\Scripts\python.exe" "%HERE%make_shorts.py" %*
exit /b %ERRORLEVEL%
