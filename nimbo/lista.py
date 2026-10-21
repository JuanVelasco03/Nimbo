"""Lee y escribe grabar.txt sin perder nada de lo que el usuario ya tenia.

grabar.txt es un archivo documentado que el usuario puede seguir editando a
mano: comentarios explicativos, una lista de nombres sugeridos comentada
(las "favoritas" de antes de que existiera Nimbo) y 14 directivas @ de
las que el detector entiende todas y el supervisor solo @vpn=. Por eso esto
NO regenera el archivo desde una plantilla: edita in-place, linea por
linea, y conserva byte a byte todo lo que no toca.

Dos tipos de linea que Nimbo edita:
  - "seleccion": una linea suelta, sin # ni @ al principio -- un nombre de
    modelo, un ID numerico, o '*'/'todo'. Son las que arrancan y paran la
    grabacion. Si no hay ninguna todavia, una nueva se inserta justo antes
    del primer "@directiva" del archivo: es exactamente donde las deja la
    plantilla que el propio detector escribe si grabar.txt no existe
    (ver detectar_m3u8.py, dentro de Detector.__init__, el bloque que
    escribe "# Agregar linea = empieza a grabar..." seguido de una linea
    en blanco y recien despues las @directivas).
  - "@clave=valor": se edita EN SU LINEA, sin moverla, para que el
    comentario de arriba siga teniendo sentido. Si la clave no existe
    todavia (un usuario que borro la linea a mano), se agrega al final.

Nada de esto corre dentro de mitmdump ni del supervisor: es la capa de
Nimbo que escribe el archivo que ESOS DOS leen solos (el detector cada
2s, el supervisor cada 5s). Ninguno de los dos lo escribe nunca -- por eso
Nimbo puede ser su dueno exclusivo sin pelearse con nadie.
"""
import os
import re
import threading

# Mismo patron que usan el detector (RE_NOMBRE_VALIDO) y el supervisor
# (RE_NOMBRE): 2 a 40 caracteres, sin espacios.
RE_NOMBRE = re.compile(r"^[A-Za-z0-9_.\-]{2,40}$")
RE_DIRECTIVA = re.compile(r"^(\s*)@([A-Za-z_]+)\s*=\s*(.*)$")

# Alias -> clave canonica (tabla de detectar_m3u8.py, Seleccion._parsear).
# Se reconocen al LEER, por si el archivo trae un alias viejo; Nimbo
# siempre ESCRIBE con la clave canonica, asi nunca hay dos lineas para lo
# mismo compitiendo entre si.
ALIAS = {
    "forzar_calidad": "forzar",
    "privados": "privado",
    "minimo": "privado",
    "all": "todo",
}


class Lista:
    """Una instancia por grabar.txt. No cachea nada entre llamadas: cada
    metodo relee el archivo, porque el usuario puede editarlo a mano en
    cualquier momento y el archivo es la unica fuente de verdad."""

    def __init__(self, ruta: str):
        self.ruta = ruta
        self._lock = threading.Lock()

    # -- lectura de bajo nivel ----------------------------------------

    def _leer_lineas(self):
        if not os.path.isfile(self.ruta):
            return [], "\n"
        with open(self.ruta, "rb") as f:
            datos = f.read()
        # mismo criterio que reducir_master() en el detector: se respeta
        # el fin de linea que el archivo ya tenia, no se le impone uno.
        fin = "\r\n" if b"\r\n" in datos else "\n"
        texto = datos.decode("utf-8", errors="replace")
        lineas = texto.split(fin)
        if lineas and lineas[-1] == "":
            lineas.pop()          # no perder/duplicar el salto final
        return lineas, fin

    def firma(self):
        """(mtime, size) o None si el archivo no existe -- para que quien
        use esto sepa si vale la pena releer sin tener que parsear nada."""
        try:
            st = os.stat(self.ruta)
            return (st.st_mtime, st.st_size)
        except OSError:
            return None

    # -- lectura de alto nivel ------------------------------------------

    def leer(self) -> dict:
        """Snapshot completo: seleccion activa, @vpn=, y cada directiva
        vista (con su clave ya canonicalizada)."""
        lineas, _ = self._leer_lineas()
        seleccion, vpn = [], set()
        directivas = {}
        for cruda in lineas:
            sin_com = cruda.split("#", 1)[0]
            l = sin_com.strip()
            if not l:
                continue
            if l.startswith("@"):
                m = RE_DIRECTIVA.match(sin_com)
                if not m:
                    continue
                clave = ALIAS.get(m.group(2).strip().lower(),
                                  m.group(2).strip().lower())
                valor = m.group(3).strip()
                if clave == "vpn":
                    vpn |= {v.strip().lower() for v in valor.split(",")
                           if v.strip()}
                else:
                    directivas[clave] = valor
                continue
            seleccion.append(l)
        return {"seleccion": seleccion, "vpn": sorted(vpn),
                "directivas": directivas}

    # -- escritura de bajo nivel -----------------------------------------

    def _escribir(self, lineas, fin):
        os.makedirs(os.path.dirname(self.ruta) or ".", exist_ok=True)
        tmp = self.ruta + ".part"
        texto = fin.join(lineas) + fin
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(texto)
        os.replace(tmp, self.ruta)     # nunca se lee un archivo a medias

    def _indice_primera_directiva(self, lineas) -> int:
        for i, cruda in enumerate(lineas):
            if cruda.split("#", 1)[0].strip().startswith("@"):
                return i
        return len(lineas)

    def _lineas_seleccion(self, lineas):
        """Indices de todas las lineas de seleccion (nombre/ID/'*')."""
        idx = []
        for i, cruda in enumerate(lineas):
            l = cruda.split("#", 1)[0].strip()
            if l and not l.startswith("@"):
                idx.append(i)
        return idx

    def _indice_de_nombre(self, lineas, nombre_lower: str):
        for i in self._lineas_seleccion(lineas):
            if lineas[i].split("#", 1)[0].strip().lower() == nombre_lower:
                return i
        return None

    def _indice_de_directiva(self, lineas, clave: str):
        for i, cruda in enumerate(lineas):
            m = RE_DIRECTIVA.match(cruda.split("#", 1)[0])
            if m:
                c = ALIAS.get(m.group(2).strip().lower(),
                              m.group(2).strip().lower())
                if c == clave:
                    return i, m
        return None, None

    # -- escritura de alto nivel ------------------------------------------

    def agregar_modelo(self, nombre: str) -> bool:
        """True si lo agrego; False si ya estaba (idempotente)."""
        nombre = nombre.strip()
        if not RE_NOMBRE.match(nombre):
            raise ValueError("nombre invalido: %r" % nombre)
        with self._lock:
            lineas, fin = self._leer_lineas()
            if self._indice_de_nombre(lineas, nombre.lower()) is not None:
                return False
            lineas.insert(self._indice_primera_directiva(lineas), nombre)
            self._escribir(lineas, fin)
            return True

    def quitar_modelo(self, nombre: str) -> bool:
        """True si lo quito; False si no estaba. No toca @vpn= -- si
        estaba marcada y se vuelve a agregar sin querer, el propio
        detector/supervisor ya avisan solos que la marca no sirve sin la
        linea suelta, asi que no hace falta duplicar ese aviso aqui."""
        with self._lock:
            lineas, fin = self._leer_lineas()
            i = self._indice_de_nombre(lineas, nombre.strip().lower())
            if i is None:
                return False
            lineas.pop(i)
            self._escribir(lineas, fin)
            return True

    def marcar_vpn(self, nombre: str, activo: bool) -> None:
        """Pone o saca a 'nombre' de @vpn=. Si se activa y el nombre
        todavia no es una linea suelta, tambien se agrega: marcarla sola
        sin la linea de seleccion no graba nada (es el error que el
        supervisor avisa por su cuenta)."""
        nombre = nombre.strip()
        nombre_l = nombre.lower()
        with self._lock:
            lineas, fin = self._leer_lineas()
            idx_vpn, m = self._indice_de_directiva(lineas, "vpn")
            actuales = ({v.strip().lower() for v in m.group(3).split(",")
                        if v.strip()} if m else set())

            if activo:
                actuales.add(nombre_l)
            else:
                actuales.discard(nombre_l)
            nueva = "@vpn=" + ",".join(sorted(actuales))

            if idx_vpn is not None:
                lineas[idx_vpn] = nueva
            else:
                lineas.insert(self._indice_primera_directiva(lineas), nueva)

            if activo and self._indice_de_nombre(lineas, nombre_l) is None:
                lineas.insert(self._indice_primera_directiva(lineas), nombre)

            self._escribir(lineas, fin)

    def establecer_directiva(self, clave: str, valor: str) -> None:
        """Edita (o agrega) una @clave=valor. Para @vpn= usar marcar_vpn():
        esa es una lista que se suma/resta, no un valor que se reemplaza."""
        clave = ALIAS.get(clave.strip().lower(), clave.strip().lower())
        if clave == "vpn":
            raise ValueError("@vpn= se edita con marcar_vpn(), no con esto")
        with self._lock:
            lineas, fin = self._leer_lineas()
            nueva_base = "@%s=%s" % (clave, valor)
            i, _ = self._indice_de_directiva(lineas, clave)
            if i is not None:
                sin_com = lineas[i].split("#", 1)[0]
                resto = lineas[i][len(sin_com):]   # conserva un "# nota" al final, si habia
                lineas[i] = nueva_base + ("  " + resto if resto else "")
            else:
                lineas.append(nueva_base)          # no existia: al final
            self._escribir(lineas, fin)
