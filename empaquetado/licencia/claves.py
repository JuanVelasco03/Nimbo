"""Las claves de la licencia y la emision de tokens a mano.

    rem una sola vez en la vida: el par de claves
    python empaquetado\\licencia\\claves.py --generar

    rem probar el camino completo sin backend ni red
    python empaquetado\\licencia\\claves.py --probar

    rem emitir un token a mano (p.ej. para desbloquear sin internet)
    python empaquetado\\licencia\\claves.py --emitir --privada <d> ^
        --clave ABCD-1234 --maquina <MachineGuid> --dias 30

La **publica** va al paquete (`construir.py --clave-publica`) y a la Edge
Function. La **privada** va SOLO a la Edge Function, como variable de
entorno: no va a git, no va al paquete y no se guarda en el repo. Si se
filtra, cualquiera puede emitir licencias y hay que rotar el par (lo que
obliga a reinstalar).

Usa `nimbo\\firma.py`, el mismo modulo que verifica del lado del usuario:
si el token que emite esto no lo acepta aquel, el error salta aca y no en
la maquina del otro.
"""
import argparse
import base64
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "nimbo"))

import firma            # noqa: E402  despues de tocar sys.path
import licencia         # noqa: E402  (solo para id_maquina y leer_token)


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def emitir(privada: bytes, clave: str, maquina: str, dias: float) -> str:
    """El mismo formato que espera licencia.leer_token.

    Se firman los bytes **crudos** del JSON, y es ese mismo texto el que
    viaja: reserializar del otro lado daria otro mensaje y la firma no
    cerraria. `separators` sin espacios por prolijidad, no por necesidad.
    """
    ahora = time.time()
    payload = json.dumps({
        "clave": clave,
        "maquina": maquina,
        "emitido": ahora,
        "exp": ahora + dias * 86400.0,
    }, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return b64e(payload) + "." + b64e(firma.firmar(privada, payload))


def cmd_generar():
    privada, publica = firma.generar()
    print()
    print("  clave PUBLICA  (al paquete y a la funcion):")
    print("    " + b64e(publica))
    print()
    print("  clave PRIVADA  (SOLO a la variable de entorno de la funcion):")
    print("    " + b64e(privada))
    print()
    print("  En Supabase, en la Edge Function:")
    print("    LICENCIA_PRIV_D = " + b64e(privada))
    print("    LICENCIA_PUB_X  = " + b64e(publica))
    print()
    print("  En el build:")
    print("    python empaquetado\\construir.py --clave-publica " + b64e(publica))
    print()
    print("  Guardala en un gestor de contrasenas. No hay forma de")
    print("  recuperarla y perderla obliga a reinstalar en el otro equipo.")


def cmd_emitir(args):
    if not args.privada:
        raise SystemExit("falta --privada")
    maquina = args.maquina or licencia.id_maquina()
    token = emitir(b64d(args.privada), args.clave, maquina, args.dias)
    print(token)


def cmd_instalar(args):
    """Deja el token puesto en estado\\licencia.json de esta instalacion.

    Es el desbloqueo de emergencia: si el backend no responde y hay que
    dejar andando una instalacion ya, esto hace lo mismo que haria
    licencia.activar() pero sin red.
    """
    if not args.privada:
        raise SystemExit("falta --privada")
    import ajustes as ajustes_mod
    maquina = args.maquina or licencia.id_maquina()
    token = emitir(b64d(args.privada), args.clave, maquina, args.dias)
    destino = os.path.join(ajustes_mod.DIR_ESTADO, licencia.NOMBRE_ESTADO)
    os.makedirs(ajustes_mod.DIR_ESTADO, exist_ok=True)
    with open(destino, "w", encoding="utf-8") as f:
        json.dump({"clave": args.clave, "token": token,
                   "revisado": time.time()}, f, indent=2)
        f.write("\n")
    print("escrito:", destino)
    print("vence en %.1f dias" % args.dias)


def cmd_probar():
    """Camino completo sin red: generar, emitir, verificar, y comprobar
    que un token de otra maquina o vencido no pasa."""
    privada, publica = firma.generar()
    maquina = licencia.id_maquina()
    print("maquina de prueba:", maquina)

    # La verificacion del cliente lee la clave publica de
    # licencia._CLAVE_COMPILADA (specs\07-licencia_inviolable.md -- antes
    # era un .json en disco); se la inyectamos en memoria para no escribir
    # nada ni compilar nada.
    licencia._CLAVE_COMPILADA = b64e(publica)

    bueno = emitir(privada, "CLAVE-DE-PRUEBA", maquina, 7)
    payload = licencia.leer_token(bueno)
    assert payload, "el cliente rechazo un token valido"
    assert licencia._vigente(payload), "el token valido no quedo vigente"
    print("  token valido            -> aceptado, vence en %.1f dias"
          % ((payload["exp"] - time.time()) / 86400))

    ajeno = emitir(privada, "CLAVE-DE-PRUEBA", "otra-maquina-1234", 7)
    assert not licencia._vigente(licencia.leer_token(ajeno)), \
        "paso un token de OTRA maquina"
    print("  token de otra maquina   -> rechazado")

    vencido = emitir(privada, "CLAVE-DE-PRUEBA", maquina, -1)
    assert not licencia._vigente(licencia.leer_token(vencido)), \
        "paso un token vencido"
    print("  token vencido           -> rechazado")

    otra_privada, _ = firma.generar()
    falso = emitir(otra_privada, "CLAVE-DE-PRUEBA", maquina, 7)
    assert not licencia.leer_token(falso), "paso un token firmado por otro"
    print("  firmado con otra clave  -> rechazado")

    partes = bueno.split(".")
    tocado = partes[0][:-2] + ("aa" if not partes[0].endswith("aa") else "bb")
    assert not licencia.leer_token(tocado + "." + partes[1]), \
        "paso un payload manipulado"
    print("  payload manipulado      -> rechazado")

    # activa() ya no depende de la clave publica sino de si el proceso
    # esta compilado (specs\07-licencia_inviolable.md): la copia de
    # desarrollo (este script) nunca lo esta.
    licencia._DESARROLLO = True
    assert not licencia.activa(), \
        "activa() tendria que ser False en la copia de desarrollo"
    print("  copia de desarrollo     -> licencia apagada (activa() == False)")

    # Simular un build compilado (sin tocar __compiled__ de verdad, que
    # Nuitka pone solo): el default invertido exige licencia salvo marca
    # de desarrollo, y falla cerrado si la clave quedo vacia (build roto).
    licencia.__dict__["__compiled__"] = True
    try:
        licencia._DESARROLLO = False
        licencia._CLAVE_COMPILADA = ""
        try:
            licencia.exigir(tempfile.mkdtemp())
            raise AssertionError("exigir() no levanto con la clave vacia")
        except licencia.SinLicencia as e:
            print("  compilado sin clave     -> exigir() levanta (%s)"
                  % e.motivo)

        licencia._CLAVE_COMPILADA = b64e(publica)
        try:
            licencia.exigir(tempfile.mkdtemp())
            raise AssertionError("exigir() no levanto sin activar")
        except licencia.SinLicencia as e:
            print("  compilado sin activar   -> exigir() levanta (%s)"
                  % e.motivo)
    finally:
        del licencia.__dict__["__compiled__"]

    print("\nTODO BIEN")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--generar", action="store_true")
    p.add_argument("--probar", action="store_true")
    p.add_argument("--emitir", action="store_true")
    p.add_argument("--instalar", action="store_true",
                   help="emitir y dejarlo puesto en esta instalacion")
    p.add_argument("--privada", default=os.environ.get("LICENCIA_PRIV_D", ""),
                   help="clave privada base64url (o la variable "
                        "LICENCIA_PRIV_D, para no dejarla en el historial "
                        "de la consola)")
    p.add_argument("--clave", default="CLAVE-MANUAL")
    p.add_argument("--maquina", default="")
    p.add_argument("--dias", type=float, default=30.0)
    args = p.parse_args(argv)

    if args.generar:
        cmd_generar()
    elif args.probar:
        cmd_probar()
    elif args.instalar:
        cmd_instalar(args)
    elif args.emitir:
        cmd_emitir(args)
    else:
        p.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
