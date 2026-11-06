@echo off
rem Doble clic: corre SOLO el nodo Power BI en esta consola, para ver paso a paso que hace.
rem (Si el nodo padre no esta corriendo, lo que envia se pierde, pero se ve todo el proceso.)
cd /d "%~dp0"
set "PY=..\..\.venv\Scripts\python.exe"
if not exist "%PY%" (
    where py >NUL 2>NUL && (set "PY=py -3") || (set "PY=python")
)
title nodo_powerbi_viki
%PY% main.py %*
echo.
echo El nodo termino (codigo %ERRORLEVEL%). Revisa los mensajes de arriba.
pause
