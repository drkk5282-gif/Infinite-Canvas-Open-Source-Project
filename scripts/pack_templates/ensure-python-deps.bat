@echo off
REM Called from pack root:  call "%~dp0scripts\ensure-python-deps.bat"
REM On success sets PY to the interpreter path (venv preferred).
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0.."

set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"

if defined PY goto :check_deps

set "SYS_PY="
where python >nul 2>&1 && set "SYS_PY=python"
if not defined SYS_PY where py >nul 2>&1 && set "SYS_PY=py"
if not defined SYS_PY (
  echo.
  echo [ERROR] Python not found. Install Python 3.10+ and retry.
  echo         https://www.python.org/downloads/
  echo.
  exit /b 1
)

echo [deps] Creating .venv ...
if /i "!SYS_PY!"=="py" (
  py -3 -m venv .venv
) else (
  "!SYS_PY!" -m venv .venv
)
if errorlevel 1 (
  echo [ERROR] Failed to create .venv
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv\Scripts\python.exe missing after venv create
  exit /b 1
)
set "PY=%CD%\.venv\Scripts\python.exe"

:check_deps
if not exist "requirements.txt" (
  echo [ERROR] requirements.txt not found in %CD%
  exit /b 1
)

set "NEED_INSTALL=0"
if not exist ".venv\.deps-ok" set "NEED_INSTALL=1"
if exist ".venv\.deps-ok" (
  fc /b "requirements.txt" ".venv\.deps-req" >nul 2>&1
  if errorlevel 1 set "NEED_INSTALL=1"
)

if "!NEED_INSTALL!"=="0" (
  "!PY!" -c "import fastapi,uvicorn,httpx,pydantic" >nul 2>&1
  if errorlevel 1 set "NEED_INSTALL=1"
)

if "!NEED_INSTALL!"=="1" (
  echo [deps] Installing / updating packages from requirements.txt ...
  "!PY!" -m pip install -U pip
  if errorlevel 1 (
    echo [ERROR] pip upgrade failed
    exit /b 1
  )
  "!PY!" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] pip install failed
    exit /b 1
  )
  copy /y "requirements.txt" ".venv\.deps-req" >nul
  echo ok> ".venv\.deps-ok"
  echo [deps] Done.
)

REM Dependencies for the canvas HTTP API pack.

REM Export PY to caller (setlocal would otherwise discard it)
endlocal & set "PY=%PY%"
exit /b 0
