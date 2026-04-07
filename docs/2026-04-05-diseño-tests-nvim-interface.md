# Test design for `nvim_interface.py`

## Goal

Cover all codepaths of `NvimInterface` and its helpers in
`nysor/nvim_interface.py`. The test file lives at `tests/test_nvim_interface.py`.


## General strategy

Rather than mocking the communication layer, `NeovimMock` implements a real Unix
socket server that speaks the msgpack-RPC protocol, just like neovim does. This
lets us test the full communication stack (serialization, parsing, callbacks)
without artificial stand-ins.

The only things that get mocked:

- `subprocess.Popen` → fake process with a configurable `poll()`.
- `NvimInterface._get_unique_sock_path` → returns the path where the mock is
  already listening.
- `NvimInterface._get_api_info` → silenced in most tests (see below).

Time control is handled by the `time-machine` library where needed (e.g. rate
limiting tests in `_receive_responses`), rather than patching `time.sleep` or
`time.time` directly.

Because the mock socket already exists on disk before `NvimInterface.__init__`
looks for it, `os.path.exists()` returns `True` naturally — no need to mock it.


## Added test dependencies

```toml
[project.optional-dependencies]
dev = [
    "pytest-asyncio",
    "pytest-mock",
    "time-machine",
    ...
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```


## `NeovimMock` class

Defined in the test file itself. Creates a real Unix socket server and exposes
methods to inject responses and read incoming requests.

```python
class NeovimMock:
    def __init__(self, tmp_path):
        self.sock_path = str(tmp_path / "nvim.sock")
        self.proc = MagicMock()
        self.proc.poll.return_value = None  # process alive by default
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.sock_path)
        self._server.listen(1)
        self._conn = None  # filled in accept()

    async def accept(self):
        """Accept the connection from NvimInterface (call after creating the interface)."""

    async def recv_request(self):
        """Read and parse the next RPC request sent by NvimInterface."""
        # returns (msgid, method, params)

    async def send_response(self, msgid, result=None, error=None):
        """Send an RPC response (type 1)."""

    async def send_notification(self, method, params):
        """Send an RPC notification (type 2)."""

    def exit(self, return_code=0):
        """Simulate the neovim process finishing."""
        self.proc.poll.return_value = return_code

    def close(self):
        """Close the server."""
```


## Base fixture

For most tests, `_get_api_info` is mocked so it does not interfere.

```python
@pytest.fixture
async def nvim(mocker, tmp_path):
    mock = NeovimMock(tmp_path)
    mocker.patch.object(NvimInterface, "_get_api_info")
    mocker.patch.object(NvimInterface, "_get_unique_sock_path", return_value=mock.sock_path)
    mocker.patch("nysor.nvim_interface.subprocess.Popen", return_value=mock.proc)
    loop = asyncio.get_event_loop()
    interface = NvimInterface("nvim", loop, notification_handler=MagicMock(), quit_callback=MagicMock())
    await mock.accept()
    yield interface, mock
    mock.close()
```

Tests for `_get_api_info` use an alternative fixture that does **not** mock that
method and configures the mock to respond to the `nvim_get_api_info` request.


## Test classes and covered codepaths

### `TestExtHook` (synchronous)

Pure function, no fixtures needed.

- Correct decoding of type and ID from big-endian bytes.
- Different type codes map to the right name in `_EXT_TYPE_CODES`.

### `TestGetUniqueSockPath` (synchronous)

- Returned path does not exist on disk.
- If the path already exists (mocking `os.path.exists`), retries and logs a warning.

### `TestNvimInterfaceInit`

- Normal startup: process launched, socket connected, reader registered, task created.
- `nvim_exec_path=None` defaults to `"nvim"`.
- `FileNotFoundError` from Popen raises `NeovimExecutableNotFound`.

### `TestNvimInterfaceGetApiInfo`

Uses the alternative fixture (no `_get_api_info` mock).

- `channel_id` and `nvim_version` are filled correctly from the response.
- `_EXT_TYPE_CODES` is populated with the types from the metadata.
- `setup_completed_event` is set after completion.

### `TestNvimInterfaceCall`

- Successful response: returns the correct result.
- Error response: raises `NeovimError` with the message.
- The assigned `msgid` matches the one in the received response.

### `TestNvimInterfaceRequest`

- Normal request: msgpack payload sent with type 0, msgid, method, params.
- `nvim_ui_attach` sets the `_ui_attached` flag.
- `nvim_ui_*` request before `nvim_ui_attach`: ignored, callback called with `None`.
- `nvim_ui_*` request after `nvim_ui_attach`: sent normally.
- `BrokenPipeError` during quit: silenced.
- `BrokenPipeError` outside of quit: re-raised.

### `TestNvimInterfaceQuit`

- `_neovim_already_finished` is True: returns immediately without sending anything.
- Normal quit: sends `nvim_command quit` + `nvim_input \r`, waits for `_quit_processed`.
- Quit with error from neovim: `eback` is called, the error is returned.
- `_quit_processed` is cleared after finishing (to allow a second quit attempt).

### `TestNvimInterfaceReceiveResponses`

Uses `time-machine` to control `time.time()` for rate limiting tests.

- Rate limiting: second call within `POLL_FREEZE_PERIOD` returns without reading.
- Type 1 message (response) with result: callback called, errback not.
- Type 1 message (response) with error: errback called with message, callback also called.
- Type 2 message (notification): `notification_handler` called with method and params.
- Unknown message type: error logged, no exception raised.
- Process finished (`poll()` returns a code): `_neovim_already_finished=True`,
  reader removed, `quit_callback` called, `_quit_processed` set.
- Process finished with pending messages: messages processed first, then shutdown.

### `TestNvimInterfaceFutureRequest`

- A task is created in the loop (synchronous, no result awaited).
- The task actually sends the request when the loop runs.

### `TestReadMessages`

- Socket returns valid data: yields the correct messages.
- Socket raises `BlockingIOError`: reading ends.
- Socket returns empty data: reading ends.
- Multiple messages in a single recv: all of them yielded.
