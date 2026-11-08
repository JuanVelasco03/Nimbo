---
version: 1
slug: "nimbo-web-app"
primary_target: "nimbo-web-app"
related_targets: []
---

# Nimbo — rediseño de toda la app (Operate)

Alcance: las cinco pestañas (Grabando, Grabaciones, Modelos, Ajustes,
Registro), el header/transporte global, los modales de previa/reproducción
y el banner de preflight. Evolución pedida por el usuario explícitamente
("evolucionar el control de video"), no reemplazo de mundo visual — sin
concept-seed roll: la dirección ya está fijada por el usuario.

Motivos confirmados por el usuario: se ve poco pulida/amateur, cuesta
escanear rápido, le falta personalidad, mal uso de densidad/espacio.

## Direction contract

THESIS: Nimbo ya habla un idioma propio — panel grafito, luces tally de
cinco colores que nunca cambian de significado — pero lo usa con timidez:
tarjetas del mismo molde para todo, jerarquía tipográfica plana, una
grilla de ajustes que trata un toggle y un campo numérico como el mismo
objeto. Se refuerza "panel de control de video real" como mecanismo, y se
abandona la grilla genérica de card-con-icono que hoy repite sin motivo.

OWN-WORLD: paleta grafito/panel y los cinco colores tally (`--color-tally-*`)
quedan intactos — es ley del sistema, no se tocan los valores. Se suma una
escala tipográfica real (11/12/13/15/17/21/26px) y una escala de espaciado
4/8/12/20/32 aplicada con disciplina en vez de valores sueltos. IBM Plex
Sans sigue siendo la voz de UI; IBM Plex Mono se reserva estrictamente para
timecode/conteos/registro (nunca como decoración "técnica"). El riel de 3px
de color tally en cada tarjeta se mantiene y se vuelve más intencional
(grosor/longitud variable según la urgencia del estado, no solo presencia
binaria). Las tarjetas dejan de ser un molde único: una fila de ajuste tipo
toggle es compacta e inline: título + ayuda a la izquierda, control a la
derecha, sin el padding de una tarjeta completa; un ajuste con input/select
mantiene más cuerpo. El header dejar de ser una nav de SaaS: se vuelve una
barra de transporte real con un indicador de estado global más legible
(no solo un punto) y jerarquía clara entre identidad/pestañas/transporte.

STORY: el usuario entra y en un vistazo sabe si algo está grabando, si algo
falló, y qué necesita atención — sin leer texto, por color y posición, como
una pared de monitores real. Puede escanear Modelos/Grabando en modo
denso (más tarjetas por fila, menos aire desperdiciado) y entender Ajustes
por secciones reales, no por una grilla uniforme de cajas.

FIRST VIEWPORT: Grabando con el header-transporte arriba (identidad +
pestañas a la izquierda, estado global + Arrancar/Parar a la derecha, con
mayor peso visual que hoy), el banner de preflight si corresponde, y la
grilla de tarjetas de modelos grabando con más densidad y el riel tally
como señal primaria — sin depender del header para saber el estado.

FORM: evolución del mundo ya pinneado por el usuario (control de
video/tally-light); no se corrió concept-seed (no aplica: no es mundo
nuevo ni reemplazo). seed: none (unavailable)

FINISH: unreviewed and undocumented is unfinished; this build ends with
the finish review, the verdict, DESIGN.md, and every shipping raster
carrying its provenance.
