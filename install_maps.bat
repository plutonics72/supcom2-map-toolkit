@echo off
setlocal enabledelayedexpansion
rem ============================================================
rem  INSTALL MAPS.bat - copies every custom SC2 map (_*.scd) from
rem  this folder into your Supreme Commander 2 gamedata folder.
rem  Close the game first; restart it afterwards to see the maps.
rem ============================================================

set "SRC=%~dp0"

rem --- find the gamedata folder ---------------------------------
set "GD="
if defined SC2_GAMEDATA if exist "%SC2_GAMEDATA%\" set "GD=%SC2_GAMEDATA%"
if not defined GD if exist "C:\Program Files (x86)\Steam\steamapps\common\Supreme Commander 2\gamedata\" set "GD=C:\Program Files (x86)\Steam\steamapps\common\Supreme Commander 2\gamedata"
if not defined GD if exist "C:\Program Files\Steam\steamapps\common\Supreme Commander 2\gamedata\" set "GD=C:\Program Files\Steam\steamapps\common\Supreme Commander 2\gamedata"
if not defined GD for %%D in (C D E F G H) do (
    if not defined GD if exist "%%D:\SteamLibrary\steamapps\common\Supreme Commander 2\gamedata\" set "GD=%%D:\SteamLibrary\steamapps\common\Supreme Commander 2\gamedata"
    if not defined GD if exist "%%D:\Steam\steamapps\common\Supreme Commander 2\gamedata\" set "GD=%%D:\Steam\steamapps\common\Supreme Commander 2\gamedata"
    if not defined GD if exist "%%D:\Games\Steam\steamapps\common\Supreme Commander 2\gamedata\" set "GD=%%D:\Games\Steam\steamapps\common\Supreme Commander 2\gamedata"
)
if not defined GD (
    echo.
    echo   Could not find the Supreme Commander 2 gamedata folder.
    echo   Find it yourself ^(search your Steam library for
    echo   "Supreme Commander 2\gamedata"^), then run:
    echo.
    echo     set "SC2_GAMEDATA=X:\...\Supreme Commander 2\gamedata"
    echo     "%~f0"
    echo.
    pause
    exit /b 1
)

echo.
echo   Maps folder : %SRC%
echo   Game folder : %GD%
echo.

set /a OK=0
set /a BAD=0
for %%F in ("%SRC%_*.scd") do (
    copy /Y "%%F" "%GD%\" >nul 2>&1
    if errorlevel 1 (
        echo   FAILED  %%~nxF
        set /a BAD+=1
    ) else (
        echo   copied  %%~nxF
        set /a OK+=1
    )
)

echo.
if !BAD! gtr 0 (
    echo   !OK! copied, !BAD! FAILED.
    echo   If copies failed, right-click this file and "Run as administrator"
    echo   ^(the game folder may be write-protected on this PC^).
) else (
    echo   !OK! map^(s^) installed.
)
echo.
echo   Restart Supreme Commander 2 - maps load at game launch.
echo.
pause
