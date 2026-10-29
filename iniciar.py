"""Inicia (o detiene) todo el sistema con un solo comando.

Cada proceso se abre en su propia consola, desde su propia carpeta y con el Python
del entorno virtual (.venv). Orden de arranque:
  1. Nodo padre, y se espera a que abra sus puertos.
  2. Healthchecker (vigila al padre y lo relanza si se cae).
  3. Nodo hijo del Excel (y, con --todo, el nodo CSV de ejemplo y el dashboard).
El padre arranca antes que los hijos para que no se pierda el primer envío del Excel.

Uso (desde la raíz del proyecto):
    python iniciar.py                 # padre + healthchecker + nodo Excel
    python iniciar.py --todo          # además el nodo CSV de ejemplo y el dashboard
    python iniciar.py --sin-ventanas  # todo en segundo plano (la salida queda en logs/)
    python iniciar.py --estado        # muestra qué procesos están vivos
    python iniciar.py --detener       # detiene todo lo iniciado
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parent
DIR_VENV = RAIZ_PROYECTO / ".venv"
PYTHON_VENV = DIR_VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

# Si se ejecuta con el Python del sistema, se vuelve a lanzar con el del entorno virtual.
if PYTHON_VENV.exists() and Path(sys.prefix).resolve() != DIR_VENV.resolve():
    sys.exit(subprocess.call([str(PYTHON_VENV), str(Path(__file__).resolve()), *sys.argv[1:]]))

sys.path.insert(0, str(RAIZ_PROYECTO))

import psutil  # noqa: E402

from comun.utilidades import cargar_json, resolver_ruta  # noqa: E402

RUTA_REGISTRO = RAIZ_PROYECTO / "run" / "procesos.json"
ESPERA_PADRE_SEGUNDOS = 20
SCRIPTS_PROPIOS = ("main.py", "healthchecker.py", "streamlit")  # para no tocar procesos ajenos


@dataclass(frozen=True)
class Proceso:
    nombre: str
    carpeta: Path
    argumentos: tuple[str, ...]


PADRE = Proceso("nodo_padre", RAIZ_PROYECTO / "nodo_padre" / "nodo_padre", ("main.py",))
HEALTHCHECKER = Proceso("healthchecker", RAIZ_PROYECTO / "nodo_padre", ("healthchecker.py",))
NODO_EXCEL = Proceso("nodo_excel_cumplimiento", RAIZ_PROYECTO / "nodos_hijo" / "nodo_excel_cumplimiento",
                     ("main.py",))
NODO_CSV = Proceso("nodo_plantilla_csv", RAIZ_PROYECTO / "nodos_hijo" / "_plantilla_nodo", ("main.py",))
DASHBOARD = Proceso("dashboard", RAIZ_PROYECTO, ("-m", "streamlit", "run", "dashboard/app.py"))
# Al detener, el healthchecker va primero para que no relance al padre.
ORDEN_DETENCION = ("healthchecker", "nodo_excel_cumplimiento", "nodo_plantilla_csv", "dashboard", "nodo_padre")


# ---------------------------------------------------------------- procesos

def proceso_propio(pid: int | None) -> psutil.Process | None:
    """El proceso con ese PID si sigue vivo y es uno de los nuestros."""
    if not pid:
        return None
    try:
        proceso = psutil.Process(pid)
        linea = " ".join(proceso.cmdline())
        if proceso.is_running() and any(script in linea for script in SCRIPTS_PROPIOS):
            return proceso
    except psutil.Error:
        pass
    return None


def leer_pid(ruta: Path) -> int | None:
    try:
        return int(ruta.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def lanzar(proceso: Proceso, con_ventana: bool, extra: tuple[str, ...] = ()) -> int:
    """Ejecuta el proceso en su carpeta, en una consola nueva o en segundo plano."""
    if os.name == "nt":
        banderas = subprocess.CREATE_NEW_CONSOLE if con_ventana else subprocess.CREATE_NO_WINDOW
    else:
        banderas = 0
    salida = None if con_ventana else subprocess.DEVNULL
    hijo = subprocess.Popen([sys.executable, *proceso.argumentos, *extra], cwd=proceso.carpeta,
                            creationflags=banderas, stdin=subprocess.DEVNULL, stdout=salida, stderr=salida)
    print(f"  - {proceso.nombre:26} iniciado (pid {hijo.pid})")
    return hijo.pid


def terminar_arbol(proceso: psutil.Process) -> None:
    """Termina el proceso y sus hijos (el Python del .venv lanza un subproceso en Windows)."""
    try:
        familia = [*proceso.children(recursive=True), proceso]
    except psutil.Error:
        return
    for miembro in familia:
        try:
            miembro.terminate()
        except psutil.Error:
            pass
    _, vivos = psutil.wait_procs(familia, timeout=5)
    for miembro in vivos:
        try:
            miembro.kill()
        except psutil.Error:
            pass


# ---------------------------------------------------------------- registro de PIDs

def leer_registro() -> dict[str, int]:
    try:
        return json.loads(RUTA_REGISTRO.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar_registro(registro: dict[str, int]) -> None:
    RUTA_REGISTRO.parent.mkdir(parents=True, exist_ok=True)
    RUTA_REGISTRO.write_text(json.dumps(registro, indent=2), encoding="utf-8")


def ruta_pid_padre() -> Path:
    return resolver_ruta(cargar_json(PADRE.carpeta / "config.json")["ruta_pid"])


def esperar_padre(ruta_pid: Path) -> bool:
    """Espera a que el padre escriba su PID (lo hace después de abrir sus puertos)."""
    limite = time.monotonic() + ESPERA_PADRE_SEGUNDOS
    while time.monotonic() < limite:
        if proceso_propio(leer_pid(ruta_pid)):
            return True
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------- acciones

def iniciar(todo: bool, con_ventana: bool) -> int:
    activos = [nombre for nombre, pid in leer_registro().items() if proceso_propio(pid)]
    if activos:
        print(f"El sistema ya está en ejecución ({', '.join(activos)}). Usa 'python iniciar.py --detener' primero.")
        return 1

    config_excel = cargar_json(NODO_EXCEL.carpeta / "config.json")
    ruta_excel = resolver_ruta(config_excel["ruta_archivo"])
    print(f"Excel a leer: {ruta_excel}")
    if not ruta_excel.exists():
        print("  AVISO: el archivo no existe. El nodo arrancará y lo reintentará cada "
              f"{config_excel['intervalo_segundos']} s. Revisa 'ruta_archivo' en {NODO_EXCEL.carpeta / 'config.json'}")

    print("Iniciando procesos:")
    registro: dict[str, int] = {}
    ruta_pid = ruta_pid_padre()
    padre_existente = proceso_propio(leer_pid(ruta_pid))
    if padre_existente:
        print(f"  - nodo_padre                 ya estaba en ejecución (pid {padre_existente.pid})")
    else:
        ruta_pid.unlink(missing_ok=True)  # PID viejo de una ejecución anterior
        registro[PADRE.nombre] = lanzar(PADRE, con_ventana)
        guardar_registro(registro)
        print("    esperando a que el nodo padre abra sus puertos...")
        if not esperar_padre(ruta_pid):
            print(f"ERROR: el nodo padre no arrancó en {ESPERA_PADRE_SEGUNDOS} s. Revisa logs/nodo_padre.log "
                  "(¿otro programa usa los puertos 5555/5556?). Deteniendo lo iniciado.")
            detener()
            return 1

    # En segundo plano el healthchecker relanza al padre sin abrir ventanas.
    registro[HEALTHCHECKER.nombre] = lanzar(HEALTHCHECKER, con_ventana, () if con_ventana else ("--misma-consola",))
    registro[NODO_EXCEL.nombre] = lanzar(NODO_EXCEL, con_ventana)
    if todo:
        registro[NODO_CSV.nombre] = lanzar(NODO_CSV, con_ventana)
        registro[DASHBOARD.nombre] = lanzar(DASHBOARD, con_ventana,
                                            () if con_ventana else ("--server.headless", "true"))
    guardar_registro(registro)

    print("\nSistema iniciado.")
    print(f"  Alertas para Power Automate: {resolver_ruta(cargar_json(PADRE.carpeta / 'config.json')['ruta_json_salida'])}")
    if todo:
        print("  Dashboard: http://localhost:8501")
    if not con_ventana:
        print(f"  Registros de cada proceso: {RAIZ_PROYECTO / 'logs'}")
    print("  Para detener todo: python iniciar.py --detener")
    return 0


def detener() -> int:
    registro = leer_registro()
    registro[PADRE.nombre + " (actual)"] = leer_pid(ruta_pid_padre()) or 0  # pudo ser relanzado con otro PID
    detenidos = 0
    for nombre in (*ORDEN_DETENCION, PADRE.nombre + " (actual)"):
        proceso = proceso_propio(registro.get(nombre))
        if proceso:
            terminar_arbol(proceso)
            print(f"  - {nombre:26} detenido (pid {proceso.pid})")
            detenidos += 1
    RUTA_REGISTRO.unlink(missing_ok=True)
    ruta_pid_padre().unlink(missing_ok=True)
    print("Sistema detenido." if detenidos else "No había procesos en ejecución.")
    return 0


def mostrar_estado() -> int:
    registro = leer_registro()
    pid_padre = leer_pid(ruta_pid_padre())
    print(f"  - {'nodo_padre (actual)':26} {'activo' if proceso_propio(pid_padre) else 'detenido'}"
          + (f" (pid {pid_padre})" if pid_padre else ""))
    for nombre in ORDEN_DETENCION[:-1]:
        if nombre in registro:
            estado = "activo" if proceso_propio(registro[nombre]) else "detenido"
            print(f"  - {nombre:26} {estado} (pid {registro[nombre]})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicia o detiene todo el sistema.")
    accion = parser.add_mutually_exclusive_group()
    accion.add_argument("--detener", action="store_true", help="Detiene todos los procesos iniciados")
    accion.add_argument("--estado", action="store_true", help="Muestra qué procesos están vivos")
    parser.add_argument("--todo", action="store_true", help="Incluye el nodo CSV de ejemplo y el dashboard")
    parser.add_argument("--sin-ventanas", action="store_true", help="Ejecuta todo en segundo plano")
    args = parser.parse_args()
    if args.detener:
        return detener()
    if args.estado:
        return mostrar_estado()
    return iniciar(args.todo, con_ventana=not args.sin_ventanas)


if __name__ == "__main__":
    sys.exit(main())
