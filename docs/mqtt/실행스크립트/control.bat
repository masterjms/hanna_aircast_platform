@echo off
setlocal
title xWIFI MQTT Broadcast Control

REM ===== Config (edit as needed) =====
set "MOSQ=C:\Program Files\mosquitto"
set "BROKER=localhost"
set "VILLAGE=00000001"
set "TARGET=iotradio/village/%VILLAGE%/cmd"
set "TARGET_DESC=Village %VILLAGE%"
set "SDIR=%~dp0"
set "SFILE=%SDIR%session.txt"
if not exist "%SFILE%" (>"%SFILE%" echo 0)
set "SERVER_IP=192.168.0.5"
set "FILE_PORT=9002"
set "FILESDIR=%SDIR%files\"
set "FCOUNTER=%SDIR%file_counter.txt"
if not exist "%FCOUNTER%" (>"%FCOUNTER%" echo 0)
set "CFGCOUNTER=%SDIR%config_counter.txt"
if not exist "%CFGCOUNTER%" (>"%CFGCOUNTER%" echo 0)
set "CONFIG_TOPIC=iotradio/all/config"
REM TEST ONLY: fixed village_id sent via CONFIG to verify the assignment
REM mechanism works end-to-end. Real per-device assignment (different id per
REM device, via a per-device topic) is a later step, not this test.
set "CFG_VILLAGE_ID_TEST=00000001"
set "OTACOUNTER=%SDIR%ota_counter.txt"
if not exist "%OTACOUNTER%" (>"%OTACOUNTER%" echo 0)
set "PKGDIR=%SDIR%files\update\"
if not exist "%PKGDIR%" mkdir "%PKGDIR%"
set "PKGNAME=IOT_RADIO.pkg"
REM ====================================

:menu
cls
echo ==================================
echo    xWIFI MQTT Broadcast Control
echo ==================================
echo  Broker : %BROKER%
echo  Target : %TARGET_DESC%
echo ----------------------------------
echo   1) Start broadcast (send LIVE_START)
echo   2) Stop broadcast  (send LIVE_STOP)
echo   3) Status check (listen 5s)
echo   4) Open result monitor window
echo   5) Send file broadcast (FILE_START)
echo   6) Cancel file broadcast (FILE_STOP)
echo   7) Send CONFIG (status/live_stats interval)
echo   8) Send OTA update (OTA_START)
echo   0) Exit
echo ==================================
choice /c 1234567890 /n /m "Select: "
set "SEL=%errorlevel%"
if "%SEL%"=="1" goto start
if "%SEL%"=="2" goto stop
if "%SEL%"=="3" goto status
if "%SEL%"=="4" goto monitor
if "%SEL%"=="5" goto filestart
if "%SEL%"=="6" goto filestop
if "%SEL%"=="7" goto configsend
if "%SEL%"=="8" goto otastart
if "%SEL%"=="10" goto end
goto menu

:start
set /p SESSION=<"%SFILE%"
set /a SESSION=SESSION+1
>"%SFILE%" echo %SESSION%
set "TMPJSON=%TEMP%\live_start.json"
<nul set /p ".={"type":"LIVE_START","job_id":%SESSION%,"codec":"opus","frame_ms":40,"sample_rate":16000,"record_flash":1,"file_name":"live-demo.lopus","ready_timeout_sec":30}" >"%TMPJSON%"
"%MOSQ%\mosquitto_pub.exe" -h %BROKER% -t "%TARGET%" -q 1 -f "%TMPJSON%"
echo.
echo [sent] LIVE_START (job_id=%SESSION%) -^> %TARGET%
echo NOTE: audio source (Icecast + mic) must already be running separately.
pause
goto menu

:stop
set /p SESSION=<"%SFILE%"
set "TMPJSON=%TEMP%\live_stop.json"
<nul set /p ".={"type":"LIVE_STOP","job_id":%SESSION%}" >"%TMPJSON%"
"%MOSQ%\mosquitto_pub.exe" -h %BROKER% -t "%TARGET%" -q 1 -f "%TMPJSON%"
echo.
echo [sent] LIVE_STOP (job_id=%SESSION%) -^> %TARGET%
pause
goto menu

:status
echo.
echo Streaming STATUS/result. Press any key to stop and return to menu.
echo.
set "PIDBEFORE=%TEMP%\mqtt_pids_before.txt"
set "PIDAFTER=%TEMP%\mqtt_pids_after.txt"
(for /f "tokens=2" %%P in ('tasklist /fi "imagename eq mosquitto_sub.exe" /nh 2^>nul') do echo %%P) >"%PIDBEFORE%"
start "" /b "%MOSQ%\mosquitto_sub.exe" -h %BROKER% -t "iotradio/device/+/status" -t "iotradio/device/+/result" -v
ping -n 2 127.0.0.1 >nul
(for /f "tokens=2" %%P in ('tasklist /fi "imagename eq mosquitto_sub.exe" /nh 2^>nul') do echo %%P) >"%PIDAFTER%"
set "SUBPID="
for /f %%P in ('findstr /v /x /g:"%PIDBEFORE%" "%PIDAFTER%" 2^>nul') do set "SUBPID=%%P"
pause >nul
if defined SUBPID taskkill /PID %SUBPID% /F >nul 2>&1
goto menu

:monitor
start "MQTT MONITOR" "%SDIR%monitor.bat"
goto menu

:filestart
echo.
echo Files available in "%FILESDIR%":
dir /b "%FILESDIR%" 2>nul
echo.
set "FNAME_TRY=0"
:ask_filename
set /a FNAME_TRY=FNAME_TRY+1
set "FNAME="
set /p FNAME=Enter file name to send (or type CANCEL):
if /i "%FNAME%"=="CANCEL" goto menu
if not defined FNAME (
    if %FNAME_TRY% GEQ 10 (
        echo.
        echo   -^> [FAILED] No file name entered.
        pause
        goto menu
    )
    REM a leftover blank line from the menu selection may have been
    REM consumed here instead of the real input - ask again
    goto ask_filename
)
set "FPATH=%FILESDIR%%FNAME%"
if exist "%FPATH%\" (
    echo.
    echo   -^> [FAILED] That is a folder, not a file: %FPATH%
    pause
    goto menu
)
if not exist "%FPATH%" (
    echo.
    echo   -^> [FAILED] File not found: %FPATH%
    pause
    goto menu
)

for %%A in ("%FPATH%") do set "FSIZE=%%~zA"
if "%FSIZE%"=="0" (
    echo.
    echo   -^> [FAILED] File size is 0, aborting: %FPATH%
    pause
    goto menu
)
echo Computing SHA256 ...
for /f "delims=" %%H in ('powershell -NoProfile -Command "(Get-FileHash -LiteralPath '%FPATH%' -Algorithm SHA256).Hash.ToLower()"') do set "FHASH=%%H"
if not defined FHASH (
    echo.
    echo   -^> [FAILED] Could not compute SHA256.
    pause
    goto menu
)

set /p FCOUNT=<"%FCOUNTER%"
set /a FCOUNT=FCOUNT+1
>"%FCOUNTER%" echo %FCOUNT%

set "TMPJSON=%TEMP%\file_start.json"
<nul set /p ".={"type":"FILE_START","job_id":%FCOUNT%,"size":%FSIZE%,"resume_offset":0,"sha256":"%FHASH%","https_url":"http://%SERVER_IP%:%FILE_PORT%/%FNAME%","file_name":"%FNAME%","store_flash":1,"autoplay":1}" >"%TMPJSON%"
"%MOSQ%\mosquitto_pub.exe" -h %BROKER% -t "%TARGET%" -q 1 -f "%TMPJSON%"
echo.
echo [sent] FILE_START (job_id=%FCOUNT%) file=%FNAME% size=%FSIZE% -^> %TARGET%
echo   url  : http://%SERVER_IP%:%FILE_PORT%/%FNAME%
echo   sha256: %FHASH%
echo NOTE: file_server_start.bat must already be running.
pause
goto menu

:filestop
set "TMPJSON=%TEMP%\file_stop.json"
<nul set /p ".={"type":"FILE_STOP"}" >"%TMPJSON%"
"%MOSQ%\mosquitto_pub.exe" -h %BROKER% -t "%TARGET%" -q 1 -f "%TMPJSON%"
echo.
echo [sent] FILE_STOP -^> %TARGET%
pause
goto menu

:configsend
set /p CVER=<"%CFGCOUNTER%"
set /a CVER=CVER+1
>"%CFGCOUNTER%" echo %CVER%

echo.
echo Send CONFIG (retained, applies to all devices via %CONFIG_TOPIC%)
echo Press Enter to keep the default shown in [brackets].
set "CSTATUS="
set /p CSTATUS=status_interval_sec [30]:
if not defined CSTATUS set "CSTATUS=30"
set "CLIVESTATS="
set /p CLIVESTATS=live_stats_interval_sec [10]:
if not defined CLIVESTATS set "CLIVESTATS=10"
set "CQOS="
set /p CQOS=event_qos, periodic STATUS/LIVE_STATS only, 0 or 1 [0]:
if not defined CQOS set "CQOS=0"

set "TMPJSON=%TEMP%\config.json"
<nul set /p ".={"config_version":%CVER%,"status_interval_sec":%CSTATUS%,"live_stats_interval_sec":%CLIVESTATS%,"event_qos":%CQOS%,"village_id":"%CFG_VILLAGE_ID_TEST%"}" >"%TMPJSON%"
"%MOSQ%\mosquitto_pub.exe" -h %BROKER% -t "%CONFIG_TOPIC%" -q 1 -r -f "%TMPJSON%"
echo.
echo [sent] CONFIG (config_version=%CVER%) -^> %CONFIG_TOPIC% (retained)
echo   status_interval_sec=%CSTATUS%  live_stats_interval_sec=%CLIVESTATS%  event_qos=%CQOS%  village_id=%CFG_VILLAGE_ID_TEST% (TEST fixed value)
pause
goto menu

:otastart
echo.
set "PKGPATH=%PKGDIR%%PKGNAME%"
if not exist "%PKGPATH%" (
    echo   -^> [FAILED] Package not found: %PKGPATH%
    echo   Place %PKGNAME% in files\update\ first.
    pause
    goto menu
)
for %%A in ("%PKGPATH%") do set "PKGSIZE=%%~zA"
if "%PKGSIZE%"=="0" (
    echo   -^> [FAILED] Package size is 0: %PKGPATH%
    pause
    goto menu
)
echo Computing SHA256 ...
for /f "delims=" %%H in ('powershell -NoProfile -Command "(Get-FileHash -LiteralPath '%PKGPATH%' -Algorithm SHA256).Hash.ToLower()"') do set "PKGHASH=%%H"
if not defined PKGHASH (
    echo   -^> [FAILED] Could not compute SHA256.
    pause
    goto menu
)

set "PVER="
set /p PVER=pkg_version [1]:
if not defined PVER set "PVER=1"

set /p OJOB=<"%OTACOUNTER%"
set /a OJOB=OJOB+1
>"%OTACOUNTER%" echo %OJOB%

set "TMPJSON=%TEMP%\ota_start.json"
<nul set /p ".={"type":"OTA_START","job_id":%OJOB%,"pkg_version":%PVER%,"url":"http://%SERVER_IP%:%FILE_PORT%/update/%PKGNAME%","size":%PKGSIZE%,"sha256":"%PKGHASH%"}" >"%TMPJSON%"
"%MOSQ%\mosquitto_pub.exe" -h %BROKER% -t "%TARGET%" -q 1 -f "%TMPJSON%"
echo.
echo [sent] OTA_START (job_id=%OJOB%) size=%PKGSIZE% -^> %TARGET%
echo   url   : http://%SERVER_IP%:%FILE_PORT%/update/%PKGNAME%
echo   sha256: %PKGHASH%
echo NOTE: file_server_start.bat must already be running.
echo NOTE: watch option 3 for OTA_STATUS (ACCEPTED/PREPARE/DOWNLOADING/VERIFYING). No approval step needed -
echo       the device auto-applies and reboots right after VERIFYING/COMPLETED. Wait for it to reconnect,
echo       then check STATUS firmware version to confirm success.
pause
goto menu

:end
endlocal
exit /b
