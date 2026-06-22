@echo off
REM ====================================================================
REM  install_tempo_tasks.cmd
REM  Registers TWO Windows scheduled tasks that refresh the Tempo cache,
REM  imported from XML so they include "Start the task as soon as possible
REM  after a scheduled start is missed" (StartWhenAvailable). That means if
REM  the machine was asleep at 2pm or 10pm, the task runs at the next wake.
REM
REM    - "IndoBriefing Tempo Refresh PM"    daily 14:00
REM    - "IndoBriefing Tempo Refresh Night" daily 22:00
REM
REM  Run ONCE from the repo root:
REM      tools\install_tempo_tasks.cmd
REM  Per-user tasks, no admin rights needed.
REM ====================================================================

set TOOLS=%USERPROFILE%\Documents\Projects\indo-news-briefing\tools

if not exist "%TOOLS%\tempo_task_pm.xml" (
    echo ERROR: cannot find %TOOLS%\tempo_task_pm.xml
    echo Run this from the repo, or fix the TOOLS path at the top of this file.
    exit /b 1
)

echo Registering afternoon task (2pm, runs at next wake if missed) ...
schtasks /Create /F /TN "IndoBriefing Tempo Refresh PM" /XML "%TOOLS%\tempo_task_pm.xml"

echo Registering night task (10pm, runs at next wake if missed) ...
schtasks /Create /F /TN "IndoBriefing Tempo Refresh Night" /XML "%TOOLS%\tempo_task_night.xml"

echo.
echo Done. Both tasks registered with catch-up enabled.
echo.
echo Test one immediately (does not wait for its scheduled time):
echo     schtasks /Run /TN "IndoBriefing Tempo Refresh Night"
echo.
echo Output of each run is appended to:
echo     %USERPROFILE%\Documents\Projects\indo-news-briefing\tempo_refresh.log
echo.
echo To remove the tasks later:
echo     schtasks /Delete /F /TN "IndoBriefing Tempo Refresh PM"
echo     schtasks /Delete /F /TN "IndoBriefing Tempo Refresh Night"
