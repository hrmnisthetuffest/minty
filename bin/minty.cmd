@echo off
rem Start Minty from this checkout: bin\minty.cmd, bin\minty.cmd --no-panel, bin\minty.cmd set-key groq
rem Uses .venv\Scripts\python.exe (or the interpreter in %MINTY_PYTHON%) and never needs an install step.
setlocal
for %%I in ("%~dp0..") do set "HERE=%%~fI"
set "PY=%MINTY_PYTHON%"
if "%PY%"=="" set "PY=%HERE%\.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo minty: no Python at %PY%; run install.ps1 from the checkout, or set MINTY_PYTHON 1>&2
  exit /b 1
)
rem UTF-8 for text files and for what Minty prints: Windows' default code page garbles anything but plain English
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if defined PYTHONPATH (set "PYTHONPATH=%HERE%\src;%PYTHONPATH%") else (set "PYTHONPATH=%HERE%\src")
"%PY%" -m minty_core %*
exit /b %ERRORLEVEL%
