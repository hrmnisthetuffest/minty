@echo off
rem Double-click to install Minty on Windows. It runs install.ps1 with the script policy relaxed for this one run only
rem (the setting on your computer is not changed). Extra words are passed on:  install.cmd -NoOnboard
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "CODE=%ERRORLEVEL%"
if not "%CODE%"=="0" (
  echo.
  echo The install did not finish. The message above says why.
  pause
)
exit /b %CODE%
