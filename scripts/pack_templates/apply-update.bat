@echo off
chcp 65001 >nul
cd /d "%~dp0"

if "%~1"=="" (
  echo.
  echo 用法：把 CanvasServer-update-*.zip 拖到此 bat 上
  echo 或：  "%~nx0" "路径\CanvasServer-update-xxxx.zip"
  echo.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\apply_update.ps1" -InstallDir "%~dp0" -UpdateZip "%~1"
set ERR=%ERRORLEVEL%
echo.
if not "%ERR%"=="0" (
  echo 更新失败，请检查上方错误信息。
) else (
  echo 更新完成。请关闭并重新启动「启动画布服务器.bat」。
)
pause
exit /b %ERR%
