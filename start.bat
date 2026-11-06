@echo off
rem Doble clic: busca un Python y corre start.py, que hace el resto.
setlocal
pushd "%~dp0"
if not exist start.py (
    echo Descomprime el zip completo y abre start.bat desde esa carpeta.
    pause
    exit /b 1
)
set "PYTHON="
py -3 -c "" >nul 2>nul && set "PYTHON=py -3"
if not defined PYTHON python -c "" >nul 2>nul && set "PYTHON=python"
if not defined PYTHON (
    echo Falta Python 3.12 o 3.13. Descarga: https://www.python.org/downloads/
    pause
    exit /b 1
)
%PYTHON% start.py %*
set "CODIGO=%errorlevel%"
if not "%CODIGO%"=="0" pause
exit /b %CODIGO%
