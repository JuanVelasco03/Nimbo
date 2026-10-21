"""
vpn_wg.py -- enciende y apaga un tunel WireGuard, para supervisor.py

Es el puente entre `supervisor.py --vpn-on/--vpn-off` y WireGuard. Se usa asi:

    python supervisor.py --vpn-on  "python scripts\\vpn_wg.py on" ^
                         --vpn-off "python scripts\\vpn_wg.py off"

Por que WireGuard y no la app de Proton: la app de Windows no tiene linea de
comandos. Los .conf de WireGuard que te da tu cuenta de Proton si se pueden
manejar desde un script, y ademas eliges el pais en el propio archivo.

Preparacion (una sola vez, consola como administrador):

    1. Instala WireGuard para Windows.
    2. Baja un .conf de account.protonvpn.com (Downloads -> WireGuard), del
       pais que quieras.
    3. python vpn_wg.py instalar "C:\\ruta\\proton-nl.conf"
    4. python vpn_wg.py permitir-usuario
       (para que despues los on/off no pidan administrador)

Despues, ya sin administrador:

    python vpn_wg.py on        enciende y espera a que el tunel este arriba
    python vpn_wg.py off       lo apaga
    python vpn_wg.py estado    dice como esta

No hace falta decir cual es el tunel: 'instalar' lo deja anotado en
vpn_tunel.txt, al lado de este script. Si aun asi no lo sabe y hay un solo
tunel de WireGuard instalado, usa ese. Para cambiarlo:

    python vpn_wg.py usar otro-pais

Orden de preferencia: --tunel > GRAB_WG_TUNEL > vpn_tunel.txt > el unico
que haya instalado.
"""

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
# aqui queda anotado el tunel, para no tener que repetirlo en cada consola
# (un 'set' solo vale para esa ventana, y era un fastidio)
ARCHIVO_TUNEL = os.path.join(AQUI, "vpn_tunel.txt")

TUNEL = os.environ.get("GRAB_WG_TUNEL", "")
ESPERA = float(os.environ.get("GRAB_WG_ESPERA", "20"))   # s para que arranque

# los servicios de tunel de WireGuard se llaman siempre asi
PREFIJO = "WireGuardTunnel$"

# CC=consultar config, LC=consultar estado, RP=arrancar, WP=parar
PERMISOS = "CCLCRPWP"


def servicio(tunel: str) -> str:
    return PREFIJO + tunel


def tunel_guardado() -> str:
    try:
        with io.open(ARCHIVO_TUNEL, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def guardar_tunel(tunel: str) -> None:
    try:
        with io.open(ARCHIVO_TUNEL, "w", encoding="utf-8") as f:
            f.write(tunel + "\n")
    except OSError as e:
        print("(no pude anotar el tunel en %s: %s)" % (ARCHIVO_TUNEL, e))


def tuneles_instalados() -> list:
    """Los tuneles de WireGuard que hay dados de alta en Windows."""
    _, salida = _sc("query", "type=", "service", "state=", "all")
    vistos = []
    for m in re.finditer(re.escape(PREFIJO) + r"(\S+)", salida):
        nombre = m.group(1).strip()
        if nombre not in vistos:
            vistos.append(nombre)
    return vistos


def resolver_tunel(pedido: str) -> tuple:
    """-> (tunel, de_donde). Cadena de preferencia, de mas a menos explicito."""
    if pedido:
        return pedido, "--tunel"
    if TUNEL:
        return TUNEL, "GRAB_WG_TUNEL"
    guardado = tunel_guardado()
    if guardado:
        return guardado, os.path.basename(ARCHIVO_TUNEL)
    instalados = tuneles_instalados()
    if len(instalados) == 1:
        return instalados[0], "el unico instalado"
    return "", "varios: " + ", ".join(instalados) if instalados else ""


def _sc(*args, admin_hint: str = "") -> tuple:
    """Corre sc.exe. -> (codigo, salida)."""
    try:
        p = subprocess.run(["sc.exe", *args], capture_output=True, text=True)
    except OSError as e:
        return 1, str(e)
    salida = ((p.stdout or "") + (p.stderr or "")).strip()
    if p.returncode == 5 and admin_hint:          # acceso denegado
        salida += "\n" + admin_hint
    return p.returncode, salida


# sc.exe traduce las etiquetas ("ESTADO" en vez de "STATE") pero no la palabra
# del estado. Por eso se busca la palabra, no la etiqueta: si no, en un Windows
# en espanol todo salia como desconocido.
RE_ESTADO = re.compile(
    r":\s*\d+\s+(RUNNING|STOPPED|START_PENDING|STOP_PENDING|"
    r"CONTINUE_PENDING|PAUSE_PENDING|PAUSED)\b", re.I)


def estado(tunel: str) -> str:
    """-> 'RUNNING' | 'STOPPED' | 'NO_EXISTE' | 'OTRO:<x>'."""
    codigo, salida = _sc("query", servicio(tunel))
    if codigo == 1060 or "1060" in salida:        # el servicio no existe
        return "NO_EXISTE"
    m = RE_ESTADO.search(salida)
    if not m:
        return "NO_EXISTE" if codigo else "OTRO:?"
    palabra = m.group(1).upper()
    return palabra if palabra in {"RUNNING", "STOPPED"} else "OTRO:" + palabra


def esperar(tunel: str, quiero: str, limite: float) -> bool:
    fin = time.time() + limite
    while time.time() < fin:
        actual = estado(tunel)
        if actual == quiero:
            return True
        if actual == "NO_EXISTE":
            return False
        time.sleep(0.5)
    return estado(tunel) == quiero


# --------------------------------------------------------------------------
# Preparacion
# --------------------------------------------------------------------------

def buscar_wireguard() -> str:
    exe = shutil.which("wireguard")
    if exe:
        return exe
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", "")):
        if not base:
            continue
        ruta = os.path.join(base, "WireGuard", "wireguard.exe")
        if os.path.exists(ruta):
            return ruta
    return ""


def instalar(conf: str) -> int:
    exe = buscar_wireguard()
    if not exe:
        print("No encuentro wireguard.exe. Instala WireGuard para Windows:")
        print("  https://www.wireguard.com/install/")
        return 2
    if not os.path.exists(conf):
        print("No existe el archivo: %s" % conf)
        return 2
    tunel = os.path.splitext(os.path.basename(conf))[0]
    print("Instalando el tunel '%s' desde %s" % (tunel, conf))
    p = subprocess.run([exe, "/installtunnelservice", os.path.abspath(conf)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        print((p.stdout or "") + (p.stderr or ""))
        print("Fallo. Abri la consola como administrador y proba de nuevo.")
        return 1
    # queda conectado y arrancando con Windows: lo dejamos parado y manual
    _sc("stop", servicio(tunel))
    _sc("config", servicio(tunel), "start=", "demand")
    guardar_tunel(tunel)
    print("Listo. El tunel es '%s' (anotado en %s)."
          % (tunel, os.path.basename(ARCHIVO_TUNEL)))
    print("Ya no hace falta pasar --tunel ni poner ninguna variable.")
    print("Ahora, en esta misma consola de administrador:")
    print("    python vpn_wg.py permitir-usuario")
    return 0


def sid_usuario() -> str:
    try:
        p = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"],
                           capture_output=True, text=True)
        m = re.search(r"(S-1-[\d-]+)", p.stdout or "")
        return m.group(1) if m else ""
    except OSError:
        return ""


def permitir_usuario(tunel: str) -> int:
    """Deja que tu usuario arranque/pare el tunel sin ser administrador."""
    sid = sid_usuario()
    if not sid:
        print("No pude averiguar tu SID de usuario.")
        return 1
    codigo, sd = _sc("sdshow", servicio(tunel))
    if codigo != 0 or "D:" not in sd:
        print("No pude leer los permisos del servicio %s" % servicio(tunel))
        print(sd)
        return 1
    sd = sd.strip().splitlines()[-1].strip()
    if sid in sd:
        print("Tu usuario ya tenia permiso.")
        return 0
    ace = "(A;;%s;;;%s)" % (PERMISOS, sid)
    # la ACE va dentro del DACL (D:), antes del SACL (S:) si lo hay
    corte = sd.find("S:")
    nuevo = (sd[:corte] + ace + sd[corte:]) if corte > 0 else sd + ace
    codigo, salida = _sc(
        "sdset", servicio(tunel), nuevo,
        admin_hint="Abri la consola como administrador para este paso.")
    if codigo != 0:
        print(salida)
        return 1
    print("Hecho: ya podes hacer on/off sin administrador.")
    return 0


# --------------------------------------------------------------------------

def encender(tunel: str) -> int:
    actual = estado(tunel)
    if actual == "NO_EXISTE":
        print("El tunel '%s' no esta instalado. Corre primero:" % tunel)
        print("    python vpn_wg.py instalar \"C:\\ruta\\%s.conf\"" % tunel)
        return 2
    if actual == "RUNNING":
        print("ya estaba conectado (%s)" % tunel)
        return 0
    codigo, salida = _sc(
        "start", servicio(tunel),
        admin_hint="Corre una vez: python vpn_wg.py permitir-usuario")
    if codigo != 0 and estado(tunel) != "RUNNING":
        print(salida)
        return 1
    if not esperar(tunel, "RUNNING", ESPERA):
        print("el tunel no llego a arrancar en %ds" % int(ESPERA))
        return 1
    print("conectado (%s)" % tunel)
    return 0


def apagar(tunel: str) -> int:
    actual = estado(tunel)
    if actual == "NO_EXISTE":
        print("El tunel '%s' no esta instalado" % tunel)
        return 2
    if actual == "STOPPED":
        print("ya estaba desconectado (%s)" % tunel)
        return 0
    codigo, salida = _sc(
        "stop", servicio(tunel),
        admin_hint="Corre una vez: python vpn_wg.py permitir-usuario")
    if codigo != 0 and estado(tunel) != "STOPPED":
        print(salida)
        return 1
    if not esperar(tunel, "STOPPED", ESPERA):
        print("el tunel no llego a pararse en %ds" % int(ESPERA))
        return 1
    print("desconectado (%s)" % tunel)
    return 0


def main(argv) -> int:
    p = argparse.ArgumentParser(
        description="Enciende y apaga un tunel WireGuard (para supervisor.py)")
    p.add_argument("accion",
                   choices=["on", "off", "estado", "instalar", "usar",
                            "permitir-usuario"])
    p.add_argument("conf", nargs="?", default="",
                   help="ruta del .conf ('instalar') o nombre del tunel ('usar')")
    p.add_argument("--tunel", default="",
                   help="tunel a usar solo esta vez")
    args = p.parse_args(argv)

    if args.accion == "instalar":
        if not args.conf:
            print("uso: python vpn_wg.py instalar \"C:\\ruta\\pais.conf\"")
            return 2
        return instalar(args.conf)

    if args.accion == "usar":
        if not args.conf:
            print("uso: python vpn_wg.py usar NOMBRE-DEL-TUNEL")
            instalados = tuneles_instalados()
            if instalados:
                print("instalados: " + ", ".join(instalados))
            return 2
        guardar_tunel(args.conf)
        print("A partir de ahora se usa '%s'." % args.conf)
        return 0

    tunel, de_donde = resolver_tunel(args.tunel)
    if not tunel:
        print("No se que tunel usar.")
        if de_donde.startswith("varios"):
            print("Hay %s. Elegi uno:" % de_donde)
            print("    python vpn_wg.py usar NOMBRE")
        else:
            print("Instala uno primero:")
            print("    python vpn_wg.py instalar \"C:\\ruta\\pais.conf\"")
        return 2

    if args.accion == "permitir-usuario":
        return permitir_usuario(tunel)
    if args.accion == "estado":
        print("%s: %s" % (tunel, estado(tunel)))
        return 0
    return encender(tunel) if args.accion == "on" else apagar(tunel)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
