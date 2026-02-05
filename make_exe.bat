echo off
rem *** Extract version from instagiffer.py ***
FOR /F "tokens=2 delims==' " %%i IN ('findstr /r "^INSTAGIFFER_VERSION" igf_common.py') DO set INSTAGIFFER_VERSION=%%i
FOR /F "tokens=2 delims==' " %%i IN ('findstr /r "^INSTAGIFFER_PRERELEASE" igf_common.py') DO set INSTAGIFFER_PRERELEASE=%%i
set APP_NAME=Instagiffer
set VERSION_NAME=%APP_NAME%-%INSTAGIFFER_VERSION%%INSTAGIFFER_PRERELEASE%
set PYTHON=.venv/Scripts/python.exe
set INNO=C:\Program Files (x86)\Inno Setup 6\ISCC.exe
set DEFAULT_LOC=C:\Program Files (x86)\%APP_NAME%
set UNINSTALLER=%DEFAULT_LOC%\unins000.exe
set SEVEN_ZIP=C:\Program Files\7-Zip\7z.exe
set BUILD_DIR=.\build\exe.win-amd64-3.14
set DEMOS_LOC=%BUILD_DIR%\tk\demos

echo *** Building %APP_NAME% v%INSTAGIFFER_VERSION%%INSTAGIFFER_PRERELEASE% ***

if exist "build" (
    echo Getting rid of all the old files in build folder ...
    rd /S /Q "build"
)

rem ***** create the exe
echo Running build script ...
"%PYTHON%" setup-win-cx_freeze.py build

IF NOT ERRORLEVEL 1 GOTO no_error
pause "Freeze failed. See error log"
exit
:no_error

if exist "%DEMOS_LOC%" (
    echo Removing unwanted files from distribution ...
    rmdir /S /Q "%DEMOS_LOC%"
)
del /S "%BUILD_DIR%\_ *"

if not exist "%INNO%" (
    echo NO Inno Setup found at "%INNO%"!
    GOTO end
)

echo Creating Installer ...
del instagiffer*setup.exe
set INNOMyAppVersion=%INSTAGIFFER_VERSION%%INSTAGIFFER_PRERELEASE%
set INNOBuildDir=%BUILD_DIR%
echo INNOMyAppVersion: %INNOMyAppVersion%
echo INNOBuildDir: %INNOBuildDir%
"%INNO%" installer.iss /V0


echo Testing Install. Uninstall first ...
echo   Uninstaller path: %UNINSTALLER%
if exist "%UNINSTALLER%" (
    "%UNINSTALLER%" /VERYSILENT /SUPPRESSMSGBOXES
)

if exist %VERSION_NAME%-setup.exe (
    echo Running Installer installation ...
    %VERSION_NAME%-setup.exe /SP- /SILENT /SUPPRESSMSGBOXES
    pause "press any key once installation completes"
) else (
    echo NO Installer found at "%VERSION_NAME%-setup.exe"!
    GOTO end
)

if exist "%DEFAULT_LOC%\instagiffer.exe" (
    echo Quickly sanity-test the installation - just verify basic app functionality ...
    "%DEFAULT_LOC%\instagiffer.exe"
) else (
    echo NO %APP_NAME% found at "%DEFAULT_LOC%\instagiffer.exe"!
    GOTO end
)

if exist "%SEVEN_ZIP%" (
    echo Generating portable release ...
    xcopy /Y /I /S /Q "%DEFAULT_LOC%" %VERSION_NAME%
    del .\%VERSION_NAME%\unins*
    copy /Y instagiffer-event.log .\%VERSION_NAME%
    "%SEVEN_ZIP%" a -tzip %VERSION_NAME%-portable.zip %VERSION_NAME%
    rmdir /S /Q %VERSION_NAME%
) else (
    echo No 7-Zip found at "%SEVEN_ZIP%"
)

:end