# Incremental plan — Multibuffer with tabs (Path A: multigrid) — v3

Goal: open several files, see them as Qt tabs, switch from the GUI, and eventually be able to
detach a tab into a separate window to see two buffers live side by side.

Chosen mechanism: **`ext_multigrid`** (one grid per Neovim window), **with `ext_cmdline`**
(command-line rendered by a Qt widget) but **without `ext_messages`** (messages still land on
the *message grid*, see below).

Decisions taken:
- **Message strip:** a fixed strip at the bottom, always visible; it grows to several lines when
  the message needs it (see "grid model").
- **Mapping:** **tab ↔ Neovim tabpage** (`:tabedit`). The detach in Step 7 turns that case into a
  split so both buffers stay live at the same time.

**Neovim version: `~/sistema/nvim-0.12.2`** (the AppImage binary we use for development).
Everything below is **verified empirically** against that binary (attached a multigrid UI and
observed the real events), not from memory. The event signatures come from
`nvim_get_api_info().ui_events` of that version.

Principle for every step: **it compiles, runs, and can be tested on its own**, without breaking
what came before. Each step leaves something demonstrable.

## About the tests

- **We do not write unit tests that only mock the GUI**: they end up mocking everything and test
  almost nothing real. Visual/interactive behavior is verified **manually**, live.
- **We do test new data structures** that carry their own logic (the grid registry from Step 1 is
  the clear case). Those tests live in `tests/`, run with `pytest tests/`, no GUI.
- Run the app: `python -m nysor <paths>` (or the `nysor` script). There are `ex1.txt`/`ex2.txt`
  to test with.

---

## Grid model under multigrid (verified on 0.12.2)

Calling `nvim_ui_attach(80, 24, {ext_linegrid, ext_multigrid})` produces **three** grids (with 1
file open), and we render each one in a different display:

| grid | what it is | rendered in | how we identify it |
|------|------------|-------------|--------------------|
| **2** (then 4, 6…) | **window / editor** (one per Neovim *window*; now 1) | `text_display` | `win_pos [grid, win, row, col, w, h]` |
| **1** | **global grid**: full-size, but its window area stays **empty** under multigrid; the only thing drawn there is the **statusline** (and the tabline, which we disable) | `statusline_display` (only the statusline slice) | it is the usual `grid=1` |
| **3** | **messages AND command-line grid** (the same grid!): messages ("written") and what you type in the cmdline (`:w`, `/foo`) | `message_display` | `msg_set_pos [grid, row, scrolled, ...]` |
| **5+** | **floating windows** (e.g. the completion popup of `:e foo<Tab>`); transient | — (**not** drawn yet) | `win_float_pos [...]` |

Key consequences (and corrections to earlier versions of the plan and to common confusions):

- **There is NOT a separate cmdline grid and messages grid.** It is a **single one** (grid 3): it
  serves both messages *and* the command-line. Today, if you type `:saveas foo`, it shows in the
  bottom strip (we render it). Only in **Step 6 (`ext_cmdline`)** does the cmdline **split off**
  from grid 3: it stops being drawn there and starts arriving through events (`cmdline_show`),
  which we render in a dedicated Qt widget. Only then is there a separate "cmdline surface".
- **Grid 1 is NOT the editor.** It is the global grid; its window area is empty (the window draws
  on grid 2). From grid 1 we render **only the statusline slice**: the rows
  `[window_bottom .. message_row − 1]`. It is not "the last line just because"; it is *that* slice
  because that is where Neovim draws the statusline (with 1 window it is 1 line, row 22 of 24).
- **The strips' height is not forced**, it comes from Neovim: the message one =
  `grid3_height − msg_set_pos_row` (grows on its own when the message is multi-line); the
  statusline one = the slice above. The **width** of each strip comes straight from the
  `grid_resize` of *its own* grid.
- **What "gets lost" today** is not the messages (those show), but the **floating windows**
  (grids 5+, e.g. the completion popup): we do not draw them yet. Left for when we handle floats.
- **Window handles (`win`) arrive as `ExtType(code=1, ...)`**, which `nvim_interface` already
  decodes via `ext_hook`. They are used for `nvim_set_current_win` (Step 3).
- **New events we now handle (they previously logged "not implemented"):** `win_pos`, `win_hide`,
  `win_close`, `grid_destroy`, `msg_set_pos`, `chdir`, and **`win_viewport_margins`** (this last
  one is `[since 12]`, new in 0.12).

Relevant signatures (from the 0.12.2 API):

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
win_viewport_margins(grid, win, top, bottom, left, right)          # new in 0.12
win_float_pos(grid, win, anchor, anchor_grid, anchor_row, anchor_col, mouse_enabled, zindex, compindex, screen_row, screen_col)
msg_set_pos(grid, row, scrolled, sep_char, zindex, compindex)
cmdline_show(content, pos, firstc, prompt, indent, level, hl_id)   # hl_id added in newer versions
cmdline_pos(pos, level)
cmdline_hide(level, abort)
```

---

## Step 1 — Enable `ext_multigrid` (single window) + grid registry + message strip

This step merges the former "routing seam" (the `grid_id → display` indirection) with enabling
multigrid, because on its own the first part is trivial.

### What I touch

**a) `grid_id → display` indirection.** Today the handlers in `nvim_notifications.py` assume a
single `TextDisplay` and have `assert grid_id == 1`. I change them to look the display up in a
**registry** keyed by `grid_id`. Without this, multigrid has nowhere to route.

**b) The grid registry (new, testable data structure).** Maps `grid_id → record`:

```
GridRecord: kind ('window' | 'message'), text_display, tab_index, win_handle, scrollbars, ...
```

With `add`/`get`/`remove` and a notion of "active grid". **This is the structure we do test**
(add/remove/lookup, active grid, distinguishing window vs message), without touching the GUI.

**c) Enable multigrid.** `setup_nvim` → `{"ext_linegrid": True, "ext_multigrid": True}`. Grids
1/2/3 appear as in the table. I map the **window grid** (2) to the existing `TextDisplay`/tab,
create the **strip** for the message grid (3), and **stop rendering grid 1**.

**d) Resize.** In the **tab ↔ tabpage** base every tabpage uses the whole area, so the resize
stays **global** via `nvim_ui_try_resize(cols, rows)` (resizes grid 1 and, in turn, the active
window). `nvim_ui_try_resize_grid(grid, ...)` (per-grid independent sizing) is only needed in
**Step 7** (splits/detach), not now. *(This corrects v2, which put it here.)*

**e) New handlers:** `win_pos`, `grid_destroy`, `msg_set_pos`, and `win_viewport_margins` (can be
ignored for now, but must be accepted so the log stays clean). Detail below.

**f) The message strip.** Detail below.

### Detail: the message strip (the *message grid*)

- **What it is:** a dedicated grid (grid 3) that Neovim places with `msg_set_pos [grid, row,
  scrolled, ...]`. `row` is the global-grid row where the message grid starts. At rest `row = 23`
  (last line) → **1 visible line**. Content arrives as normal `grid_line` on that grid.
- **Multi-line messages → supported.** When the message/cmdline does not fit in one line, Neovim
  **lowers the `row`** of `msg_set_pos` (observed it go to 22 with `scrolled=True` when typing a
  long path) → the strip **grows**. Strip height in lines = `global_rows - row`. With `:messages`,
  long errors, or "press ENTER" prompts, it grows further. Step 1 handles all of this.
- **Completion popup of `:edit foo<Tab>` → out of scope (but nothing breaks).** This is a
  **different** thing from multi-line messages: while completing, Neovim opens a separate
  **floating window** (a new transient grid, via `win_float_pos`; observed as grid 5 in the probe)
  with the candidate list. That is *floats* machinery, not the message grid. Without drawing that
  float, **completion still works** (Neovim completes the text anyway); the only thing not shown
  is the **visual candidate list**, until we add floating-window support (see "Cross-cutting
  notes").
- **Display-only, no focus nor mouse:** the strip is purely informational. It must not steal the
  keyboard nor accept clicks. The current `TextDisplay` installs focus (`StrongFocus`) and
  mouse/keyboard handlers in `BaseDisplay`; for the strip I use a **display-only** variant
  (`NoFocus`, no forwarding of mouse/keyboard). Concretely: better to add a read-only mode to
  `BaseDisplay`, or a subclass that does not install those handlers.
- **Reuse `text_display.py`?** Yes. The cell rendering (cell → `CharFormat` → `QPainter`,
  highlights, wide chars, font) is already in `TextDisplay`. I instantiate a display-only
  `TextDisplay` bound to the message grid; its visible height is driven by the `row` of
  `msg_set_pos`.

### Detail: handlers `win_pos`, `grid_destroy`, `msg_set_pos`, `win_viewport_margins`

- **`win_pos(grid, win, startrow, startcol, width, height)`:** associates a `grid` with its
  window (`win`) and its geometry. This is how I learn "grid 2 is the editor window" to bind it to
  its tab/`TextDisplay`. The `win` is needed to focus/switch (Step 3).
- **`grid_destroy(grid)`:** the grid is gone (its window was closed) → I tear down its
  `TextDisplay`/tab and drop it from the registry. The counterpart to creation.
- **`msg_set_pos(grid, row, scrolled, ...)`:** defines which grid is the message grid and where it
  starts; with that I place and size the strip (and detect multi-line growth).
- **`win_viewport_margins(grid, win, top, bottom, left, right)`:** the window's internal margins
  (new in 0.12). For now I accept and ignore it; it may refine the viewport computation later
  (Step 4).
- (Related, Step 2: **`win_hide(grid)`** = the window is not shown right now, e.g. an inactive
  tabpage; I keep it marked as hidden.)

### What I get

Editing one file on top of the multigrid infrastructure, with messages in the bottom strip
(growing when needed). Proof that the multigrid plumbing works.

### How I test

- **Unit (new structure):** the grid registry — add, lookup, remove, active grid, window vs
  message.
- **Manual** (with `~/sistema/nvim-0.12.2`): open a file, edit, `:w` (see "written" in the strip),
  `/text` (highlight + search message), a multi-line `:echo` (see the strip grow), `G`/`gg`
  (scroll).

---

## Step 2 — Several files → several tabs (the core)

**What I touch:** `_feed_neovim_from_path`: the first path with `:edit`, from the second onward
`:tabedit <path>`. Each new tabpage = new window = **new grid** (observed grid 4 appear) → I
register a `TextDisplay` + a Qt tab for that grid.
- Lifecycle: on `tabedit` we get `grid_resize` + `win_pos` for the new grid and **`win_hide` for
  the previous one** (confirmed). On close, `win_close`/`grid_destroy`.
- Design note (confirmed): **tab ↔ tabpage**. Only the active tabpage is drawn; the others get
  `win_hide` and stay "frozen" until shown. For a tab UI, perfect.

**What I get:** `python -m nysor ex1.txt ex2.txt` → **two tabs**, each with its own content.

**How I test:**
- Manual: open two files, see two tabs with different contents.

---

## Step 3 — Tab switching both ways

**What I touch:**
- Tab change in Qt (`currentChanged`) → `nvim_set_current_tabpage` / `nvim_set_current_win` (using
  the `win` learned from `win_pos`).
- Autocmd `TabEnter`/`WinEnter` with `rpcnotify` → a `current_changed`-style notification → I
  update the active Qt tab without re-triggering the event (guard).
- In Neovim, `tabnext`/`tabprevious` fire `win_hide` for the outgoing one + `win_pos` for the
  incoming one (confirmed) — that already tells me which grid became active.

**What I get:** clicking a tab moves Neovim, and `gt`/`gT` in Neovim moves the Qt tab.

**How I test:**
- Manual: click tabs; use `gt`/`gT`; verify they match.

---

## Step 4 — Per-tab state (title, modified, scrollbars)

**What I touch:** today `state_buffer_is_modified`, `state_buffer_filepath`, the scrollbars, and
`adjust_viewport` are global. I move them into the registry record (**per grid/tab**). The
autocmds `BufModifiedSet`/`BufFilePost` start reporting which window/buffer changed. The
`Save`/`Open` menu is enabled based on the active tab. (Several `FIXME.90` land here.) I can also
use `win_viewport_margins` here if needed for the fine viewport computation.

**What I get:** each tab with its filename, its modified indicator, and its independent scroll.

**How I test:**
- Manual: modify one file → only its tab marked; scroll each one independently.

---

## Step 5 — Mouse with real `grid_id`

**What I touch:** in `text_display.py` the mouse events use a hardcoded `grid = 0` (FIXME.90).
Each `TextDisplay` gets to know its `grid_id` (from the registry) and sends it in
`nvim_input_mouse`.

**What I get:** clicking/selecting in a tab focuses and operates on the right window.

**How I test:**
- Manual: click in a tab positions the cursor there; drag selects there.

---

## Step 6 — `ext_cmdline`

**What I touch:** I add `ext_cmdline: True` to the attach. The command-line stops being drawn on
the message grid and starts arriving through events (confirmed on 0.12.2):
- `cmdline_show(content, pos, firstc, prompt, indent, level, hl_id)` — `content` is a list of
  chunks `[hl_id, text]`; `firstc` is `:`/`/`/`?`; `pos` is the cursor.
- `cmdline_pos(pos, level)`, `cmdline_hide(level, abort)`, `cmdline_special_char`,
  `cmdline_block_show/append/hide`.
I render that in a dedicated Qt widget (bottom bar). The **message grid still exists** for
`echo`/messages (confirmed: with ext_cmdline, `echo` still goes to the message grid), so the Step
1 strip stays; ext_cmdline only takes the cmdline out of it. This is exactly "with ext_cmdline but
without ext_messages".

**What I get:** `:`, `/`, `?`, `:%s/...` in a Qt command-line widget, with cursor and position.

**How I test:**
- Manual: `:w`, `/foo` with incsearch, `:%s/a/b/gc`; see it show in the Qt bar and no longer in
  the message strip.

---

## Step 7 — Detach a tab into a separate window (live side-by-side)

**What I touch:** in Qt, I reparent the tab's widget into a floating `QMainWindow`/`QDockWidget`
(+ re-attach). In Neovim, so that **both stay live at once**, I turn that case into a **split in
the same tabpage** (coexisting windows → both grids get drawn). Here `nvim_ui_try_resize_grid(grid,
cols, rows)` **does** come into play, to give each grid the size of the widget that holds it. I
handle the round trip back.

**What I get:** pull a buffer into its own OS window and edit both live, side by side.

**How I test:**
- Manual: detach a tab, edit in both windows simultaneously, re-attach.

---

## Cross-cutting notes

- **Ordering and delivery:** Steps 1–4 already give the requested feature ("open several, tabs,
  switch"). Step 5 is hygiene needed for multi-window. Step 6 (ext_cmdline) and Step 7 (detach)
  are improvements on top of a solid base.
- **Floating windows / completion popup:** they appear as transient grids via `win_float_pos`
  (observed with `:edit foo<Tab>`). Not drawn in the base; left as a future step (they fit well
  together with the floats handling of Step 7).
- **"New" menu:** the `FIXME.90` in `MainMenu` asks for a "New" option for multibuffer; it is
  added naturally in Step 2 or 4.
- **`ext_messages` is out of scope** (a lot more work and code); it is an optional future step,
  independent of everything above.
