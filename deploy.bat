@echo off
echo ===================================================
echo Deploying GeoLevelQGIS Plugin to QGIS...
echo ===================================================

xcopy "C:\Users\user01\Downloads\geodetic_tool_v1.1\GeoLevelQGIS" "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\GeoLevelQGIS" /E /I /Y

echo.
echo Deployment successful! 
echo Head over to QGIS and press Ctrl+F5 to reload the plugin.
echo ===================================================
pause
