@echo off
rem Copies the plugin folder next to this script into the default QGIS profile.
echo ===================================================
echo Deploying Geo Level Gravi (GeoLevelQGIS) to QGIS...
echo ===================================================

set "SRC=%~dp0GeoLevelQGIS"
set "DST=%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\GeoLevelQGIS"

if not exist "%SRC%\metadata.txt" (
    echo ERROR: plugin folder not found at "%SRC%"
    pause
    exit /b 1
)

rem /MIR keeps the installed copy identical (removes files deleted from the repo)
robocopy "%SRC%" "%DST%" /MIR /XD __pycache__ tests /XF *.pyc /NFL /NDL /NJH /NP
if %ERRORLEVEL% GEQ 8 (
    echo ERROR: copy failed ^(robocopy code %ERRORLEVEL%^)
    pause
    exit /b 1
)

echo.
echo Deployment finished. Restart QGIS (or use Plugin Reloader) to load the new version.
echo ===================================================
pause
