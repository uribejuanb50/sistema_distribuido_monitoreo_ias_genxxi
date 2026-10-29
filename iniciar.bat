@echo off
rem Doble clic: inicia el nodo padre, el healthchecker y el nodo del Excel, cada uno en su consola.
rem Para incluir el nodo CSV y el dashboard, ejecutar en consola:  iniciar.bat --todo
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo No se encontro el entorno virtual .venv. Sigue los pasos de instalacion del README.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" iniciar.py %*
pause
