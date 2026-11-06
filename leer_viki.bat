@echo off
rem ==================================================================================
rem  LECTOR VIKI: UN solo script, UNA sola consola.
rem  Abre Edge con Selenium (visible), carga el tablero Power BI de VIKI, cada 15 s
rem  RECARGA la pagina, inyecta el script de captura y le manda los datos al nodo padre.
rem  Todo lo que hace se ve en ESTA consola (y queda en logs\nodo_powerbi_viki.log).
rem  El nodo padre debe estar corriendo aparte para recibir (iniciar.bat lo arranca).
rem  Ctrl+C para detenerlo.
rem ==================================================================================
title Lector VIKI (Power BI)
cd /d "%~dp0"
set "RAIZ=%~dp0."
rem ---- Python a usar (en este orden) ----------------------------------------------
rem  1. RUTA_PYTHON: si tu .venv esta en otra carpeta, pega aqui la ruta de su python.exe, p. ej.
rem     set "RUTA_PYTHON=C:\Users\familia\mis_entornos\.venv\Scripts\python.exe"
rem  2. El entorno que este ACTIVADO en la consola desde donde corres el .bat (VIRTUAL_ENV).
rem  3. Un .venv dentro de la carpeta del proyecto o en la carpeta de arriba.
rem  4. El Python del sistema (py / python).
set "RUTA_PYTHON="
set "PY="
if defined RUTA_PYTHON if exist "%RUTA_PYTHON%" set "PY=%RUTA_PYTHON%"
if not defined PY if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" set "PY=%VIRTUAL_ENV%\Scripts\python.exe"
if not defined PY if exist "%RAIZ%\.venv\Scripts\python.exe" set "PY=%RAIZ%\.venv\Scripts\python.exe"
if not defined PY if exist "%RAIZ%\..\.venv\Scripts\python.exe" set "PY=%RAIZ%\..\.venv\Scripts\python.exe"
if not defined PY if exist "%RAIZ%\venv\Scripts\python.exe" set "PY=%RAIZ%\venv\Scripts\python.exe"
if not defined PY (
    for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>NUL') do set "PY=%%i"
)
if not defined PY (
    for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)" 2^>NUL') do set "PY=%%i"
)
if not defined PY (
    echo ERROR: no se encontro ningun Python. Pon la ruta de tu python.exe en RUTA_PYTHON al inicio de este .bat.
    pause
    exit /b 1
)
echo Usando Python: %PY%
"%PY%" -c "import selenium, zmq" 2>NUL || (
    echo.
    "%PY%" -c "import selenium, zmq"
    echo.
    echo ERROR: a ese Python le faltan librerias ^(selenium o pyzmq^).
    echo   - Si tu .venv esta en otra carpeta, pon su python.exe en RUTA_PYTHON al inicio de este .bat,
    echo     o activa el .venv en una consola y corre el .bat desde ahi.
    echo   - O instala en ESE Python:  "%PY%" -m pip install -r "%RAIZ%\requirements.txt"
    pause
    exit /b 1
)
rem -----------------------------------------------------------------------------------
cd /d "%RAIZ%\nodos_hijo\nodo_powerbi_viki"
"%PY%" main.py %*
echo.
if errorlevel 1 (echo El lector se detuvo por un error. Revisa los mensajes de arriba ^(y logs\powerbi_error.png si se genero^).) else (echo El lector termino.)
pause
