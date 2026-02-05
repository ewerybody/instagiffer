echo off
rem *** Extract version from instagiffer.py ***
FOR /F "tokens=2 delims==' " %%i IN ('findstr /r "^INSTAGIFFER_VERSION" igf_common.py') DO set INSTAGIFFER_VERSION=%%i
FOR /F "tokens=2 delims==' " %%i IN ('findstr /r "^INSTAGIFFER_PRERELEASE" igf_common.py') DO set INSTAGIFFER_PRERELEASE=%%i
echo *** Building Instagiffer v%INSTAGIFFER_VERSION%%INSTAGIFFER_PRERELEASE%***

if exist build (
    echo Getting rid of all the old files in build folder ...
    rd /S /Q build
)

rem ***** create the exe
echo Running build script ...
".venv/Scripts/python.exe" setup-win-cx_freeze.py build

IF NOT ERRORLEVEL 1 GOTO no_error
pause "Freeze failed. See error log"
exit
:no_error
pause "no_error"
