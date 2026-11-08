# Recomendaciones — ideas duras para llevar Nimbo más allá

> **Estado:** Propuesta — nada de esto está implementado
> **Fecha:** 2026-11-07
> **Base:** repo en `90dc996`, instalación real (`config.json` con
> `dir_segmentos` en `C:\Nimbo\streams`, `dir_grabaciones` en
> `F:\Nimbo\datos\grabaciones`, `dias_segmentos: 0.25`, `@directo=1`)
> **Objetivo:** dejar anotado qué falta, verificado contra el código, para
> poder elegir el próximo spec sin volver a investigar.

---

## Cómo leer esto

Cada punto dice **qué pasa hoy** (con archivo y línea, verificado, no de
memoria), **qué agregar**, **dónde toca** y **cuánto cuesta**. El orden es
por valor sobre costo, no por lo vistoso.

Lo que domina la lista: Nimbo ya graba bien, pero **cuando algo deja de
grabar no hay nadie que avise**. Tres de los cuatro primeros puntos son
eso, y los tres reusan código que ya está escrito.

| # | Qué | Valor | Costo |
|---|---|---|---|
| C10 | Acceso desde el celular en la LAN | alto | medio-alto |
| C11 | Línea de tiempo y estadísticas | medio | medio |
| C12 | Versión del paquete y aviso de actualización | medio | medio |
| C13 | Atajos de teclado | bajo | bajo |
| D14 | Multi-sitio (chaturbate, cam4, …) | alto | muy alto |

---

## C. Operación

### C10 — Acceso desde el celular en la LAN

La que más se nota, y la más delicada. Hoy `servidor.py` escucha solo en
`127.0.0.1` y exige `?t=<token>` nuevo por arranque en `/`, `/api/*` y el
handshake de `/ws`.

Para salir a la LAN hacen falta, no negociables: opt-in explícito (jamás
por defecto), contraseña obligatoria, token persistente en vez de uno por
arranque, y un QR en Ajustes para no tipear. Tener en cuenta que los
estáticos hoy van **sin** token a propósito (los imports del navegador son
URLs relativas), así que el modelo de autenticación hay que pensarlo
entero, no parchear el `bind`.

**Costo.** Medio-alto, casi todo en seguridad.

### C12 — Versión del paquete y aviso de actualización

**Hoy** la versión está escrita a mano en un solo lugar y nadie la
consulta: `empaquetado\nimbo.iss:13` tiene `#define Version "1.0.0"`, y
`paquete.json` guarda `armado`, `version_python`, `completo`, `licencia`,
`compilado` y `js_minificado` — **ninguna versión de la app**. No hay
chequeo de actualización en ningún lado: el que recibe el instalador queda
clavado en esa build para siempre.

**Agregar.** Versión única en un solo archivo, de ahí la toman `.iss` y
`paquete.json`, y un manifiesto **firmado** con `firma.py` (Ed25519 en
stdlib pura, ya está escrito y probado para la licencia) con el aviso "hay
una nueva". Descargar y reemplazar puede venir después; enterarse ya vale.

**Costo.** Medio.

### C13 — Atajos de teclado

Hoy hay `keydown` en tres lugares: Grabaciones (línea 432), el buscador de
Modelos (760) y la previa (475). Falta lo global: cambiar de pestaña,
arrancar/parar, abrir la previa de la primera en vivo, y quizá una paleta
de comandos. Costo bajo.

---

## D. La grande y cara

### D14 — Multi-sitio

Soportar chaturbate, cam4 y compañía. Hay que abstraer "sitio" en
`sitio.py` y en la capa de protocolo (`mouflon.py` es específico de
doppiocdn), y después **mantener ingeniería inversa de un protocolo ajeno
por cada sitio nuevo** — con el precedente de que ya hubo que derivar la
tabla de claves MOUFLON una vez. Alto impacto, alto costo permanente. No
antes de que A y B estén cerrados.

---

## Lo que no conviene hacer

Queda escrito para no volver a gastar tiempo ahí:

- **Automatizar el login con Playwright.** Cloudflare detecta CDP (el
  protocolo, no la ventana) y corta con "Actividad automática detectada".
  Probado tres veces. SPEC 15 lo resolvió al revés: ventana de Edge normal
  ruteada por el proxy, y el sniffing del tráfico.
- **Importar `detectar_m3u8.py` desde el proceso de Nimbo.** Termina en
  `addons = [Detector()]`: cualquier import construye un detector vivo
  dentro de `app.py`. Por eso `mp4.py`, `vivo.py` y `cosecha_streams.py`
  portan constantes a mano.
- **Exponer el servidor a la red sin contraseña** (ver C10).
- **Borrar `.mp4` sin papelera.** La excepción de SPEC 14 son los
  segmentos ya unidos, que son regenerables; una grabación son horas.
- **Reconstruir una grilla con `innerHTML` en el camino del delta.** Es lo
  que congelaba la ventana (SPEC 01); cualquier vista nueva se reconcilia
  por clave.

---
