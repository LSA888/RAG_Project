@echo off
chcp 65001 >nul
cd /d "%~dp0"
title RAG Starter

echo ============================================
echo   RAG Project - One-Click Starter
echo ============================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo [ERROR] .venv not found! Run: uv sync
  echo.
  pause
  exit /b 1
)

echo [1/3] Checking Milvus containers...
docker info >nul 2>&1
if errorlevel 1 (
  echo       [SKIP] Docker Desktop not running.
  echo       Start Docker Desktop first, then re-run this script.
) else (
  docker compose -f deploy\milvus\docker-compose.yml up -d
  echo       Milvus containers up.
)
echo.

echo [2/3] Cleaning ports 8000 / 8001...
for %%P in (8000 8001) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING 2^>nul') do (
    taskkill /F /PID %%A >nul 2>&1
  )
)

echo [3/3] Launching services...
echo.
start "RAG-Import-8000" cmd /k "cd /d %~dp0 && set PYTHONPATH=%~dp0 && .venv\Scripts\python.exe app\import_process\api\file_import_service.py"
start "RAG-Query-8001"  cmd /k "cd /d %~dp0 && set PYTHONPATH=%~dp0 && .venv\Scripts\python.exe app\query_process\api\query_service.py"

echo ============================================
echo  Started! Wait ~60s for models to load.
echo  Access:
echo    Home:    http://127.0.0.1:8000/
echo    Import:  http://127.0.0.1:8000/import.html
echo    Chat:    http://127.0.0.1:8001/chat.html
echo ============================================
echo.
pause