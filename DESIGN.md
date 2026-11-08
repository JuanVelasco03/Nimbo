---
name: Nimbo
description: A single-user desktop control room for unattended stream recording — tally lights, never a SaaS dashboard.
colors:
  broadcast-blue: "#4f8fe0"
  signal-red: "#e5484d"
  tally-amber: "#d9a441"
  signal-green: "#3ddc84"
  warning-orange: "#e2793d"
  grafito: "#121316"
  panel: "#1b1d22"
  panel-alto: "#23262d"
  borde: "#2b2e36"
  borde-fuerte: "#3a3e49"
  texto: "#e7e8ec"
  atenuado: "#8a8d99"
  apagado: "#5c5f6b"
typography:
  display:
    fontFamily: "IBM Plex Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "21px"
    fontWeight: 700
    lineHeight: "normal"
    letterSpacing: "-0.01em"
  title:
    fontFamily: "IBM Plex Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "17px"
    fontWeight: 600
  body:
    fontFamily: "IBM Plex Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "15px"
    fontWeight: 600
  label:
    fontFamily: "IBM Plex Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 500
    letterSpacing: "0.01em"
  caption:
    fontFamily: "IBM Plex Sans, Segoe UI, system-ui, sans-serif"
    fontSize: "12px"
    fontWeight: 400
  mono:
    fontFamily: "IBM Plex Mono, Consolas, Courier New, monospace"
    fontSize: "11px"
    fontWeight: 500
    letterSpacing: "0.02em"
rounded:
  chip: "3px"
  sm: "0.125rem"
  full: "9999px"
spacing:
  2xs: "0.25rem"
  xs: "0.5rem"
  sm: "0.75rem"
  md: "1.25rem"
  lg: "2rem"
components:
  button-primary:
    backgroundColor: "{colors.broadcast-blue}"
    textColor: "{colors.grafito}"
    rounded: "{rounded.sm}"
    padding: "0.5rem 1rem"
    typography: "{typography.label}"
  button-danger:
    backgroundColor: "{colors.signal-red}"
    textColor: "#1a0d0d"
    rounded: "{rounded.sm}"
    padding: "0.5rem 1rem"
    typography: "{typography.label}"
  button-ghost:
    backgroundColor: "{colors.panel-alto}"
    textColor: "{colors.texto}"
    rounded: "{rounded.chip}"
    padding: "0.25rem 0.625rem"
    typography: "{typography.caption}"
  chip-filtro:
    backgroundColor: "{colors.panel-alto}"
    textColor: "{colors.atenuado}"
    rounded: "{rounded.chip}"
    padding: "0.25rem 0.625rem"
    typography: "{typography.caption}"
  card-tarjeta-modelo:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.sm}"
    padding: "0.75rem"
  lista-ajustes-fila:
    backgroundColor: "{colors.panel}"
    padding: "0.75rem"
    typography: "{typography.label}"
  directiva-card:
    backgroundColor: "{colors.panel}"
    rounded: "{rounded.sm}"
    padding: "0.75rem"
---

# Design System: Nimbo

## Overview

**Creative North Star: "The Tally Wall"**

Nimbo is a single-user panel for an unattended Windows recorder, not a SaaS product with customers to court — the interface exists so one person can glance at it, in passing, and know what's recording, what failed, and what needs them, the way a wall of monitors in a real broadcast control room reads at a glance: by color and position, before anyone reads a word. The system already spoke this language before this pass (grafito panel, five fixed tally colors, a colored accent rail on every card); this pass is deliberately an evolution of that idiom, not a replacement — it gives the same vocabulary more intent (variable rail weight for urgency, a real type scale, a spacing scale, two container languages by control complexity) instead of inventing a new one.

The aesthetic is dense, dark, and utilitarian on purpose: a 13.5px root font-size (not the browser's 16px default) because this is a control panel meant to show a lot of state in a small window, not a marketing page meant to breathe. Depth comes from layering panel tones and from the rail's thickness and color, never from drop shadows or glow — a dark ambient glow was tried on the rail this session and deliberately removed for reading as the generic "AI-generated dark UI" tell; weight and color carry urgency instead.

**Key Characteristics:**
- Grafito/panel neutral stack with five fixed-meaning tally colors that never shift role.
- A literal-pixel type scale (11–21px) immune to the app's non-standard 13.5px root; a rem-based spacing scale that is consistent with it.
- IBM Plex Mono strictly reserved for timecodes, counts, and the log — never decorative.
- A single signature device, the `.riel` accent rail, whose thickness (not glow) communicates urgency.
- Two settings container languages chosen by control complexity, not by category: a compact hairline row for toggles, a bordered card for anything with an input.
- Flat by construction: no elevation shadows, only two intentional exceptions (a focus-style ring on the header status dot, a scrim under floating video controls).

## Colors

The palette is a dark instrument panel with one interactive accent and five fixed status signals that read as a tally-light bank, not a branded hue family.

### Primary
- **Broadcast Blue** (`#4f8fe0`, same value as `--color-tally-conectando`): the interactive accent — focus rings (`:focus-visible`), the active nav tab's underline, primary buttons ("Arrancar"), hover borders on cards/chips/inputs. It doubles as the "connecting" tally signal, which is deliberate: blue reads as "in progress / needs no alarm" in both roles.

### Secondary — Tally Signal Colors
Five fixed meanings, identical everywhere they appear (card rail, status dot, status badge, header transport text, log line color). None of these is ever repurposed for a decorative or unrelated use.
- **Signal Red** (`#e5484d`, `--color-tally-rec`): recording / failure. Also the "Parar" button and error log lines.
- **Tally Amber** (`#d9a441`, `--color-tally-privado`): private show / waiting / preflight warning.
- **Signal Green** (`#3ddc84`, `--color-tally-vivo`): live and healthy, on-state of the toggle switch, "sigue a tu cuenta real" tag.
- **Warning Orange** (`#e2793d`, `--color-tally-aviso`): no-signal / fault alert text (`.alerta`).

### Neutral
- **Grafito** (`#121316`): the page background, the darkest tone in the stack — also used as a "this got serious" hover background for destructive buttons.
- **Panel** (`#1b1d22`): the base surface for cards, modals, settings containers, log bars.
- **Panel Alto** (`#23262d`): one step up — thumbnail placeholders, inputs, scrollbar thumb, chip backgrounds, the toggle track's off-state.
- **Borde** (`#2b2e36`): default hairline border/divider.
- **Borde Fuerte** (`#3a3e49`): hover/emphasis border, default (inactive) rail color, offline rail color.
- **Texto** (`#e7e8ec`): primary text.
- **Atenuado** (`#8a8d99`): secondary text, help copy, section labels.
- **Apagado** (`#5c5f6b`): dimmest neutral — off-state dots and toggle thumbs, nothing a user reads as content.

### Named Rules
**The Tally Meaning Rule.** Each of the five signal colors carries exactly one meaning everywhere it appears — rail, dot, badge, header text, log line. A color is never borrowed for an unrelated accent just because its hue would otherwise fit; if a new surface needs a new kind of signal, it needs a sixth color, not a reused one.

**The Contrast-Safe Mix Rule.** When a tally color is used as text on a background where it fails the 4.5:1 floor (Signal Red at 4.3:1 on the header panel), mix it toward white only as far as the floor requires (`color-mix(in srgb, var(--color-tally-rec) 85%, white)`) — never substitute a different hue, and never apply the mix to the same color's rail/dot/badge use, which keeps the exact signal value.

## Typography

**Body Font:** IBM Plex Sans (with Segoe UI, system-ui fallback)
**Label/Mono Font:** IBM Plex Mono (with Consolas, Courier New fallback)

**Character:** A technical, legible UI face paired with a monospace reserved for exactly the data that benefits from tabular alignment — timecodes, byte counts, log output — so the mono face itself becomes a signal ("this is measured data") rather than a texture choice.

### Hierarchy
- **Display** (700, 21px, `tracking-tight`): the `.wordmark` only — the single place in the header with real typographic weight, by design (everything else in the header is a transport bar, not a brand lockup).
- **Title** (600, 17px): modal/preview name (`.modal-cuerpo .nombre`) — one step up from the card name for the single most important line in an expanded view.
- **Body / emphasis** (600, 15px, `--text-base`): the card name (`.nombre`) — deliberately one step above the app's plain running text so the one fact a user scans for (whose card is this) doesn't compete on equal footing with everything else in the card.
- **Default running text** (13.5px, the literal root size, untokened): controls, nav, buttons, and most body copy that doesn't need to stand out — this is the floor, not a named step.
- **Label** (500, 13px, `--text-sm`): nav buttons, primary/secondary buttons, settings-row titles.
- **Caption** (400, 11–12px, `--text-2xs`/`--text-xs`): help text, metadata, badges, section headers (uppercase, `tracking-[0.08em]`) — deliberately the smallest text in any card, so it never competes with the name.

### Named Rules
**The Literal-Pixel Rule.** The type-scale tokens (`--text-2xs` through `--text-lg`) are declared in literal px, never rem. The `<html>`/`<body>` root is fixed at 13.5px (a control panel, not a 16px marketing page), and a rem-based type token would silently resolve against that non-standard root instead of the intended pixel value — this was a real bug caught and fixed this session. The spacing scale (`--spacing-2xs` through `--spacing-lg`) stays in rem on purpose, because it's meant to track that same 13.5px root the way the rest of the app's Tailwind spacing already does.

**The Mono Reservation Rule.** IBM Plex Mono is used only for timecodes, byte/part counts, the log, and short technical tags (VPN/quality/part badges). It never appears as a decorative "technical-looking" treatment on prose, labels, or headings — if a string isn't actually a measured or logged value, it stays in Plex Sans.

**The 16px Edge-Safety Rule.** Any element touching the viewport edge needs at least 16px of clearance, even though this app's rem resolves smaller than catalog Tailwind values. `.aviso-preflight` was bumped from `px-4` (1rem = 13.5px, under the floor) to `px-5` (1.25rem = 16.9px) this session for exactly this reason — treat `px-4` as unsafe on an edge in this codebase.

## Layout

The app is a fixed-chrome single window (`html, body` at `h-full`, `overflow-hidden`), not a scrolling page: a transport header, an optional preflight banner, five tabs' content, and a log footer, with only the tab body scrolling. Density is the explicit goal over marketing-style whitespace — the spacing scale (`--spacing-2xs` 4px, `-xs` 8px, `-sm` 12px, `-md` 20px, `-lg` 32px, all in rem against the 13.5px root) replaces what used to be ad hoc values (`p-3.5`, `mb-3.5`, `gap-2.5`) so rhythm is consistent instead of improvised.

Card grids all use `repeat(auto-fill, minmax(_, 1fr))` so density responds to window width rather than a fixed breakpoint set — more tarjetas per row on a wide window, never fewer than fit at the minimum. The minimum tracks what the grid actually holds, not one fixed number: `260px` for Grabando's recording cards, `220px` for Modelos' favorites (tighter, since those cards carry less state), `190px` for the Grabaciones thumbnail grid, and `340px`/`360px` for the Ajustes settings-card grids (`.directiva`, which needs room for a label, help text, and an input). The log table (`fila-registro`) is the one place layout is column-locked rather than fluid: fixed time and prefix columns (`4.25rem`, `minmax(0, 12rem)`) with only the message column flexing, because a scanning log needs stable alignment more than it needs fluid width.

## Elevation & Depth

Nimbo is flat by construction. Depth is conveyed by neutral layering (grafito → panel → panel-alto, darkest to lightest) and by the rail's color/thickness, never by drop shadows or ambient glow. There are exactly two deliberate departures from flat, both small and functional, not decorative: a soft ring halo on the header status dot when healthy (`box-shadow: 0 0 0 3px color-mix(...)`, a focus-ring-style halo, not an ambient glow), and a dark linear-gradient scrim behind floating video controls (`.transporte-vivo`, `.marca-tiempo`) so light text stays legible over arbitrary video content.

### Named Rules
**The No-Glow Rule.** A colored drop-shadow/glow on the rail was tried this session and removed: it read as the generic dark-UI "AI slop" accent rather than anything native to this system. Urgency is rail thickness and color only (2px calm, 4px for `grabando`/`mal`) — never a shadow.

## Shapes

Corners are small and consistent: Tailwind's `rounded-sm` utility on cards, modals, buttons, and containers; a tighter `3px` radius on chips, tags, and small inline buttons (badges, VPN/quality/part tags, `boton-chico`); `rounded-full` only on genuinely circular elements (status dots, the toggle track/thumb, the scrollbar thumb). Media surfaces (`.riel`, `.miniatura-grande` inside cards) are explicitly `rounded-none` — the thumbnail is a cropped broadcast frame, not an avatar, and squares read as footage rather than a profile picture. Borders are hairline (1px) throughout, in `--color-borde` at rest and `--color-borde-fuerte` or the accent color on hover/focus; there is no heavier border weight anywhere in the system.

## Components

### Buttons
- **Shape:** `rounded-sm`, hairline or borderless depending on variant.
- **Primary** (`button.principal`): Broadcast Blue background, grafito text, `px-4 py-2`, `brightness-110` on hover — this is the one place a tally color becomes a surface rather than a signal.
- **Danger** (`button.principal.parar`): swaps to Signal Red background with near-black (`#1a0d0d`) text for contrast; same shape and hover behavior as primary.
- **Ghost / small** (`.boton-chico`): panel-alto background, hairline `--color-borde` border, hover border shifts to Broadcast Blue (or to Signal Red + grafito background for `.peligro`, reading as "this got serious").

### Chips
- **Style** (`.chip-filtro`): panel-alto background, atenuado text, `3px` radius border.
- **State:** `.prendido` shifts border and background to a blue-tinted mix (`color-mix(... tally-conectando 16%, panel-alto)`) and text to full texto; `.en-cero` drops to 50% opacity rather than disappearing, so a filter with no matches stays visible but clearly empty.

### Cards / Containers
- **Corner Style:** `rounded-sm`, media children `rounded-none`.
- **Background:** `--color-panel`, hover border shifts to `--color-borde-fuerte` (plain cards) or the active tally color (recording cards, grabaciones cards).
- **Shadow Strategy:** none — see Elevation & Depth.
- **Border:** 1px `--color-borde` at rest.
- **Internal Padding:** `--spacing-sm` (12px) is the standard card-body padding.

### Inputs / Fields
- **Style:** panel-alto background, hairline `--color-borde` border, `rounded-[3px]`.
- **Focus:** border shifts to Broadcast Blue, `outline: none` on the field itself (the global `:focus-visible` ring handles keyboard focus elsewhere).

### Navigation
- **Style:** `nav button` is borderless/transparent at rest, atenuado text; hover and active both move to `panel-alto` background with texto color; the active tab is marked by a 2px Broadcast Blue underline (`::after`), not a filled pill or a left-side indicator.

### The Riel (signature component)
A colored accent rail on every card and the preview modal — the system's one true signature device, carried over and sharpened from the prior design rather than invented this session. On vertically-tiled cards (`.tarjeta-modelo`, `.modal`) it renders as a **top** bar spanning the full width, not a left-edge bar; thickness and color both carry meaning: 2px for calm states (`online`, `publico`, `offline`, `privado`), 4px for the two states that need attention (`grabando` solid red, `mal` an orange diagonal hazard stripe). It never carries a glow (see Elevation's No-Glow Rule). It needs an explicit `z-index` (`z-10`): as an `absolute`-positioned sibling of `.miniatura-grande` (also positioned), without one it loses paint order to whichever positioned sibling comes later in the DOM and disappears under the thumbnail — a real, previously-unnoticed bug this pass fixed, not a hypothetical.

### Settings Containers (signature split)
Two container languages, chosen by control complexity, not by settings category — the single biggest density decision in this system.
- **`.lista-ajustes` / `.ajuste-fila`** — for a single boolean toggle. A compact, hairline-separated row: title + help text on the left, the toggle on the right, no card border or background of its own (the list container carries one shared border). Modeled on a native OS settings list (iOS/macOS), chosen specifically so many toggles can be scanned at once instead of read card by card.
- **`.directiva`** — for anything that needs an input, select, or action button (text field, number, dropdown, "Examinar" browse button). A full bordered card with its own padding, because that control genuinely needs the room a toggle doesn't.
- **The rule for future settings:** classify by what the control *is*, not what it's *about* — a new boolean always goes in `.lista-ajustes` even if it lives next to directivas in the same section; a new setting with a value field always gets a `.directiva` card even if it's a one-off toggle's neighbor.

## Do's and Don'ts

### Do:
- **Do** keep all five tally colors' meaning fixed everywhere they appear (rail, dot, badge, header, log) — see The Tally Meaning Rule.
- **Do** declare new type-scale steps in literal px, never rem, as long as the 13.5px root stands — see The Literal-Pixel Rule.
- **Do** reserve IBM Plex Mono for timecodes/counts/log/technical tags only — see The Mono Reservation Rule.
- **Do** give every `absolute`/`relative` sibling pair an explicit `z-index` when paint order matters; two positioned siblings with `z-index: auto` paint in DOM order, not by role or element type (the rail-under-thumbnail bug).
- **Do** treat `1rem` (`px-4`) as unsafe on a true viewport edge in this codebase; use `px-5` (1.25rem ≈ 16.9px) or an explicit value at or above 16px instead — see The 16px Edge-Safety Rule.
- **Do** route a new boolean setting to `.lista-ajustes` and anything needing an input/select/button to `.directiva`, regardless of what section it lives in.

### Don't:
- **Don't** add a drop-shadow or ambient glow to the rail, a card, or any accent element to signal urgency or depth — thickness and color already do that job, and a glow was tried and removed this session specifically because it read as generic AI-dark-UI decoration, not as anything native to this system.
- **Don't** put a colored left-edge border ("side-tab") on a banner or horizontal bar to signal status — this system's accent language is a *top* rail on cards, not a left tab on banners; it was tried on `.aviso-preflight` this session and reverted for tripping exactly that tell. `.aviso-preflight` keeps only its bottom border.
- **Don't** repurpose a tally color for a new, unrelated decorative accent just because the hue would otherwise work — a new kind of signal needs a sixth color, not a borrowed one.
- **Don't** treat `nimbo\web\icono.ico` as a settled brand asset in any new design work — PRODUCT.md records it as explicitly not kept; don't derive a palette or mark from it.
