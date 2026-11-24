@echo off
REM What the scheduled task runs: 'update', tried again up to twice, half an hour
REM apart, when it exits 75 (no usable link) or 70 (published, but the gap page
REM fell back to cached OSM). Task Scheduler's RestartCount never did this: it
REM counts a launched process as a success whatever it exits with. Any other exit
REM code stops here. ping does the waiting because timeout refuses a redirected
REM stdin.
setlocal
cd /d "%~dp0" || exit /b 1
if not defined RETRY_WAIT_S set "RETRY_WAIT_S=1801"
set "TRIES=0"
:run
python run.py update
set "RC=%ERRORLEVEL%"
if not "%RC%"=="70" if not "%RC%"=="75" exit /b %RC%
if %TRIES% GEQ 2 exit /b %RC%
set /a TRIES+=1 >nul
echo [%DATE% %TIME%] exit %RC%, retry %TRIES% of 2 in half an hour
ping -n %RETRY_WAIT_S% 127.0.0.1 >nul
goto run
