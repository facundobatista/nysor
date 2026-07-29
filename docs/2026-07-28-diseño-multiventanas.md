# Plan incremental — Multibuffer con tabs (Camino A: multigrid) — v3

Objetivo: abrir varios archivos, verlos como tabs de Qt, switchear desde la GUI, y
eventualmente poder detachear un tab a una ventana suelta para ver dos buffers en vivo
lado a lado.

Mecanismo elegido: **`ext_multigrid`** (un grid por ventana de Neovim), **con
`ext_cmdline`** (command-line renderizada por un widget de Qt) pero **sin `ext_messages`**
(los mensajes siguen cayendo en el *message grid*, ver abajo).

Decisiones tomadas:
- **Franja de mensajes:** franja fija al pie, siempre visible; crece a varias líneas cuando
  el mensaje lo requiere (ver "modelo de grids").
- **Mapeo:** **tab ↔ tabpage** de Neovim (`:tabedit`). El detach del Paso 7 convierte ese
  caso a split para tener los dos buffers vivos a la vez.

**Versión de Neovim: `~/sistema/nvim-0.12.2`** (binario AppImage que usamos para el
desarrollo). Todo lo de abajo está **verificado empíricamente** contra ese binario (attaché
una UI multigrid y observé los eventos reales), no de memoria. Las firmas de eventos salen de
`nvim_get_api_info().ui_events` de esa versión.

Principio de cada paso: **que compile, corra y se pueda probar solo**, sin romper lo
anterior. Cada paso deja algo demostrable.

## Sobre las pruebas

- **No hacemos unit tests que solo mockeen la GUI**: terminan mockeando todo y no prueban
  nada real. La verificación de comportamiento visual/interactivo es **manual**, en vivo.
- **Sí testeamos las estructuras de datos nuevas** que tengan lógica propia (el registro de
  grids del Paso 1 es el caso claro). Esos tests van en `tests/` con `pytest tests/`, sin GUI.
- Correr la app: `python -m nysor <paths>` (o el script `nysor`). Hay `ex1.txt`/`ex2.txt`
  para probar.

---

## Modelo de grids en multigrid (verificado en 0.12.2)

Al hacer `nvim_ui_attach(80, 24, {ext_linegrid, ext_multigrid})` aparecen **tres** grids
(con 1 archivo abierto), y cada uno lo renderizamos en un display distinto:

| grid | qué es | lo renderizamos en | cómo lo identifico |
|------|--------|--------------------|--------------------|
| **2** (luego 4, 6…) | **ventana / editor** (1 por *window* de Neovim; ahora 1) | `text_display` | `win_pos [grid, win, row, col, w, h]` |
| **1** | **grid global**: full-size, pero su área de ventana queda **vacía** bajo multigrid; lo único dibujado ahí es la **statusline** (y la tabline, que apagamos) | `statusline_display` (solo la rebanada de la statusline) | es el `grid=1` de siempre |
| **3** | **grid de mensajes Y command-line** (¡la misma grilla!): mensajes ("written") y lo que se tipea en la cmdline (`:w`, `/foo`) | `message_display` | `msg_set_pos [grid, row, scrolled, ...]` |
| **5+** | **floating windows** (p.ej. el popup de completado de `:e foo<Tab>`); transitorios | — (**no** los dibujamos aún) | `win_float_pos [...]` |

Consecuencias clave (y correcciones a versiones anteriores del plan y a confusiones comunes):

- **NO hay un grid de cmdline y otro de mensajes por separado.** Es **uno solo** (grid 3): sirve
  para mensajes *y* para la command-line. Hoy, si tipeás `:saveas foo`, se ve en la franja de
  abajo (la mostramos). Recién en el **Paso 6 (`ext_cmdline`)** la cmdline se **separa** de grid 3:
  deja de dibujarse ahí y pasa a llegar por eventos (`cmdline_show`), que renderizamos en un
  widget de Qt propio. Ahí sí habrá un "aparato de cmdline" aparte.
- **El grid 1 NO es el editor.** Es el grid global; su área de ventana está vacía (la ventana
  dibuja en grid 2). De grid 1 renderizamos **solo la rebanada de la statusline**: las filas
  `[fondo_de_la_ventana .. fila_de_mensajes−1]`. No es "la última línea porque sí"; es *esa*
  franja porque ahí Neovim dibuja la statusline (con 1 ventana da 1 línea, la fila 22 de 24).
- **El alto de las franjas no se fuerza**, sale de Neovim: la de mensajes = `alto_grid3 −
  fila_de_msg_set_pos` (crece sola si el mensaje es multilínea); la de statusline = la rebanada
  de arriba. El **ancho** de cada franja sale directo del `grid_resize` de *su* grid.
- **Lo que "se pierde" hoy** no son los mensajes (esos se ven), sino los **floating windows**
  (grids 5+, p.ej. el popup de completado): no los dibujamos todavía. Queda para cuando
  manejemos floats.
- **Handles de ventana (`win`) llegan como `ExtType(code=1, ...)`**, que `nvim_interface`
  ya decodifica vía `ext_hook`. Sirven para `nvim_set_current_win` (Paso 3).
- **Eventos nuevos que hoy manejamos (antes logueaban "not implemented"):** `win_pos`,
  `win_hide`, `win_close`, `grid_destroy`, `msg_set_pos`, `chdir`, y **`win_viewport_margins`**
  (este último es `[since 12]`, nuevo en 0.12).

Firmas relevantes (de la API de 0.12.2):

```
grid_resize(grid, width, height)
grid_line(grid, row, col_start, data, wrap)
grid_scroll(grid, top, bot, left, right, rows, cols)
grid_cursor_goto(grid, row, col)
grid_destroy(grid)
win_pos(grid, win, startrow, startcol, width, height)
win_hide(grid)
win_close(grid)
win_viewport(grid, win, topline, botline, curline, curcol, line_count, scroll_delta)
win_viewport_margins(grid, win, top, bottom, left, right)          # nuevo en 0.12
win_float_pos(grid, win, anchor, anchor_grid, anchor_row, anchor_col, mouse_enabled, zindex, compindex, screen_row, screen_col)
msg_set_pos(grid, row, scrolled, sep_char, zindex, compindex)
cmdline_show(content, pos, firstc, prompt, indent, level, hl_id)   # hl_id agregado en versiones nuevas
cmdline_pos(pos, level)
cmdline_hide(level, abort)
```

---

## Paso 1 — Activar `ext_multigrid` (una sola ventana) + registro de grids + franja de mensajes

Este paso fusiona la ex-"costura de ruteo" (indirección `grid_id → display`) con la
activación de multigrid, porque por separado la primera es trivial.

### Qué toco

**a) Indirección `grid_id → display`.** Hoy los handlers de `nvim_notifications.py` asumen un
único `TextDisplay` y tienen `assert grid_id == 1`. Los cambio para que busquen el display en
un **registro** por `grid_id`. Sin esto, multigrid no tiene a dónde rutear.

**b) El registro de grids (estructura nueva, testeable).** Mapea `grid_id → record`:

```
GridRecord: kind ('window' | 'message'), text_display, tab_index, win_handle, scrollbars, ...
```

Con `add`/`get`/`remove` y noción de "grid activo". **Esta es la estructura que sí testeamos**
(alta/baja/lookup, grid activo, distinguir window vs message), sin tocar la GUI.

**c) Activar multigrid.** `setup_nvim` → `{"ext_linegrid": True, "ext_multigrid": True}`.
Aparecen los grids 1/2/3 como en la tabla. Mapeo el **grid de ventana** (2) al `TextDisplay`/
tab que ya existe, creo la **franja** para el message grid (3), y **dejo de renderizar el
grid 1**.

**d) Resize.** En la base **tab ↔ tabpage** todas las tabpages usan el área completa, así que
el resize sigue siendo **global** con `nvim_ui_try_resize(cols, rows)` (redimensiona grid 1 y,
en consecuencia, la ventana activa). `nvim_ui_try_resize_grid(grid, ...)` (tamaño por grid
independiente) recién hace falta en el **Paso 7** (splits/detach), no ahora. *(Corrige la v2,
que lo ponía acá.)*

**e) Handlers nuevos:** `win_pos`, `grid_destroy`, `msg_set_pos`, y `win_viewport_margins`
(por ahora se puede ignorar, pero hay que aceptarlo para no ensuciar el log). Detalle abajo.

**f) La franja de mensajes.** Detalle abajo.

### Detalle: la franja de mensajes (el *message grid*)

- **Qué es:** un grid dedicado (grid 3) que Neovim ubica con `msg_set_pos [grid, row, scrolled,
  ...]`. `row` es la fila del grid global donde arranca el message grid. En reposo `row = 23`
  (última línea) → **1 línea visible**. El contenido llega como `grid_line` normal sobre ese
  grid.
- **Mensajes multilínea → SÍ soportados (responde tu FIXME).** Cuando el mensaje/cmdline no
  entra en una línea, Neovim **baja el `row`** de `msg_set_pos` (lo vi pasar a 22 con
  `scrolled=True` al tipear un path largo) → la franja **crece**. Alto de la franja en líneas =
  `global_rows - row`. Con `:messages`, errores largos o prompts "press ENTER", crece más.
  Todo esto lo maneja el Paso 1; no queda nada afuera.
- **Popup de completado de `:edit foo<Tab>` → esto SÍ queda fuera de alcance (pero no rompe
  nada).** Es una cosa **distinta** de los mensajes multilínea: al completar, Neovim abre un
  **floating window** aparte (grid transitorio nuevo, vía `win_float_pos`; lo vi como grid 5 en
  el probe) con la lista de candidatos. Eso es maquinaria de *floats*, no del message grid. Sin
  dibujar ese float, **el completado sigue funcionando** (Neovim completa el texto igual); lo
  único que no se ve es la **lista visual de candidatos**, hasta que agreguemos soporte de
  floating windows (ver "Notas transversales").
- **Solo-display, sin foco ni mouse (responde tu FIXME):** la franja es puramente informativa.
  No debe robar teclado ni aceptar clicks. El `TextDisplay` actual instala foco (`StrongFocus`)
  y handlers de mouse/teclado en `BaseDisplay`; para la franja uso una variante **display-only**
  (`NoFocus`, sin reenviar mouse/teclado). Concreto: conviene separar en `BaseDisplay` un modo
  read-only, o una subclase que no instale esos handlers.
- **¿Reuso `text_display.py`?** Sí. El render de celdas (celda → `CharFormat` → `QPainter`,
  highlights, chars anchos, fuente) ya está en `TextDisplay`. Instancio un `TextDisplay`
  display-only atado al message grid; su alto visible lo manejo con el `row` de `msg_set_pos`.

### Detalle: handlers `win_pos`, `grid_destroy`, `msg_set_pos`, `win_viewport_margins`

- **`win_pos(grid, win, startrow, startcol, width, height)`:** asocia un `grid` con su ventana
  (`win`) y su geometría. Es cómo aprendo "el grid 2 es la ventana de edición" para atarlo al
  tab/`TextDisplay`. El `win` lo necesito para enfocar/switchear (Paso 3).
- **`grid_destroy(grid)`:** el grid desapareció (se cerró la ventana) → desmonto su
  `TextDisplay`/tab y lo saco del registro. Contraparte de la creación.
- **`msg_set_pos(grid, row, scrolled, ...)`:** define cuál es el message grid y dónde arranca;
  con eso ubico y dimensiono la franja (y detecto crecimiento multilínea).
- **`win_viewport_margins(grid, win, top, bottom, left, right)`:** márgenes internos de la
  ventana (nuevo en 0.12). Por ahora lo acepto e ignoro; puede ajustar el cálculo de viewport
  más adelante (Paso 4).
- (Relacionado, Paso 2: **`win_hide(grid)`** = la ventana no se muestra ahora, p.ej. tabpage
  inactiva; la mantengo marcada como oculta.)

### Qué obtengo

Edición de un archivo sobre la infraestructura multigrid, con los mensajes en la franja
inferior (creciendo cuando hace falta). Prueba de que la plomería multigrid anda.

### Cómo pruebo

- **Unit (estructura nueva):** el registro de grids — alta, lookup, baja, grid activo, distinguir
  window vs message.
- **Manual** (con `~/sistema/nvim-0.12.2`): abrir un archivo, editar, `:w` (ver "written" en la
  franja), `/texto` (resaltado + mensaje de búsqueda), un `:echo` de varias líneas (ver la
  franja crecer), `G`/`gg` (scroll).

---

## Paso 2 — Varios archivos → varios tabs (el core)

**Qué toco:** `_feed_neovim_from_path`: el primer path con `:edit`, del segundo en adelante
`:tabedit <path>`. Cada tabpage nueva = ventana nueva = **grid nuevo** (vi aparecer grid 4) →
doy de alta en el registro un `TextDisplay` + tab de Qt para ese grid.
- Ciclo de vida: al `tabedit` llega `grid_resize` + `win_pos` del grid nuevo y **`win_hide`
  del anterior** (confirmado). Al cerrar, `win_close`/`grid_destroy`.
- Nota de diseño (confirmada): **tab ↔ tabpage**. Solo la tabpage activa se dibuja; las otras
  reciben `win_hide` y quedan "congeladas" hasta mostrarlas. Para un UI de tabs, perfecto.

**Qué obtengo:** `python -m nysor ex1.txt ex2.txt` → **dos tabs**, cada uno con su contenido.

**Cómo pruebo:**
- Manual: abrir dos archivos, ver dos tabs con contenidos distintos.

---

## Paso 3 — Switch de tab en los dos sentidos

**Qué toco:**
- Cambio de tab en Qt (`currentChanged`) → `nvim_set_current_tabpage` / `nvim_set_current_win`
  (uso el `win` que aprendí de `win_pos`).
- Autocmd `TabEnter`/`WinEnter` con `rpcnotify` → notificación tipo `current_changed` →
  actualizo el tab activo de Qt sin re-disparar el evento (guard).
- En Neovim, `tabnext`/`tabprevious` disparan `win_hide` del saliente + `win_pos` del entrante
  (confirmado) — eso ya me sirve para saber qué grid quedó activo.

**Qué obtengo:** clickear un tab mueve a Neovim, y `gt`/`gT` en Neovim mueve el tab de Qt.

**Cómo pruebo:**
- Manual: clickear tabs; usar `gt`/`gT`; verificar que coinciden.

---

## Paso 4 — Estado por tab (título, modificado, scrollbars)

**Qué toco:** hoy `state_buffer_is_modified`, `state_buffer_filepath`, scrollbars y
`adjust_viewport` son globales. Los muevo al record del registro (**por grid/tab**). Los
autocmds `BufModifiedSet`/`BufFilePost` pasan a informar qué ventana/buffer cambió. Menú
`Save`/`Open` se habilita según el tab activo. (Acá caen varios `FIXME.90`.) Acá también puedo
usar `win_viewport_margins` si hace falta para el cálculo fino del viewport.

**Qué obtengo:** cada tab con su nombre de archivo, su indicador de modificado y su scroll
independiente.

**Cómo pruebo:**
- Manual: modificar un archivo → solo su tab marcado; scrollear cada uno por separado.

---

## Paso 5 — Mouse con `grid_id` real

**Qué toco:** en `text_display.py` los eventos de mouse usan `grid = 0` hardcodeado
(FIXME.90). Cada `TextDisplay` pasa a conocer su `grid_id` (del registro) y lo manda en
`nvim_input_mouse`.

**Qué obtengo:** clickear/seleccionar en un tab enfoca y opera sobre la ventana correcta.

**Cómo pruebo:**
- Manual: click en un tab posiciona el cursor ahí; drag selecciona ahí.

---

## Paso 6 — `ext_cmdline`

**Qué toco:** agrego `ext_cmdline: True` al attach. La command-line deja de dibujarse en el
message grid y pasa a llegar por eventos (confirmado en 0.12.2):
- `cmdline_show(content, pos, firstc, prompt, indent, level, hl_id)` — `content` es lista de
  chunks `[hl_id, texto]`; `firstc` es `:`/`/`/`?`; `pos` es el cursor.
- `cmdline_pos(pos, level)`, `cmdline_hide(level, abort)`, `cmdline_special_char`,
  `cmdline_block_show/append/hide`.
Renderizo eso en un widget de Qt propio (barra inferior). El **message grid sigue existiendo**
para los `echo`/mensajes (confirmado: con ext_cmdline, `echo` sigue yendo al message grid), así
que la franja del Paso 1 se queda; ext_cmdline solo saca la cmdline de ahí. Esto es exactamente
"con ext_cmdline pero sin ext_messages".

**Qué obtengo:** `:`, `/`, `?`, `:%s/...` en un widget de command-line de Qt, con cursor y
posición.

**Cómo pruebo:**
- Manual: `:w`, `/foo` con incsearch, `:%s/a/b/gc`; ver que aparece en la barra Qt y ya no en
  la franja de mensajes.

---

## Paso 7 — Detach de tab a ventana suelta (side-by-side en vivo)

**Qué toco:** en Qt, reparento el widget del tab a una `QMainWindow`/`QDockWidget` flotante
(+ re-attach). En Neovim, para que **los dos queden vivos a la vez**, convierto ese caso a
**split en la misma tabpage** (ventanas coexistentes → los dos grids se dibujan). Acá **sí**
entra `nvim_ui_try_resize_grid(grid, cols, rows)` para darle a cada grid su tamaño según el
widget que lo contiene. Manejo la vuelta.

**Qué obtengo:** sacar un buffer a su propia ventana del SO y editar los dos en vivo, lado a
lado.

**Cómo pruebo:**
- Manual: detachear un tab, editar en ambas ventanas simultáneamente, re-attachear.

---

## Notas transversales

- **Orden y despliegue:** los Pasos 1–4 ya dan la feature pedida ("abrir varios, tabs,
  switchear"). El 5 es higiene necesaria para multi-ventana. El 6 (ext_cmdline) y el 7
  (detach) son mejoras encima de una base sólida.
- **Floating windows / popup de completado:** aparecen como grids transitorios vía
  `win_float_pos` (lo vi con `:edit foo<Tab>`). No se dibujan en la base; quedan como paso
  futuro (van bien junto con el manejo de floats del Paso 7).
- **Menú "New":** el `FIXME.90` de `MainMenu` pide un "New" para multibuffer; se agrega
  naturalmente en el Paso 2 o 4.
- **`ext_messages` queda fuera de alcance** (mucho más trabajo y código); es un paso futuro
  opcional, independiente de todo lo anterior.
