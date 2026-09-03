@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Stop Milvus

echo Stopping RAG services...
for %%P in (8000 8001) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING 2^>nul') do (
    taskkill /F /PID %%A >nul 2>&1
  )
)

echo.
echo Stopping Milvus containers...
docker compose -f deploy\milvus\docker-compose.yml stop

echo.
echo All stopped. Data preserved. Next start.bat will resume quickly.
pause