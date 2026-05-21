@echo off
REM fg_launch_windows.bat
REM ─────────────────────────────────────────────────────────────────────────────
REM Launch FlightGear on Windows to talk to the avionics app
REM
REM ─────────────────────────────────────────────────────────────────────────────

SET TARGET_IP=
IF "%TARGET_IP%"=="" SET TARGET_IP=127.0.0.1

REM ── Ports ────────────────────────────────────────────────────────────────────
SET FDM_PORT=5500
SET CTRLS_PORT=5501
SET RATE=25

REM ── FlightGear install path — adjust if yours differs ────────────────────────
SET FG_BIN=C:\Program Files\FlightGear 2024.1\bin\fgfs
SET FG_ROOT=C:\Users\Rohith\FlightGear\Downloads\fgdata_2024_1

echo ======================================================
echo   FlightGear ^-^> Avionics Interface
echo   QNX IP     : %TARGET_IP%
echo   FDM out    : %TARGET_IP%:%FDM_PORT%  
echo   Ctrls in   : :%CTRLS_PORT%        
echo   Rate       : %RATE% Hz
echo ======================================================
echo.

"%FG_BIN%" ^
  --fg-root="%FG_ROOT%" ^
  --aircraft=c172p ^
  --airport=KSFO ^
  --runway=28L ^
  --in-air ^
  --altitude=3000 ^
  --vc=100 ^
  --heading=270 ^
  --timeofday=noon ^
  --prop:/sim/rendering/als-lighting=false ^
  --native-fdm=socket,out,%RATE%,%TARGET_IP%,%FDM_PORT%,udp ^
  --native-ctrls=socket,in,%RATE%,,%CTRLS_PORT%,udp ^
  --httpd=8080 ^
  --telnet=5401 ^
  --disable-sound ^
  --geometry=1280x720

REM ─────────────────────────────────────────────────────────────────────────────
REM Troubleshooting
REM
REM Problem: fgfs.exe not found
REM Fix: Update FG_BIN path above to match your FlightGear installation.
REM      Common locations:
REM        C:\Program Files\FlightGear 2020.3\bin\fgfs.exe
REM        C:\FlightGear\bin\fgfs.exe
REM ─────────────────────────────────────────────────────────────────────────────