# SPEC 17 — Acceso LAN, aviso de actualización y atajos de teclado

> **Estado:** Implementado
> **Depende de:** SPEC 01 (deltas de estado por WebSocket), SPEC 16 (franja de
> salud, donde se integra el aviso de actualización)
> **Fecha:** 2026-11-07
> **Objetivo:** Agregar acceso remoto por LAN con login y cookie de sesión,
> un chequeo de actualización contra un manifiesto firmado en GitHub, y
> atajos de teclado globales con paleta de comandos y ayuda — las
> recomendaciones C10, C12 y C13 de `recomendaciones/recomendaciones.md`.

---

## Por qué

Las tres vienen del mismo documento de recomendaciones (grupo "Operación"),
quedan juntas en un solo spec por pedido explícito, y no comparten código
entre sí — cada una se puede leer y verificar por separado:

**C10.** `servidor.py` escucha solo en `127.0.0.1` y exige `?t=<token>`
nuevo por arranque en `/`, `/api/*` y el handshake de `/ws`
(`servidor.py:1511`, `nimbo\app.py:234`). Cero forma de abrir la interfaz
desde el celular sin estar en la misma máquina. `nimbo\web\js\api.js:8` lee
el token una sola vez de `location.search` — ese dato acota el diseño: la
pieza nueva no tiene que reemplazar esa lectura, alcanza con dársela al
celular una vez.

**C12.** La versión vive escrita a mano en `empaquetado\nimbo.iss:13`
(`#define Version "1.0.0"`) y en ningún otro lado; `paquete.json`
(`empaquetado\construir.py:873`) guarda `armado`, `version_python`,
`compilado`, etc., pero ninguna versión de la app. No hay chequeo de
actualización en ningún lado: quien recibe un instalador queda clavado en
esa build. El repo es público en GitHub
(`github.com/JuanVelasco03/Nimbo`), así que alojar un manifiesto no exige
infraestructura nueva.

**C13.** Hoy hay `keydown` en tres lugares aislados
(`vista_grabaciones.js:432`, `vista_modelos.js:760`, `vista_previa.js:475`),
cada uno local a su propia vista. Falta lo global: cambiar de pestaña,
arrancar/parar el motor, abrir la previa de la primera en vivo, y una
paleta de comandos.

---

## Scope

**Entra:**

- **C10 — acceso LAN:**
  - Toggle `acceso_lan` en Ajustes (apagado por defecto, nunca implícito).
  - `puerto_lan` fijo y configurable en `config.json`: con `acceso_lan`
    prendido, el servidor deja de pedir un puerto al azar (`puerto=0`,
    `servidor.py:1511`) y usa siempre ese número, para que el QR y
    cualquier marcador del celular sigan sirviendo después de reiniciar
    Nimbo.
  - Contraseña única (no por usuario), guardada como hash PBKDF2-HMAC-SHA256
    + sal — nunca en claro, nunca reversible.
  - Página de login mínima servida cuando llega un `GET /` sin `?t=` válido
    y sin cookie de sesión LAN.
  - `POST /api/lan/login`: verifica la contraseña, en éxito pone una cookie
    HMAC propia (30 días) y redirige a `/`.
  - `GET /` sin `?t=` pero con cookie de sesión válida → **302** a
    `/?t=<token real>`. De ahí en más el celular usa el mismo `api.js` y el
    mismo handshake de `/ws` que la ventana de escritorio — no se toca
    `_token_valido` ni ninguna ruta `/api/*` existente.
  - `_origen_valido` (`servidor.py:820`) acepta además
    `http://<ip-lan>:<puerto_lan>` cuando `acceso_lan` está prendido.
  - Bloqueo de fuerza bruta: 5 intentos fallidos de contraseña por IP
    bloquean esa IP 60 segundos, contador en memoria (se pierde al
    reiniciar, que es aceptable).
  - QR en Ajustes con un generador propio en stdlib (sin dependencia nueva),
    mostrando `http://<ip-lan>:<puerto_lan>/`.
  - Botón "Cerrar todas las sesiones" en Ajustes: rota `lan_cookie_clave`,
    lo que invalida de una cualquier cookie emitida antes.
  - Panel "Acceso remoto" en Ajustes: toggle, poner/cambiar contraseña, QR,
    botón de cierre de sesiones, aviso de riesgo (sin TLS, red local).

- **C12 — versión y aviso de actualización:**
  - `nimbo\version.py` con `VERSION = "x.y.z"` como fuente única.
  - `empaquetado\nimbo.iss` y `empaquetado\construir.py` (`paquete.json`)
    toman la versión de ahí en vez de tenerla escrita a mano.
  - Par de claves Ed25519 nuevo y separado del de licencia:
    `empaquetado\actualizaciones\claves.py`, mismo patrón que
    `empaquetado\licencia\claves.py` (usa `nimbo\firma.py`, `--generar` y
    `--probar`).
  - `nimbo\actualizaciones.py`: baja `manifest.json` desde
    `raw.githubusercontent.com/JuanVelasco03/Nimbo/main/releases/manifest.json`,
    verifica la firma con la clave pública embebida y compara con
    `version.VERSION`.
  - Chequeo al arrancar Nimbo y cada 24 horas, atado a un toggle
    `buscar_actualizaciones` (prendido por defecto, pero visible y
    apagable — Nimbo no tiene por qué salir a internet sin que quede claro).
  - Con `buscar_actualizaciones` apagado, `actualizaciones.py` no hace
    ningún pedido de red.
  - El aviso se integra como una alerta más de la franja de salud de SPEC
    16 (`estado["salud"]`, tipo `"actualizacion"`, nivel `"aviso"`) y en un
    panel "Actualizaciones" en Ajustes (versión actual, última
    verificación, botón "buscar ahora").
  - Enterarse alcanza para este spec: el aviso linkea a la página de
    releases de GitHub, no descarga ni instala nada.

- **C13 — atajos de teclado globales:**
  - Listener de `keydown` global en `main.js`, con guarda: si
    `document.activeElement` es `INPUT`, `TEXTAREA` o
    `[contenteditable]`, no intercepta nada (mismo criterio que ya usa
    `vista_ajustes.js:renderDirectivas` para no robarle el foco a un campo
    con el cursor adentro).
  - Atajos: `1`-`5` cambian de pestaña (Grabando/Modelos/Grabaciones/
    Siguiendo/Ajustes, en ese orden); `Espacio` alterna
    `/api/motor/arrancar` / `/api/motor/parar` según el estado actual;
    `v` abre el modal de previa de la primera modelo que esté grabando.
  - Paleta de comandos (`nimbo\web\js\paleta.js`, nuevo): `Ctrl+K` abre un
    modal con un campo de texto que filtra una lista fija de acciones
    (las mismas de arriba, más "abrir Ajustes" y "buscar actualizaciones")
    y las ejecuta con `Enter` o click.
  - Modal de ayuda: `?` abre una lista estática de todos los atajos
    disponibles con su descripción; `Escape` la cierra (mismo patrón de
    modal que la paleta).

**Fuera de scope (para specs futuros):**

- C10: sin TLS/HTTPS (el riesgo de contraseña y cookie en claro sobre la
  LAN queda documentado, no resuelto); sin cuentas por usuario (una sola
  contraseña compartida); sin acceso fuera de la LAN (VPN, port-forwarding,
  túneles).
- C12: sin descarga ni instalación automática de la actualización.
- C13: atajos fijos — no configurables por el usuario en esta versión.
- C9 (ventanas horarias y modo juego) y C11 (línea de tiempo y
  estadísticas): no fueron pedidas, quedan afuera.
- D14 (multi-sitio): no tiene relación con estas tres.

---

## Modelo de datos

```python
# nimbo\ajustes.py -- claves nuevas en DEFECTOS
"acceso_lan": False,            # opt-in explicito, nunca por defecto
"puerto_lan": 8787,             # fijo mientras acceso_lan este prendido
"lan_pass_hash": "",            # PBKDF2-HMAC-SHA256, hex
"lan_pass_sal": "",             # sal aleatoria, hex
"lan_cookie_clave": "",         # secreto HMAC para firmar cookies; se
                                 # regenera al activar y al "cerrar sesiones"
"buscar_actualizaciones": True,
```

```text
# Cookie de sesion LAN (Set-Cookie: nimbo_lan=<valor>)
<payload_b64>.<firma_hex>
  payload = {"exp": 1799999999}                 # epoch, +30 dias al emitir
  firma   = HMAC-SHA256(lan_cookie_clave, payload_b64).hexdigest()
```

```python
# nimbo\version.py
VERSION = "1.1.0"
```

```python
# releases/manifest.json (en el repo de GitHub, raiz "main")
{
  "version": "1.1.0",
  "notas": "texto corto para el aviso",
  "url": "https://github.com/JuanVelasco03/Nimbo/releases/tag/v1.1.0",
  "firma": "<firma Ed25519 en base64, sobre el JSON sin la clave 'firma'>"
}
```

```python
# estado["salud"] -- una alerta mas, mismo formato que SPEC 16
{"clave": "actualizacion", "tipo": "actualizacion", "nivel": "aviso",
 "titulo": "Hay una version nueva (1.1.0)",
 "texto": "notas del release -- ver el link en Ajustes"}
```

Nada de esto rompe lo que ya existe: `ajustes.DEFECTOS` sigue siendo claves
planas, `estado["salud"]` sigue siendo la misma lista de SPEC 16 con un tipo
más, y el token `?t=` sigue siendo la única puerta de las rutas `/api/*`.

---

## Plan de implementación

1. **`nimbo\version.py`:** archivo nuevo con `VERSION = "1.0.0"` (la que ya
   tiene `nimbo.iss` hoy). Verificación: `python -c "import sys;
   sys.path.insert(0,'nimbo'); import version; print(version.VERSION)"`.
2. **`empaquetado\nimbo.iss` y `empaquetado\construir.py`:** leer la versión
   de `nimbo\version.py` en vez del `#define Version "1.0.0"` fijo, y
   sumarla a `paquete.json`. Verificación: `construir.py --descargar` deja
   `paquete.json` con la versión correcta.
3. **`nimbo\ajustes.py`:** agregar las seis claves de C10/C12 a `DEFECTOS`.
   Verificación: Nimbo arranca y `config.json` las tiene.
4. **`nimbo\lan.py`** (nuevo, funciones puras): `hashear(pw, sal=None)`,
   `verificar_password(pw, hash, sal)`, `emitir_cookie(clave, dias=30)`,
   `verificar_cookie(clave, valor)`. Verificación: probar a mano con
   `python -i`.
5. **`servidor.py` — login y bind:** `POST /api/lan/configurar` (primera
   vez: setea contraseña + genera `lan_cookie_clave`), `POST
   /api/lan/login` (verifica, pone la cookie), página de login servida
   cuando `GET /` no trae `?t=` válido ni cookie válida, `302` a
   `/?t=<token>` cuando sí hay cookie válida; `_origen_valido` extendida;
   bind a `puerto_lan`/`"0.0.0.0"` en vez de `0`/`"127.0.0.1"` cuando
   `acceso_lan` está prendido (toma efecto en el próximo arranque de
   Nimbo, no en caliente). Verificación: con `acceso_lan` apagado,
   `/api/lista` responde exactamente igual que hoy.
6. **Fuerza bruta:** contador en memoria por IP en `Aplicacion`, 5 fallos →
   60s de bloqueo antes de comparar la contraseña. Verificación: el sexto
   intento seguido con contraseña mala da 403 sin tocar `verificar_password`.
7. **`nimbo\qr.py`** (nuevo, stdlib puro): matriz QR mínima + encoder PNG
   crudo (sin Pillow); endpoint `GET /api/lan/qr` devuelve `image/png` con
   la URL actual. Verificación: un lector de QR real decodifica la URL
   correcta.
8. **Panel "Acceso remoto" en Ajustes:** toggle, campo de contraseña, `<img>`
   con el QR, botón "Cerrar todas las sesiones" (`POST
   /api/lan/cerrar_sesiones`, rota `lan_cookie_clave`). Verificación:
   activar, reiniciar Nimbo, entrar desde un celular en la misma red.
9. **`empaquetado\actualizaciones\claves.py`** (nuevo, calco de
   `empaquetado\licencia\claves.py`): `--generar` / `--probar` para el par
   Ed25519 de actualizaciones. Verificación: `--probar` firma un manifiesto
   de prueba y lo verifica con `nimbo\firma.py`.
10. **`nimbo\actualizaciones.py`:** `verificar(dir_estado) -> dict | None`,
    baja el `manifest.json`, valida firma y compara versión; cachea en RAM
    con marca de tiempo; no hace nada si `buscar_actualizaciones` está
    apagado. Verificación: apuntar a un manifiesto de prueba con versión
    mayor y confirmar que detecta la actualización; con el toggle apagado,
    cero pedidos de red.
11. **`servidor.py` — bucle:** `_bucle_actualizaciones` (al arrancar + cada
    24h) que llama a `actualizaciones.verificar` en el ejecutor y agrega la
    alerta `"actualizacion"` a `estado["salud"]` si corresponde; `GET
    /api/actualizacion` para el panel de Ajustes. Verificación: con el
    manifiesto de prueba, la franja de salud muestra el aviso y el link.
12. **Panel "Actualizaciones" en Ajustes:** versión actual
    (`version.VERSION`), última verificación, botón "buscar ahora", toggle
    `buscar_actualizaciones`. Verificación: el toggle sobrevive a recargar
    la ventana.
13. **`main.js` — atajos globales:** listener de `keydown` con el guard de
    foco; `1`-`5`, `Espacio`, `v`. Verificación manual en el navegador: cada
    tecla dispara su acción y ninguna interfiere con escribir en el
    buscador de Modelos.
14. **`nimbo\web\js\paleta.js`** (nuevo): modal de `Ctrl+K` con filtro de
    texto sobre la lista fija de acciones. Verificación: escribir "parar"
    filtra a una sola acción y `Enter` la ejecuta.
15. **Modal de ayuda (`?`):** lista estática de atajos, mismo patrón de
    modal que la paleta. Verificación: `?` abre, `Escape` cierra.

---

## Criterios de aceptación

- [ ] Con `acceso_lan` apagado (default), el servidor se comporta
      exactamente igual que hoy: bind en `127.0.0.1`, puerto al azar, auth
      solo por `?t=`.
- [ ] Con `acceso_lan` prendido, contraseña configurada y Nimbo reiniciado,
      desde un celular en la misma red se puede entrar a
      `http://<ip-lan>:<puerto_lan>/`, loguearse y usar la interfaz
      (listas, WebSocket conectado) sin pegar ningún token a mano.
- [ ] 5 intentos de contraseña incorrecta seguidos bloquean esa IP 60
      segundos; el sexto intento inmediato es rechazado sin comparar la
      contraseña.
- [ ] El QR de Ajustes, leído con la cámara de un celular, abre la URL
      correcta.
- [ ] Una cookie de sesión vencida (o después de "Cerrar todas las
      sesiones") vuelve a pedir la contraseña.
- [ ] Reiniciar Nimbo con `acceso_lan` prendido no cambia el puerto: el QR
      guardado de antes sigue sirviendo.
- [ ] Con `buscar_actualizaciones` apagado, `actualizaciones.verificar` no
      dispara ningún pedido de red (verificable revisando que la función
      corta antes del `urlopen`).
- [ ] Con un `manifest.json` de prueba firmado y versión mayor a
      `version.VERSION`, la franja de salud y el panel de Ajustes muestran
      el aviso con el link al release.
- [ ] Un `manifest.json` con firma inválida (o sin firma) no dispara ningún
      aviso.
- [ ] Las teclas `1` a `5` cambian de pestaña, salvo que el foco esté en un
      campo de texto, en cuyo caso escriben el número.
- [ ] `Ctrl+K` abre la paleta de comandos, el filtro de texto funciona y
      `Enter` ejecuta la acción resaltada.
- [ ] `?` abre el modal de ayuda con la lista completa de atajos y `Escape`
      lo cierra.
- [ ] `python nimbo\servidor.py` arranca sin traceback tanto con
      `acceso_lan` prendido como apagado.

---

## Decisiones

- **Sí:** la cookie LAN **desbloquea conocer el token**, no lo reemplaza.
  `api.js` sigue leyendo `?t=` de la URL como siempre; solo cambia cómo el
  celular consigue esa URL la primera vez. Evita tocar `_token_valido`, el
  framing de `/ws` o cualquier ruta `/api/*` existente.
- **No:** un token persistente plano en vez de cookie. Un token fijo que
  nunca rota y queda pegado en el historial del navegador del celular es
  peor superficie que una cookie HMAC que expira y se puede revocar entera
  rotando `lan_cookie_clave`.
- **Sí:** contraseña con PBKDF2 + sal, nunca en claro ni reversible — no
  hace falta mostrarla de nuevo, solo compararla.
- **No:** DPAPI para la contraseña LAN (como `sesion.json`). DPAPI es para
  secretos que hay que leer después (la sesión del sitio); acá alcanza con
  poder comparar, nunca con recuperar.
- **Sí:** `puerto_lan` fijo y separado del puerto de loopback (que sigue al
  azar). Sin esto, el QR y cualquier marcador del celular se rompen en cada
  reinicio de Nimbo — detectado explorando `app.py:244` y
  `servidor.py:1511` antes de escribir este spec.
- **Sí:** el bind no cambia en caliente — hace falta reiniciar Nimbo para
  que `acceso_lan` tome efecto. Rehacer el `asyncio.start_server` ya vivo
  es más riesgo que pedirle un reinicio al usuario, que ya es la norma para
  otros cambios de Ajustes que tocan el servidor.
- **Sí:** par de claves Ed25519 separado para actualizaciones
  (`empaquetado\actualizaciones\claves.py`), mismo código de `firma.py`
  pero otra clave privada. Si una se filtra, no compromete la otra.
- **Sí:** el manifiesto vive en el propio repo público de GitHub
  (`raw.githubusercontent.com`). Cero infraestructura nueva — el repo ya
  existe y ya es público (confirmado con `gh repo view`).
- **No:** descarga o instalación automática de la actualización. Enterarse
  ya vale, como dice el propio documento de recomendaciones; instalar es
  trabajo de otro spec.
- **Sí:** `buscar_actualizaciones` como toggle visible y apagable, prendido
  por defecto. Nimbo graba contenido de un sitio de terceros; que salga a
  internet por su cuenta sin que quede a la vista es justo el tipo de cosa
  que este repo evita en otros lados (ver "Lo que no conviene hacer" en
  `recomendaciones.md`).
- **Sí:** atajos de teclado fijos (no configurables) para esta primera
  versión. Menos superficie; si hace falta reasignarlos, es un spec propio.
- **No:** dividir esto en tres specs separados pese a tocar áreas distintas
  del sistema (seguridad del servidor, empaquetado/firma, frontend). Pedido
  explícito del usuario tras confirmar que se refería a C10, C12 y C13 del
  documento de recomendaciones.

---

## Riesgos

| Riesgo | Mitigación |
| --- | --- |
| El acceso LAN va sin TLS: contraseña y cookie viajan en claro dentro de la red local | Alcance limitado a LAN confiable, advertencia explícita en el panel de Ajustes; TLS queda para un spec futuro si hace falta |
| Pérdida o robo del celular con la cookie guardada | La cookie expira a los 30 días; botón "Cerrar todas las sesiones" rota `lan_cookie_clave` e invalida todas de una |
| GitHub caído, sin red, o rate-limit en `raw.githubusercontent.com` | Degrada en silencio, igual que `licencia.py`: sin respuesta válida, no hay aviso y Nimbo sigue andando |
| Atajos globales interfieren con campos de texto ya existentes (buscador de Modelos, etc.) | Guard por `document.activeElement` antes de interceptar cualquier tecla, mismo criterio que `vista_ajustes.js:renderDirectivas` |
| El toggle `acceso_lan` queda prendido sin que el usuario se acuerde | Estado siempre visible en el panel de Ajustes, nunca oculto ni en una sub-pantalla |
| `puerto_lan` elegido choca con otro proceso de la máquina | El bind falla al arrancar con un error claro en el log; el usuario cambia el número en Ajustes y reinicia |

---

## Lo que **no** está en este spec

- TLS/HTTPS para el acceso LAN.
- Cuentas por usuario o múltiples contraseñas.
- Acceso fuera de la LAN (VPN, port-forwarding, túneles).
- Descarga o instalación automática de actualizaciones.
- Atajos de teclado configurables por el usuario.
- C9 (ventanas horarias y modo juego) y C11 (línea de tiempo y
  estadísticas).

Cada una de esas, si llega, va en su propio spec.
