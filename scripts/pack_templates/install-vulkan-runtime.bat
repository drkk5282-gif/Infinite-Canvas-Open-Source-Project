@echo off
chcp 65001 >nul
echo Installing Khronos Vulkan Runtime via winget...
winget install --id KhronosGroup.VulkanRT -e --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo.
  echo winget failed. Please open:
  echo   https://vulkan.lunarg.com/sdk/home
  echo Download and install Vulkan Runtime, then restart CanvasServer.
  pause
  exit /b 1
)
echo Done. Restart CanvasServer after closing this window.
pause
