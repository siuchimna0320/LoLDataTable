@echo off
rem ===================================================================
rem  LOL Dashboard - one-click startup for Windows
rem  - Double-click to start the waitress server and open the browser
rem  - Keep this window open while using the dashboard
rem  - Close this window to stop the server
rem  - If port 8050 is already in use, the dashboard just opens
rem ===================================================================
setlocal
cd /d "%~dp0"
set "URL=http://127.0.0.1:8050/"
set "PORT=8050"

rem --- Single-instance check: open browser if server is already up ---
netstat -ano -p tcp | findstr "LISTENING" | findstr ":%PORT%" >nul 2>&1
if not errorlevel 1 (
    echo Dashboard is already running. Opening browser...
    start "" "%URL%"
    timeout /t 3 >nul
    exit /b 0
)

echo Starting LOL dashboard server...
echo URL: %URL%
echo The browser will open automatically once the server is ready.
echo Keep this window open while using the dashboard.
echo.

rem --- Poll the port in the background, open browser when ready ---
start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 40;$i++){try{$c=New-Object Net.Sockets.TcpClient;$c.Connect('127.0.0.1',%PORT%);$c.Close();Start-Process '%URL%';break}catch{Start-Sleep -Milliseconds 500}}"

rem --- Start the server (closing this window stops it) ---
python -m waitress --threads 8 --listen=127.0.0.1:%PORT% frontend.app:server
if errorlevel 1 (
    echo.
    echo [ERROR] Server failed to start. Check that Python and the
    echo required packages are installed in this environment.
    pause
)
