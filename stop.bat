@echo off
chcp 65001 >nul
title RAG Stopper

echo Stopping services on ports 8000 / 8001...
setlocal enabledelayedexpansion
set FOUND=0
for %%P in (8000 8001) do (
  for /f "tokens=5" %%A in ('netstat -ano ^| findstr ":%%P " ^| findstr LISTENING 2^>nul') do (
    echo   Killing PID %%A on port %%P
    taskkill /F /PID %%A >nul 2>&1
    set FOUND=1
  )
)
if "!FOUND!"=="0" echo   No running RAG services found.
endlocal

echo.
echo Done. Milvus containers are still running in background.
echo Use stop-milvus.bat to stop DB too.
pause