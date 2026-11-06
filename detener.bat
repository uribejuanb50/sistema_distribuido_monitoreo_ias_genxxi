@echo off
rem Doble clic: detiene todos los procesos iniciados con iniciar.bat / iniciar.py.
cd /d "%~dp0"
".venv\Scripts\python.exe" iniciar.py --detener
pause
