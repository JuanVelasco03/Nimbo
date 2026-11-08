# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Un único usuario: el propio desarrollador (Juan Velasco), dueño de la
máquina donde corre. No hay clientes ni distribución a terceros hoy —
la usa para su propio archivo de streams, sin supervisar la PC
constantemente (la app sigue grabando con la ventana cerrada, desde la
bandeja).

## Product Purpose

Automatizar la grabación de streams de stripchat/doppiocdn sin tener que
correr scripts a mano desde una consola. Reemplaza lo que antes era
editar `grabar.txt` y lanzar `detectar_m3u8.py`/`supervisor.py` por
separado, con una ventana propia para arrancar/parar, ver qué se está
grabando y elegir a quién grabar.

## Positioning

Tres cosas a la vez, confirmadas por el usuario, ninguna sacrificable
por las otras:

- **Confiabilidad desatendida**: graba 24/7 sin intervención — se
  recupera sola de caídas (`Motor._bucle_vigilancia`, backoff
  5s/15s/60s, reintento cada 15 min si se rinde), nunca corta una sesión
  en curso a lo bruto.
- **Eficiencia técnica**: el camino directo descifra el protocolo
  MOUFLON v2 y baja los segmentos con pedidos HTTP pelados, sin abrir un
  Chromium por modelo — mucho más liviano que el enfoque de "navegador
  invisible por modelo" que sigue existiendo como respaldo.
- **Simplicidad de uso**: interfaz en español, buscar por nombre con
  miniatura, un click para favoritear/grabar, sin tocar `grabar.txt` ni
  una consola en el día a día.

## Operating Context

Corre entero en la máquina Windows del usuario (ver `maquina_de_build`
y `disco_f_compartido_con_juegos` en memoria): nada en la nube, ningún
servidor remoto salvo el proxy/VPN de grabación y, si algún día se
activa, la verificación de licencia. Pensado para quedar corriendo en
segundo plano horas o toda la noche sin que nadie esté mirando la
pantalla — una caída tiene que poder pasar desapercibida y
autorepararse. El disco de grabación (`F:`) es compartido con otros usos
de la PC (juegos), así que compite por I/O con ellos.

## Capabilities and Constraints

- Solo Windows 10/11; depende de Microsoft Edge (ventana `--app`),
  mitmproxy, ffmpeg y opcionalmente Playwright+Chromium. No es
  multiplataforma ni se planea que lo sea.
- Ya existe infraestructura de licencia (Supabase + Ed25519,
  `empaquetado\licencia\`) y un instalador que empaqueta todo adentro,
  pero **no está en uso**: hoy no hay distribución ni venta a terceros.
  Es una capacidad construida para un futuro posible, no un hecho del
  producto actual — no asumir usuarios externos ni flujos de compra al
  diseñar.
- Dato sensible a tratar con cuidado: `sesion.json`/`sesion_capturada.json`
  equivalen a la contraseña de la cuenta real mientras la sesión viva
  (cifrados con DPAPI); nunca deben loguearse ni mostrarse en texto
  plano en ningún diseño futuro. `grabar.txt` (a quién se graba) es dato
  igual de sensible y nunca sale de la máquina.
- La búsqueda/miniaturas de modelos dependen de un endpoint no oficial
  del sitio; si cambia, la búsqueda deja de traer resultados pero el
  agregado manual por nombre exacto sigue andando.

## Brand Commitments

Solo el nombre "Nimbo" está fijado. El ícono actual
(`nimbo\web\icono.ico`, usado en la ventana y en la bandeja) **no** es
una decisión a preservar — el usuario pidió explícitamente otro tipo de
ícono/set de íconos a futuro. No hay paleta, tipografía ni tono de voz
fijados todavía: quedan abiertos al trabajo de diseño.

## Evidence on Hand

Hay una interfaz funcionando de verdad (pestañas Grabando, Modelos,
Siguiendo, Ajustes, Registro) con datos reales de la instalación del
usuario. No hay clientes, testimonios, precios públicos ni material de
marketing — no inventar ninguno de esos en trabajo futuro; si se
necesita contenido de ese tipo, confirmarlo con el usuario primero.

## Product Principles

1. Observar el motor de captura sin modificarlo — toda mejora de
   interfaz vive en la capa de Nimbo, nunca tocando `nucleo\`.
2. Ninguna operación larga bloquea la ventana: todo lo que tarda
   responde al toque y avisa su progreso de forma asíncrona (vía
   WebSocket), nunca con la UI colgada esperando al motor.
3. Preferir la recuperación automática a la intervención manual: una
   caída de madrugada no puede costar la grabación de toda la noche.
4. Tratar como secreto todo lo que identifica la cuenta real o a quién
   se graba; nunca exponerlo en logs, capturas de pantalla de diseño ni
   en texto plano en la interfaz.
5. Priorizar simplicidad en español por sobre configurabilidad: cada
   opción nueva en la interfaz debe justificar por qué no puede
   resolverse ya con un default razonable.
