@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Marble Madness - Python Arcade

rem Marble Madness ROM-free Python port - Windows launcher.
rem No ROM files needed.  Use RUNME.bat --help for command-line help.
rem Any other arguments are passed unchanged to marble_game.py.

if /I "%~1"=="--help" goto cli_help
if /I "%~1"=="-h" goto cli_help
if /I "%~1"=="/?" goto cli_help
if /I "%~1"=="help" goto cli_help

if not exist "marble_game.py" (
    echo ERROR: marble_game.py was not found beside RUNME.bat.
    echo Extract the complete package into one folder.
    pause
    exit /b 1
)

call :find_python
if errorlevel 1 (
    echo.
    echo ERROR: Python 3.10 or newer was not found.
    echo Install Python for Windows, then run RUNME.bat again.
    pause
    exit /b 1
)

rem An argument starts the game directly, without the interactive menu.
if not "%~1"=="" goto direct_run

:menu
cls
echo.
echo   =====================================================
echo                 M A R B L E   M A D N E S S
echo                   Atari 1984 - Python port
echo   =====================================================
echo.
echo      1. Play arcade mode                [keyboard]
echo      2. Play arcade mode                [TRACKBALL]
echo      3. Watch the arcade demo / attract sequence
echo.
echo      4. Choose a race                   [keyboard]
echo      5. Choose a race                   [TRACKBALL]
echo      6. Choose a race                   [TRACKBALL, FREE time]
echo      7. Choose a race                   [keyboard, FREE time]
echo.
echo      8. Play arcade mode with sound OFF
echo      9. Play arcade mode with attract music ON
echo.
echo      H. Controls and command-line help
echo      A. Advanced command examples
echo      Q. Quit
echo.
echo   -----------------------------------------------------
echo   TIP: Trackball mode also works with a normal mouse.
echo        Press M during play to capture or release it.
echo   -----------------------------------------------------
echo.
choice /c 123456789HAQ /n /m "  Select an option: "
set "PICK=%errorlevel%"
if "%PICK%"=="12" goto quit
if "%PICK%"=="11" goto advanced
if "%PICK%"=="10" goto menu_help
if "%PICK%"=="9"  goto arcade_music
if "%PICK%"=="8"  goto arcade_silent
if "%PICK%"=="7"  goto race_keyboard_free
if "%PICK%"=="6"  goto race_trackball_free
if "%PICK%"=="5"  goto race_trackball
if "%PICK%"=="4"  goto race_keyboard
if "%PICK%"=="3"  goto demo
if "%PICK%"=="2"  goto arcade_trackball
if "%PICK%"=="1"  goto arcade

goto menu

:arcade
call :launch
goto after_game

:arcade_trackball
call :launch --trackball
goto after_game

:demo
call :launch --demo
goto after_game

:arcade_silent
call :launch --nosound
goto after_game

:arcade_music
call :launch --attract-music
goto after_game

:race_keyboard
call :pick_race
call :launch %RACE%
goto after_game

:race_trackball
call :pick_race
call :launch %RACE% --trackball
goto after_game

:race_keyboard_free
call :pick_race
call :launch %RACE% --free
goto after_game

:race_trackball_free
call :pick_race
call :launch %RACE% --trackball --free
goto after_game

:pick_race
cls
echo.
echo   SELECT A MARBLE MADNESS RACE
echo   ============================
echo.
echo      1. Practice Race
echo      2. Beginner Race
echo      3. Intermediate Race
echo      4. Aerial Race
echo      5. Silly Race
echo      6. Ultimate Race
echo.
choice /c 123456 /n /m "  Select race [1-6]: "
set "RACE=%errorlevel%"
exit /b 0

:after_game
set "GAME_RESULT=%errorlevel%"
echo.
if not "%GAME_RESULT%"=="0" echo Game exited with error code %GAME_RESULT%.
echo Press any key to return to the Marble Madness menu.
pause >nul
goto menu

:direct_run
call :launch %*
exit /b %errorlevel%

:launch
%PYTHON% -c "import pygame, numpy, PIL" >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: Required Python packages are missing.
    echo Install them with:
    echo.
    echo     %PYTHON% -m pip install pygame-ce numpy pillow
    echo.
    exit /b 1
)
echo.
echo Starting: %PYTHON% marble_game.py %*
echo.
%PYTHON% "marble_game.py" %*
exit /b %errorlevel%

:find_python
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=py -3"
    exit /b 0
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 (
    set "PYTHON=python"
    exit /b 0
)
exit /b 1

:menu_help
cls
call :show_help
echo.
pause
goto menu

:cli_help
call :show_help
exit /b 0

:show_help
echo.
echo   MARBLE MADNESS - HELP
 echo   =====================
echo.
echo   RUNNING THE GAME
echo      Double-click RUNME.bat for the menu.
echo      Or enter commands in a Windows command prompt:
echo.
echo      RUNME.bat                    Arcade attract mode / normal game
echo      RUNME.bat --trackball        Normal game, trackball or mouse input
echo      RUNME.bat --demo             Automatic attract demonstration
echo      RUNME.bat 6 --trackball --free   Ultimate, no time limit
echo      RUNME.bat --help             Show this help
echo.
echo   GAME OPTIONS (can be combined unless noted)
echo      [1-6]            Start directly at a race; skips attract mode
echo      --trackball      Capture mouse/trackball input from startup
echo      --free           No race countdown; F4 toggles in-game
echo      --demo           Automatic demonstration; no race number
 echo      --nosound        Start muted; F7 toggles sound
echo      --attract-music  Enable the attract-page music option
echo      --soundlog       Write sound commands to sound_log.txt
echo      --replay=FILE    Replay a MAME JSON trackball capture
echo      --avi=FILE       Record AVI from first frame to the given file
echo.
echo   ARCADE CONTROLS
echo      5                Insert coin
echo      1 / 2            Start 1- or 2-player game
echo      Arrow keys       Player 1 marble
echo      R / F / D / G    Player 2: up / down / left / right
echo      M                Toggle mouse/trackball capture
echo      [ / ]            Trackball sensitivity down / up
echo      Esc              Quit game
echo.
echo   EXTRA KEYS / TOOLS
echo      F1  Debug position mode    F2  Next race
echo      F3  Wall-mask overlay      F4  Toggle race clock
echo      F5  Pause + mask overlay   F6  Toggle raw AVI capture
echo      F7  Toggle sound          F12 Screenshot to screenshots/
echo      H   Height overlay         R  Restart race (solo mode)
echo.
echo   NOTE: The translated arcade engine is always on.
echo         --engine is obsolete and is not needed.
exit /b 0

:advanced
cls
echo.
echo   ADVANCED COMMAND-LINE EXAMPLES
echo   ==============================
echo.
echo   Open Command Prompt in this folder and enter:
echo.
echo     RUNME.bat 4 --trackball --free
echo       Aerial Race, trackball controls, unlimited time
echo.
echo     RUNME.bat --nosound --soundlog
echo       Normal arcade game; save sound command diagnostics
echo.
echo     RUNME.bat --attract-music
echo       Normal arcade game with optional attract-page music
echo.
echo     RUNME.bat 3 --replay=mm_capture.json --avi=replay.avi
echo       MAME trackball replay with AVI output
echo.
echo   Or use RUNME.bat --help for every supported game option.
echo   Sound recordings are optional and are not included in this package.
echo.
pause
goto menu

:quit
exit /b 0
