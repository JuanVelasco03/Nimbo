"""Las claves de actualizacion y la firma del manifiesto a mano (SPEC 17,
C12).

    rem una sola vez en la vida: el par de claves
    python empaquetado\\actualizaciones\\claves.py --generar

    rem probar el camino completo sin red
    python empaquetado\\actualizaciones\\claves.py --probar

    rem firmar un manifiesto de verdad, para publicar en el repo
    python empaquetado\\actualizaciones\\claves.py --firmar --privada <d> ^
        --version 1.1.0 --notas "texto corto" ^
        --url "https://github.com/JuanVelasco03/Nimbo/releases/tag/v1.1.0" ^
        > releases\\manifest.json

Par separado del de licencia (`empaquetado\\licencia\\claves.py`): si uno
se filtra, no compromete al otro. La **publica** va pegada adentro de
`nimbo\\actualizaciones.py` tal cual, sin pasar por Nuitka -- a diferencia
de la licencia, esto no protege contra un binario parchado: solo evita
que un `manifest.json` falso en `raw.githubusercontent.com` dispare un
aviso de actualizacion falso, y para eso alcanza con que la clave este en
el codigo (dev y paquete ven la misma). La **privada** se guarda aparte,
nunca va a git, y solo hace falta para firmar un release nuevo.

Usa `nimbo\\firma.py`, el mismo modulo Ed25519 que verifica del lado del
usuario: si lo que firma esto no lo acepta aquel, el error salta aca y no
en la maquina de otro.
"""
import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "nimbo"))

import firma            # noqa: E402  despues de tocar sys.path


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def firmar_manifiesto(privada: bytes, version: str, notas: str, url: str) -> dict:
    """El mismo formato que espera `nimbo\\actualizaciones.py`: se firman
    los bytes **crudos** del JSON sin la clave "firma" -- separators sin
    espacios y sort_keys, igual criterio que
    `empaquetado\\licencia\\claves.py:emitir` (reserializar del otro lado
    daria otro mensaje y la firma no cerraria)."""
    base = {"version": version, "notas": notas, "url": url}
    crudo = json.dumps(base, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return {**base, "firma": b64e(firma.firmar(privada, crudo))}


def _crudo_sin_firma(manifiesto: dict) -> bytes:
    sin_firma = {k: v for k, v in manifiesto.items() if k != "firma"}
    return json.dumps(sin_firma, separators=(",", ":"), sort_keys=True).encode("utf-8")


def cmd_generar():
    privada, publica = firma.generar()
    print()
    print("  clave PUBLICA  (pegarla tal cual en nimbo\\actualizaciones.py):")
    print("    CLAVE_PUBLICA = \"" + b64e(publica) + "\"")
    print()
    print("  clave PRIVADA  (para firmar releases -- NO va a git):")
    print("    " + b64e(privada))
    print()
    print("  Guardala en un gestor de contrasenas. Perderla obliga a rotar")
    print("  el par (CLAVE_PUBLICA nueva en el codigo) y las instalaciones")
    print("  viejas quedan sin aviso de actualizacion hasta que actualicen")
    print("  a mano una vez.")


def cmd_firmar(args):
    if not args.privada:
        raise SystemExit("falta --privada")
    if not (args.version and args.url):
        raise SystemExit("faltan --version y/o --url")
    manifiesto = firmar_manifiesto(b64d(args.privada), args.version, args.notas, args.url)
    print(json.dumps(manifiesto, indent=2, ensure_ascii=False))


def cmd_probar():
    """Camino completo sin red: generar, firmar, verificar -- y confirmar
    que un manifiesto tocado despues de firmado, o firmado con otra
    clave, no pasa."""
    privada, publica = firma.generar()

    bueno = firmar_manifiesto(
        privada, "1.1.0", "notas de prueba",
        "https://github.com/JuanVelasco03/Nimbo/releases/tag/v1.1.0")
    assert firma.verificar(publica, _crudo_sin_firma(bueno), b64d(bueno["firma"])), \
        "el manifiesto bien firmado no verifico"
    print("  manifiesto bien firmado  -> verifica OK")

    tocado = dict(bueno)
    tocado["version"] = "9.9.9"
    assert not firma.verificar(publica, _crudo_sin_firma(tocado), b64d(tocado["firma"])), \
        "paso un manifiesto tocado despues de firmar"
    print("  manifiesto tocado        -> rechazado")

    otra_privada, _ = firma.generar()
    ajeno = firmar_manifiesto(
        otra_privada, "1.1.0", "notas de prueba",
        "https://github.com/JuanVelasco03/Nimbo/releases/tag/v1.1.0")
    assert not firma.verificar(publica, _crudo_sin_firma(ajeno), b64d(ajeno["firma"])), \
        "paso un manifiesto firmado con OTRA clave"
    print("  firmado con otra clave   -> rechazado")

    sin_firma = dict(bueno)
    del sin_firma["firma"]
    assert not firma.verificar(publica, _crudo_sin_firma(sin_firma), b""), \
        "un manifiesto sin firma no tendria que verificar nunca"
    print("  sin firma                -> rechazado")

    print("\nTODO BIEN")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--generar", action="store_true")
    p.add_argument("--probar", action="store_true")
    p.add_argument("--firmar", action="store_true")
    p.add_argument("--privada", default=os.environ.get("ACTUALIZACIONES_PRIV_D", ""),
                   help="clave privada base64url (o la variable "
                        "ACTUALIZACIONES_PRIV_D, para no dejarla en el "
                        "historial de la consola)")
    p.add_argument("--version", default="")
    p.add_argument("--notas", default="")
    p.add_argument("--url", default="")
    args = p.parse_args(argv)

    if args.generar:
        cmd_generar()
    elif args.probar:
        cmd_probar()
    elif args.firmar:
        cmd_firmar(args)
    else:
        p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
