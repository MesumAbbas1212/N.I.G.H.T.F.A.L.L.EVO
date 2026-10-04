@echo off
setlocal EnableExtensions EnableDelayedExpansion

title NIGHTFALL Evo - Launcher
cd /d "%~dp0"

color 0E

echo ==========================================================================
echo  ================================================================
echo  =              N I G H T F A L L   E V O               =
echo  ================================================================
echo                       NIGHTFALL EVO
echo          AUTONOMOUS SELF-EVOLUTION COGNITIVE ENGINE
echo    [Skill Forge // Crucible Sandbox // 180 FPS HoloCore]
echo ==========================================================================
echo.
echo Launching automated bootstrap sequence...

powershell.exe -ExecutionPolicy Bypass -File "%~dp0bootstrap.ps1"

if %errorlevel% neq 0 (
  echo ERROR: Bootstrap failed.
  pause
  exit /b %errorlevel%
)
exit /b 0
