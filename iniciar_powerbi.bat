@echo off
rem Doble clic: inicia el nodo padre, el healthchecker y el nodo Power BI de VIKI, cada uno en su consola.
rem La consola "nodo_powerbi_viki" muestra todo lo que hace (recarga, espera de carga, inyeccion, envios)
rem y NO se cierra si el servicio se cae, para que se pueda leer el error.
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    where py >NUL 2>NUL && (set "PY=py -3") || (set "PY=python")
)
%PY% iniciar.py --solo-powerbi %*
pause
