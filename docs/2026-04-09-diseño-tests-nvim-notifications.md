# Test design for `nvim_notifications.py`

## Goal

Cover all codepaths of `DynamicCache` and `NvimNotifications` in
`nysor/nvim_notifications.py`. The test file lives at
`tests/test_nvim_notifications.py`.


## General strategy

Both classes are fully synchronous, so all tests are plain `def` functions —
no async needed.

`NvimNotifications` depends on two collaborators (`main_window` and
`text_display`) that are always `MagicMock`. `call_async` is patched at the
module level so that `_n_redraw__win_viewport` can be tested without a running
event loop.

`DynamicCache` is self-contained and needs no mocks.


## Base fixture

```python
@pytest.fixture
def notif(mocker):
    mocker.patch("nysor.nvim_notifications.call_async")
    nn = NvimNotifications(main_window=MagicMock())
    nn.text_display = MagicMock()
    return nn
```

Tests that need to assert on `call_async` calls can access the mock via
`mocker` directly, or the fixture can expose it explicitly if needed.


## Test classes and covered codepaths

### `TestDynamicCache`

- `get` on empty cache returns `None`.
- `set` then `get` returns the stored value.
- `get` with different labels returns `None` (caches are independent).
- `clean` clears all caches that include the given label.
- `clean` with a label shared across multiple label-sets clears all of them.
- `clean` with an unknown label does nothing.
- After `clean`, previously stored values under that label-set are gone but
  other label-sets are untouched.

### `TestNvimNotificationsHandler`

Tests for `handler()`.

- Known method is dispatched correctly (calls the right `_h__*` method).
- Unknown method logs an error and returns without raising.

### `TestNvimNotificationsRedraw`

Tests for `_h__redraw()`.

- Known submethod is dispatched correctly (calls the right `_n_redraw__*`
  method).
- Unknown submethod logs an error and continues (does not raise).
- Exception inside a submethod is caught, logged, and execution continues with
  the next submethod.
- Multiple submethods in one call are all dispatched in order.

### `TestNvimNotificationsHandlers`

Tests for the non-redraw `_h__*` handlers.

- `_h__modified_changed`: calls `main_window.set_buffer_state(is_modified=...)`.
- `_h__filepath_changed`: calls `main_window.set_buffer_state(filepath=...)`.

### `TestNvimNotificationsRedrawHandlers`

One sub-group per `_n_redraw__*` method. All use the `notif` fixture.

- `default_colors_set`: updates `structs["default_colors"]` with fg/bg/sp;
  calls `dyncache.clean("default_colors")`.
- `flush`: calls `text_display.flush()`.
- `grid_clear`: calls `text_display.clear()`.
- `grid_cursor_goto`: calls `text_display.set_cursor(row, col)`.
- `grid_line` (single item): calls `text_display.write_grid(row, col_start,
  cells)`.
- `grid_line` (multiple items): calls `text_display.write_grid` once per item.
- `grid_resize`: calls `text_display.resize_view((width, height))`.
- `grid_scroll`: calls `text_display.scroll((top, bottom, rows), (left, right,
  cols))`.
- `hl_attr_define`: stores rgb attrs in `structs["hl-attrs"]`; calls
  `dyncache.clean("hl-attrs")`.
- `hl_group_set`: stores group→id mapping in `structs["hl-groups"]`; calls
  `dyncache.clean("hl-groups")`.
- `mode_change`: looks up mode in `structs["mode-info"]` and calls
  `text_display.change_mode(mode_info)`.
- `mode_info_set`: populates `structs["mode-info"]` (strips `name` and
  `short_name`); calls `dyncache.clean("mode-info")`.
- `mouse_on`: does not raise.
- `mouse_off`: does not raise.
- `option_set` without `guifont`: updates `self.options`, does not call
  `text_display.set_font`.
- `option_set` with `guifont`: updates `self.options` and calls
  `text_display.set_font(name, size)`.
- `set_icon` with empty icon: does not log a warning.
- `set_icon` with a non-empty icon: logs a warning.
- `set_title`: calls `main_window.setWindowTitle(title)`.
- `win_viewport`: calls `call_async` with `main_window.adjust_viewport` and
  the correct positional arguments.
