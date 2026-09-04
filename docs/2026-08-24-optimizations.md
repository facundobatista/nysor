# Editor responsiveness optimizations (plan)

Status: **not implemented yet** — design/plan only, to be picked up in a few days.
Date: 2026-08-24.

This document captures four planned optimizations discussed after the multi-window "rounding"
stage was finished. Nothing here is coded yet; the file references (`file:line`) are as of the date
above and may drift.

## Background: the hot path

`win_viewport` is a redraw sub-event Neovim emits on essentially **every cursor move / scroll**. Our
handler routes it to the owning pane:

- `NvimNotifications._n_redraw__win_viewport` (`nysor/nvim_notifications.py:536`) →
  `call_async(pane.adjust_viewport, topline, botline, line_count, curcol)` (`:542`).
- `EditorPane.adjust_viewport` (`nysor/main.py:454`) updates the scroll bars. Its **vertical** part
  is cheap (computed from the `win_viewport` args, no RPC — `main.py:465-474`). Its **horizontal**
  part is expensive: it makes **2-3 awaited RPC round-trips to Neovim per call**:
  1. `nvim_get_option_value "wrap"` — always (`main.py:477`).
  2. `nvim_eval` with `getbufline + map + strlen` over the visible lines — always unless wrapping
     (`main.py:495-496`).
  3. `nvim_eval winsaveview()` for `leftcol` — only when horizontally overflowing (`main.py:509`).

So today every `j`/`k`/`h`/`l` triggers 2-3 evals. It is fire-and-forget (does not block the UI
thread), but it floods Neovim and the scroll bar lags. Also, `call_async` launches a fresh task per
event, so rapid movement piles up concurrent tasks whose results can land **out of order** (a latent
bug).

Goal: bring **pure cursor movement to 0 horizontal RPC**, recompute line widths only when something
relevant actually changed, and never apply stale results.

Existing patterns to mirror: the autocmd → `rpcnotify` → `_h__<name>` handler flow already used for
`window_buffer` (`main.py:1398`) and `modified_changed` (`main.py:1412`); dispatch in
`NvimNotifications.handler` (`nvim_notifications.py:325`) via `getattr("_h__" + method)`. Per-window
state lives on `GridEntry` (`nvim_notifications.py:49`: `grid_id, pane, win_id, bufnr, filepath`),
written through `GridRegistry` setters (e.g. `set_buffer`, `nvim_notifications.py:125`).

---

## Plan 1 — cache `wrap` per window (2.1.1)

**Problem.** `wrap` is queried by RPC on every `adjust_viewport`, but it changes rarely. It is also
**window-local**, and the current query uses `{}` opts (the *current* window), which is subtly wrong
when adjusting a pane that is not current.

**Approach.** Cache `wrap` per window and keep it fresh with a notification, exactly like
`modified_changed`.

- Add a new field to `GridEntry`, e.g. `wrap: bool` (with a `GridRegistry.set_wrap(grid, value)`
  setter). Default it to Neovim's default (`wrap = True`) until told otherwise.
- Register an `OptionSet` autocmd scoped to the option, in `setup_nvim`:
  ```lua
  vim.api.nvim_create_autocmd('OptionSet', {
      pattern = 'wrap',
      callback = function()
          local win = vim.api.nvim_get_current_win()
          vim.rpcnotify(CHAN, 'window_wrap', win, vim.v.option_new)
      end,
  })
  ```
  `OptionSet` fires on `:set`/`:setlocal` and (in recent Neovim) on API sets; `vim.v.option_new`
  holds the new value.
- **Seed the initial value** so there is no window of wrong state before the first `OptionSet`:
  piggyback on the existing `window_buffer` autocmd (also send `vim.wo.wrap`), or send a `window_wrap`
  once from the same buffer-enter autocmds. Preferred: extend `window_buffer` to carry wrap.
- Add `_h__window_wrap(win_id, wrap)` in `NvimNotifications`: `entry = registry.get(win_id=win_id)`;
  if found, `registry.set_wrap(entry.grid_id, wrap)` (stash in a pending map if the grid is not known
  yet, mirroring `_pending_buffers`).

**Effect.** `adjust_viewport` reads `entry.wrap` — no RPC — and it is the correct per-window value.

---

## Plan 2 — gate `adjust_viewport` on real change (2.1.2)

**Problem.** The expensive horizontal work runs on every `win_viewport`, even when nothing that
affects the scroll bars changed. But we **cannot** cheaply skip on "range unchanged" alone: the line
width can change *within the same visible range* (e.g. `p` pastes 45 chars into the current line — no
vertical move, yet the horizontal scroll bar must shrink). Neovim never pushes line lengths (the
linegrid protocol only sends the **visible, truncated** cells), so we still have to query them — but
we can query far less often.

**Approach.** Keep the current body as `_adjust_viewport(...)` (unchanged: the full vertical +
horizontal work). Add a thin `adjust_viewport(...)` that bails out unless something relevant changed:

Relevant change = any of:
- `(topline, botline, line_count)` differs from last seen → vertical extent and/or visible range
  changed.
- `curcol` differs from last seen → the cursor may be **pushing the view left/right** (`leftcol`
  changes even with the same range and content); we must re-read `leftcol`. **This is the detail to
  not miss:** process `curcol` in the gate, not only `topline`/`botline`.
- `content_dirty` is set → a visible line's text changed (covers `p`, edits on lines above the
  cursor, substitutes that don't move the column, etc.).

Sketch:
```python
def adjust_viewport(self, topline, botline, line_count, curcol):
    if self.closed:
        return
    key = (topline, botline, line_count, curcol)
    if key == self._last_viewport and not self._content_dirty:
        return  # nothing that moves a scroll bar changed -> skip the RPCs
    self._last_viewport = key
    self._content_dirty = False
    # ... then run the full work (was adjust_viewport) ...
```
(This is where Plan 3's task management wraps the "run the full work" part.)

**Tracking `content_dirty`.** Add a `TextChanged` / `TextChangedI` autocmd →
`rpcnotify(CHAN, 'buffer_changed', win)`; `_h__buffer_changed(win_id)` maps `win → grid → pane` and
sets `pane._content_dirty = True`. It is a sticky flag cleared when the full `_adjust_viewport` runs.

**Note on `leftcol`.** The `winsaveview()` RPC for `leftcol` stays inside `_adjust_viewport` (only
reached on horizontal overflow), but with the gate it is now reached only when `curcol` actually
moved. An alternative — deriving `leftcol` from `curcol` + width — is **not** reliable (Neovim's
`sidescroll`/`sidescrolloff` behavior), so keep querying it.

**Effect.** Pure vertical/horizontal cursor movement with no content change and no view push → the
gate skips entirely. `p` is caught by `curcol` (cursor lands after the paste) and by `content_dirty`;
an edit on a visible line above the cursor is caught by `content_dirty`.

---

## Plan 3 — cancel the previous viewport task (latest-wins) (2.1.3)

**Problem.** `call_async(pane.adjust_viewport, ...)` spawns a new fire-and-forget task per
`win_viewport`. Under fast scrolling these overlap; because each awaits RPCs, their results can apply
**out of order**, leaving the scroll bar wrong.

**Approach.** "Latest wins": keep a per-pane task handle and cancel the pending one before launching
a new run. Prefer this over a debounce timer — it processes the newest event immediately (no added
latency) and drops the intermediates.

```python
# where win_viewport is dispatched (replaces the call_async today)
if pane._viewport_task and not pane._viewport_task.done():
    pane._viewport_task.cancel()
pane._viewport_task = asyncio.create_task(pane.adjust_viewport(...))
```

**Why it's safe.** The vertical scroll bar update runs **synchronously before the first `await`**, so
even a cancelled task leaves the vertical bar correct. The horizontal part is after the awaits; if
cancelled mid-flight, the newer task redoes it with fresh data. Cancellation raises `CancelledError`
at the `await` and the task ends cleanly (no catch needed; the newer task overwrites any partial
state). An `nvim_eval` already in flight still runs in Neovim (cheap) but its result is discarded —
which is exactly the point (no stale application).

**Interplay with Plan 2.** The cheap gate (Plan 2) runs *before* creating the task, so most events
never spawn a task at all; cancellation only matters for genuine bursts of real changes.

---

## Plan 4 — paint (2.2) — LOWER PRIORITY / deferred

Honest priority note: full-widget repaint is rarely the real bottleneck (Qt draws a few thousand
cells in a few ms); Plan 1-3 (the RPC storm) is the big win. Two levels here, both **deferred**:

**4a. Batch `setFont` (cheap, but explicitly deferred for now).** The foreground loop calls
`setItalic`/`setBold`/`setFont` **per character** (`nysor/text_display.py:550-552`). `setFont` per
char is the costliest part of the loop. Remember the last `(italic, bold)` and call `setFont` only
when it changes within a row. Real gain on fast typing, near-zero risk. *(Left out of the current
round per decision; keep as a quick future win.)*

**4b. Repaint only the dirty region — REJECTED (documented so we don't reconsider it lightly).**
Idea was: `flush()` → `self.update(dirty_band_rect)` instead of the whole widget
(`text_display.py:437`), and `paint()` clamps its `range(rows)` to `event.rect()`
(`text_display.py:495`), tracking a dirty band of rows widened by `write_grid` / `clear` / `scroll` /
`set_cursor` (old + new cursor rows).

Decision (2026-08-24): **not worth it.**
- Reward is small: full-widget repaint of a few thousand cells is sub-ms to a few ms; for this
  workload it is rarely the bottleneck (Plan 1-3 is the real responsiveness win).
- Risk is high and inherently coupled: every source of change must be tracked in motion
  (`write_grid`, `scroll`, `clear`, cursor old+new, resize, mode change = cursor shape, highlight
  changes); a single missed dirty region shows as **stale / ghost pixels**. It is all-or-nothing
  (`update(rect)` gains nothing unless `paint()` also clips), so it cannot be introduced gradually.
- The worst realistic case (huge maximized window + key-repeat scroll) is dominated by the per-char
  `setFont` (4a), not by `fillRect`/`drawText`. So **4a captures most of the achievable paint gain
  at ~zero risk**, likely enough even at 4K.

If paint ever *measurably* dominates on large windows, **profile first**; only then reconsider, and
eyeball-test carefully (GUI paths are not unit-tested by project policy).

---

## Suggested order

1. **Plan 1 + 2 + 3 together** (same method, they reinforce each other; this is the large win — pure
   cursor movement → 0 horizontal RPC, edits recompute once, no pile-up).
2. **Plan 4a** (`setFont` batch) — cheap, when convenient.
3. ~~Plan 4b (dirty-region)~~ — **rejected**; see 4b. Profile before ever reconsidering.

## Code reference index (as of 2026-08-24)

- `EditorPane.adjust_viewport` — `nysor/main.py:454` (wrap query `:477`, getbufline `:495`,
  winsaveview `:509`, vertical part `:465-474`, `self.closed` bail-outs throughout).
- `win_viewport` dispatch — `nysor/nvim_notifications.py:536-542`.
- Notification dispatch — `NvimNotifications.handler` `nvim_notifications.py:325`; per-message
  `_h__<method>`; pending-buffer stash pattern `_pending_buffers` (`_h__window_buffer`
  `nvim_notifications.py:312`).
- Autocmds to mirror/extend — `setup_nvim`: `window_buffer` `main.py:1398`, `modified_changed`
  `main.py:1412`.
- Per-window state — `GridEntry` `nvim_notifications.py:49`; setters on `GridRegistry`
  (`set_buffer` `nvim_notifications.py:125`, add a `set_wrap`).
- Paint — `TextDisplay.flush` `text_display.py:437`, `TextDisplay.paint` `text_display.py:495`,
  per-char `setFont` `text_display.py:550-552`.
