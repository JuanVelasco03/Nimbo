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
        """Snapshot completo: seleccion activa, @vpn=, @chaturbate= (como
        "sitios": {nombre.lower(): sitio}) y cada directiva vista (con su
        clave ya canonicalizada)."""
        lineas, _ = self._leer_lineas()
        seleccion, vpn, chaturbate = [], set(), set()
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
                elif clave == "chaturbate":
                    chaturbate |= {v.strip().lower() for v in valor.split(",")
                                  if v.strip()}
                else:
                    directivas[clave] = valor
                continue
            seleccion.append(l)
        sitios = {nombre.lower(): ("chaturbate" if nombre.lower() in chaturbate
                                    else "stripchat") for nombre in seleccion}
        return {"seleccion": seleccion, "vpn": sorted(vpn),
                "sitios": sitios, "directivas": directivas}

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

    def _desmarcar_chaturbate(self, lineas, nombre_lower: str) -> bool:
        """Saca 'nombre' de @chaturbate= EN LA LISTA QUE SE LE PASA, sin
        escribir nada. -> True si la linea cambio.

        Existe para que agregar/quitar puedan hacer el pop/insert de la
        linea suelta y este discard en UNA sola escritura: dos llamadas
        sueltas (quitar_modelo() y despues marcar_sitio()) son dos
        reescrituras de un archivo que el detector, el supervisor, el
        directo y chaturbate releen cada 1-2 s, o sea una ventana real en
        la que se ve el estado intermedio.

        Si la directiva no existia, NO se crea vacia solo para desmarcar
        (marcar_sitio() si la crea, porque ahi hay algo que marcar): una
        @chaturbate= que aparece de la nada en un grabar.txt que nunca la
        tuvo es ruido en un archivo que el usuario edita a mano.
        """
        idx_cb, m = self._indice_de_directiva(lineas, "chaturbate")
        if idx_cb is None or m is None:
            return False
        actuales = {v.strip().lower() for v in m.group(3).split(",") if v.strip()}
        if nombre_lower not in actuales:
            return False
        actuales.discard(nombre_lower)
        lineas[idx_cb] = "@chaturbate=" + ",".join(sorted(actuales))
        return True

    def agregar_modelo(self, nombre: str, sitio: str = "") -> bool:
        """True si lo agrego; False si ya estaba (idempotente).

        sitio == "stripchat" saca el nombre de @chaturbate= en la misma
        escritura: cierra el otro lado del bug de la marca huerfana -- una
        que ya este en el archivo (de antes de SPEC 20, o puesta a mano) no
        se hereda al re-agregar el nombre pensando en stripchat, que es
        como se grababa contra el sitio equivocado en silencio.

        sitio == "" (el default) no toca NINGUNA directiva, o sea el
        comportamiento byte a byte de antes de este spec. Eso es lo que
        deja intacto a vivo_temporal.py, que llama a esto sin ningun
        concepto de sitio y cuyo invariante es no tocar lo que no es suyo:
        con el default "stripchat", una previa temporal habria desmarcado
        @chaturbate= al abrirse.
        """
        nombre = nombre.strip()
        if not RE_NOMBRE.match(nombre):
            raise ValueError("nombre invalido: %r" % nombre)
        limpiar_cb = sitio.strip().lower() == "stripchat"
        with self._lock:
            lineas, fin = self._leer_lineas()
            ya_estaba = self._indice_de_nombre(lineas, nombre.lower()) is not None
            cambio = False
            if limpiar_cb:
                cambio = self._desmarcar_chaturbate(lineas, nombre.lower())
            if ya_estaba:
                # Idempotente para el nombre, pero si habia una marca
                # huerfana igual hay que escribir: es justo el caso que
                # este spec arregla.
                if cambio:
                    self._escribir(lineas, fin)
                return False
            lineas.insert(self._indice_primera_directiva(lineas), nombre)
            self._escribir(lineas, fin)
            return True

    def quitar_modelo(self, nombre: str, sitio: str = "") -> bool:
        """True si lo quito; False si no estaba.

        sitio == "chaturbate" saca el nombre de @chaturbate= en la MISMA
        escritura que el pop de la linea suelta (ver
        _desmarcar_chaturbate). Sin eso, el nombre quedaba solo en la
        directiva y era INVISIBLE en toda la interfaz -- leer() arma
        "sitios" iterando "seleccion" --, pero seguia en el archivo: al
        re-agregarlo desde Modelos pensando en stripchat reaparecia marcado
        y se grababa contra el sitio equivocado, en silencio.

        No toca @vpn=, y eso NO es un descuido sino otro criterio: una
        @vpn= huerfana rutea por el tunel sin que se haya pedido (molesto,
        pero no es grabacion doble ni es invisible -- el propio
        detector/supervisor avisan solos que la marca no sirve sin la linea
        suelta). Cambiarlo es su propia decision, ver SPEC 20 ("No entra").

        sitio == "" (el default) no toca ninguna directiva: el
        comportamiento de antes de este spec, que es lo que deja intacto a
        vivo_temporal.py (ver agregar_modelo).
        """
        limpiar_cb = sitio.strip().lower() == "chaturbate"
        nombre_l = nombre.strip().lower()
        with self._lock:
            lineas, fin = self._leer_lineas()
            i = self._indice_de_nombre(lineas, nombre_l)
            if i is None:
                return False
            lineas.pop(i)
            if limpiar_cb:
                self._desmarcar_chaturbate(lineas, nombre_l)
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

    def marcar_sitio(self, nombre: str, sitio: str) -> None:
        """Pone o saca a 'nombre' de @chaturbate=, calco de marcar_vpn().
        sitio == "chaturbate" lo suma al set; cualquier otro valor
        (normalmente "stripchat") lo saca. Si se marca chaturbate y el
        nombre todavia no es una linea suelta, tambien se agrega -- sin la
        linea de seleccion no graba nada, igual que con @vpn=."""
        nombre = nombre.strip()
        nombre_l = nombre.lower()
        activo = sitio.strip().lower() == "chaturbate"
        with self._lock:
            lineas, fin = self._leer_lineas()
            idx_cb, m = self._indice_de_directiva(lineas, "chaturbate")
            actuales = ({v.strip().lower() for v in m.group(3).split(",")
                        if v.strip()} if m else set())

            if activo:
                actuales.add(nombre_l)
            else:
                actuales.discard(nombre_l)
            nueva = "@chaturbate=" + ",".join(sorted(actuales))

            if idx_cb is not None:
                lineas[idx_cb] = nueva
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
