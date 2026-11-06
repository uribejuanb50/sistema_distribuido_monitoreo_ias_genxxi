@echo off
rem Abre Edge con el puerto de depuracion para que el nodo Power BI se conecte a TU sesion
rem (en config.json: "modo_sesion": "conectar"). Inicias sesion en esta ventana UNA vez y queda guardada.
rem Usa la misma carpeta de perfil que el modo "perfil_propio" (desde Edge 136 el puerto de depuracion
rem no funciona con el perfil principal del navegador, por eso es una carpeta aparte).
set "PERFIL=%LOCALAPPDATA%\sistema_recoleccion_xxi\perfil_navegador_powerbi"
set "EDGE=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
if not exist "%EDGE%" set "EDGE=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
start "" "%EDGE%" --remote-debugging-port=9222 --user-data-dir="%PERFIL%" --no-first-run --no-default-browser-check "https://app.powerbi.com/groups/me/reports/4964bcb3-33b1-42c4-a16f-99223f17c8be/461a7eb191670baa83a2?ctid=46bb22b8-4c2c-40ff-8360-7b6334821279&experience=power-bi"
echo Edge abierto. Inicia sesion en esa ventana si te la pide y deja Edge abierto mientras corre el nodo.
