@echo off
REM ====================================================================
REM  refresh_tempo_cache.bat
REM  Wrapper for Windows Task Scheduler. Moves into the repo, runs the
REM  Tempo refresh script, and appends output to a log. Task Scheduler
REM  otherwise starts in C:\Windows\System32, where the repo-relative
REM  paths and git would fail, so the cd below is essential.
REM ====================================================================

REM --- Adjust this path if your repo lives somewhere else ---
set REPO=%USERPROFILE%\Documents\Projects\indo-news-briefing

cd /d "%REPO%" || (echo Could not cd to %REPO% & exit /b 1)

REM Timestamp each run in the log
echo. >> tempo_refresh.log
echo ===== %DATE% %TIME% ===== >> tempo_refresh.log

REM Use the Python on PATH. If you use a venv, point to its python.exe here.
python tools\refresh_tempo_cache.py >> tempo_refresh.log 2>&1

echo Exit code: %ERRORLEVEL% >> tempo_refresh.log
