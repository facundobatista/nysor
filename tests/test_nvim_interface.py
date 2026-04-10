# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Tests for nysor/nvim_interface.py."""

import asyncio
import os
import socket
import uuid
from unittest.mock import MagicMock

import msgpack
import pytest
import time_machine

from nysor.nvim_interface import (
    POLL_FREEZE_PERIOD,
    NvimInterface,
    NeovimError,
    NeovimExecutableNotFound,
    _EXT_TYPE_CODES,
    ext_hook,
)


# ---------------------------------------------------------------------------
# NeovimMock
# ---------------------------------------------------------------------------

class NeovimMock:
    """Real Unix socket server speaking msgpack-RPC, for testing NvimInterface."""

    def __init__(self, sock_path):
        self.sock_path = sock_path
        self.proc = MagicMock()
        self.proc.poll.return_value = None
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self.sock_path)
        self._server.listen(1)
        self._conn = None
        self._unpacker = msgpack.Unpacker(raw=False)

    async def accept(self):
        """Accept the connection from NvimInterface."""
        loop = asyncio.get_event_loop()
        self._server.setblocking(False)
        self._conn, _ = await loop.sock_accept(self._server)

    async def recv_request(self):
        """Read and parse the next RPC request sent by NvimInterface.

        Returns (msgid, method, params).
        """
        loop = asyncio.get_event_loop()
        while True:
            for msg in self._unpacker:
                _msgtype, msgid, method, params = msg
                if isinstance(method, bytes):
                    method = method.decode()
                return msgid, method, params
            data = await loop.sock_recv(self._conn, 4096)
            self._unpacker.feed(data)

    async def send_response(self, msgid, result=None, error=None):
        """Send an RPC response (type 1) to NvimInterface."""
        loop = asyncio.get_event_loop()
        data = msgpack.packb([1, msgid, error, result])
        await loop.sock_sendall(self._conn, data)

    async def send_notification(self, method, params):
        """Send an RPC notification (type 2) to NvimInterface."""
        loop = asyncio.get_event_loop()
        data = msgpack.packb([2, method, params])
        await loop.sock_sendall(self._conn, data)

    async def send_raw(self, data):
        """Send raw bytes to NvimInterface."""
        loop = asyncio.get_event_loop()
        await loop.sock_sendall(self._conn, data)

    def exit(self, return_code=0):
        """Simulate the neovim process finishing."""
        self.proc.poll.return_value = return_code

    def close(self):
        """Close the server and any open connections."""
        if self._conn is not None:
            self._conn.close()
        self._server.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_API_RESPONSE = [
    42,  # channel_id
    {
        "version": {"major": 0, "minor": 10, "patch": 0},
        "types": {
            "Buffer": {"id": 0, "prefix": "nvim_buf_"},
            "Window": {"id": 1, "prefix": "nvim_win_"},
        },
    },
]


@pytest.fixture
def sock_path():
    """Provide a short socket path and clean it up after the test.

    We need to do this manually as if we rely on tmp_path the result socket path is
    too long for what is allowed in MacOS.
    """
    path = f".tmptest-{uuid.uuid4().hex[:8]}.s"
    try:
        yield path
    finally:
        if os.path.exists(path):
            os.unlink(path)


@pytest.fixture
async def nvim(mocker, sock_path):
    """NvimInterface connected to a NeovimMock, with _get_api_info mocked out."""
    mock = NeovimMock(sock_path)
    mocker.patch.object(NvimInterface, "_get_api_info")
    mocker.patch.object(NvimInterface, "_get_unique_sock_path", return_value=mock.sock_path)
    mocker.patch("nysor.nvim_interface.subprocess.Popen", return_value=mock.proc)
    loop = asyncio.get_event_loop()
    interface = NvimInterface(
        "nvim", loop, notification_handler=MagicMock(), quit_callback=MagicMock())
    await mock.accept()
    yield interface, mock
    mock.close()


@pytest.fixture
async def nvim_with_api(mocker, sock_path):
    """NvimInterface with real _get_api_info; mock responds to nvim_get_api_info."""
    mock = NeovimMock(sock_path)
    mocker.patch.dict(_EXT_TYPE_CODES, {}, clear=True)
    mocker.patch.object(NvimInterface, "_get_unique_sock_path", return_value=mock.sock_path)
    mocker.patch("nysor.nvim_interface.subprocess.Popen", return_value=mock.proc)
    loop = asyncio.get_event_loop()
    interface = NvimInterface(
        "nvim", loop, notification_handler=MagicMock(), quit_callback=MagicMock())
    await mock.accept()
    msgid, method, _ = await mock.recv_request()
    assert method == "nvim_get_api_info"
    await mock.send_response(msgid, result=_API_RESPONSE)
    await interface.setup_completed_event.wait()
    yield interface, mock
    mock.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExtHook:

    def test_basic(self, mocker):
        """Decode type and ID from big-endian bytes."""
        mocker.patch.dict(_EXT_TYPE_CODES, {7: "Buffer"})
        assert ext_hook(7, b'\x00\x01') == ["Buffer", 1]

    def test_different_type_codes(self, mocker):
        """Different type codes map to the correct object type names."""
        mocker.patch.dict(_EXT_TYPE_CODES, {1: "Window", 2: "Tabpage"})
        assert ext_hook(1, b'\x00\x05') == ["Window", 5]
        assert ext_hook(2, b'\x00\x0a') == ["Tabpage", 10]

    def test_multi_byte_id(self, mocker):
        """Multi-byte big-endian IDs are decoded correctly."""
        mocker.patch.dict(_EXT_TYPE_CODES, {0: "Buffer"})
        assert ext_hook(0, b'\x01\x00') == ["Buffer", 256]


class TestGetUniqueSockPath:

    def test_returns_nonexistent_path(self):
        """Returned path follows the expected format and does not exist on disk."""
        path = NvimInterface._get_unique_sock_path(None)
        assert path.startswith("/tmp/nysor-subnvim-")
        assert path.endswith(".s")
        assert not os.path.exists(path)

    def test_retries_on_collision(self, mocker, logs):
        """Retries and logs a warning when the generated path already exists."""
        mocker.patch("nysor.nvim_interface.os.path.exists", side_effect=[True, False])
        NvimInterface._get_unique_sock_path(None)
        assert "Found an unique path already in disk" in logs.warning


class TestNvimInterfaceInit:

    async def test_normal_startup(self, mocker, sock_path):
        """Process is launched with the correct arguments on normal startup."""
        mock = NeovimMock(sock_path)
        mocker.patch.object(NvimInterface, "_get_api_info")
        mocker.patch.object(NvimInterface, "_get_unique_sock_path", return_value=mock.sock_path)
        popen_mock = mocker.patch("nysor.nvim_interface.subprocess.Popen", return_value=mock.proc)
        loop = asyncio.get_event_loop()
        NvimInterface("nvim", loop, MagicMock(), MagicMock())
        await mock.accept()
        popen_mock.assert_called_once_with(["nvim", "--headless", "--listen", mock.sock_path])
        mock.close()

    async def test_default_exec_path(self, mocker, sock_path):
        """None as exec path defaults to 'nvim'."""
        mock = NeovimMock(sock_path)
        mocker.patch.object(NvimInterface, "_get_api_info")
        mocker.patch.object(NvimInterface, "_get_unique_sock_path", return_value=mock.sock_path)
        popen_mock = mocker.patch("nysor.nvim_interface.subprocess.Popen", return_value=mock.proc)
        loop = asyncio.get_event_loop()
        NvimInterface(None, loop, MagicMock(), MagicMock())
        await mock.accept()
        assert popen_mock.call_args[0][0][0] == "nvim"
        mock.close()

    def test_executable_not_found(self, mocker, sock_path):
        """FileNotFoundError from Popen raises NeovimExecutableNotFound."""
        mock = NeovimMock(sock_path)
        mocker.patch.object(NvimInterface, "_get_unique_sock_path", return_value=mock.sock_path)
        mocker.patch("nysor.nvim_interface.subprocess.Popen", side_effect=FileNotFoundError)
        with pytest.raises(NeovimExecutableNotFound):
            NvimInterface("bad_nvim", MagicMock(), MagicMock(), MagicMock())
        mock.close()


class TestNvimInterfaceGetApiInfo:

    async def test_fills_channel_and_version(self, nvim_with_api):
        """channel_id and nvim_version are populated from the API response."""
        interface, _ = nvim_with_api
        assert interface.channel_id == 42
        assert interface.nvim_version == "0.10.0"

    async def test_fills_ext_type_codes(self, nvim_with_api):
        """_EXT_TYPE_CODES is populated with types from the API metadata."""
        assert _EXT_TYPE_CODES[0] == "Buffer"
        assert _EXT_TYPE_CODES[1] == "Window"

    async def test_setup_completed_event_is_set(self, nvim_with_api):
        """setup_completed_event is set after completing API info retrieval."""
        interface, _ = nvim_with_api
        assert interface.setup_completed_event.is_set()


class TestNvimInterfaceCall:

    async def test_successful_response(self, nvim):
        """Returns the result from a successful RPC response."""
        interface, mock = nvim

        async def respond():
            msgid, _, _ = await mock.recv_request()
            await mock.send_response(msgid, result="hello")

        asyncio.create_task(respond())
        result = await interface.call("some_method", "arg1")
        assert result == "hello"

    async def test_error_response(self, nvim):
        """Raises NeovimError when the response contains an error."""
        interface, mock = nvim

        async def respond():
            msgid, _, _ = await mock.recv_request()
            await mock.send_response(msgid, error=[0, "something went wrong"])

        asyncio.create_task(respond())
        with pytest.raises(NeovimError, match="something went wrong"):
            await interface.call("some_method")

    async def test_correct_method_and_params_sent(self, nvim):
        """The correct method name and parameters are sent in the request."""
        interface, mock = nvim

        async def respond():
            msgid, method, params = await mock.recv_request()
            await mock.send_response(msgid, result=None)
            return method, params

        task = asyncio.create_task(respond())
        await interface.call("nvim_buf_get_name", 1, 2)
        method, params = await task
        assert method == "nvim_buf_get_name"
        assert params == [1, 2]


class TestNvimInterfaceRequest:

    async def test_sends_correct_payload(self, nvim):
        """Request is sent with the correct method and parameters."""
        interface, mock = nvim
        asyncio.create_task(interface._request(None, None, "test_method", "arg1", 2))
        msgid, method, params = await mock.recv_request()
        assert method == "test_method"
        assert params == ["arg1", 2]

    async def test_ui_attach_sets_flag(self, nvim):
        """nvim_ui_attach request sets the _ui_attached flag."""
        interface, mock = nvim
        assert not interface._ui_attached
        asyncio.create_task(interface._request(None, None, "nvim_ui_attach", 80, 24, {}))
        await mock.recv_request()
        assert interface._ui_attached

    async def test_ui_request_before_attach_ignored(self, nvim):
        """nvim_ui_* request before attach is ignored; callback is called with None."""
        interface, mock = nvim
        callback = MagicMock()
        await interface._request(callback, None, "nvim_ui_try_resize", 80, 24)
        callback.assert_called_once_with(None)

    async def test_ui_request_after_attach_sent(self, nvim):
        """nvim_ui_* request after attach is sent normally."""
        interface, mock = nvim
        interface._ui_attached = True
        asyncio.create_task(interface._request(None, None, "nvim_ui_try_resize", 80, 24))
        _, method, _ = await mock.recv_request()
        assert method == "nvim_ui_try_resize"

    async def test_broken_pipe_during_quit_silenced(self, nvim, mocker):
        """BrokenPipeError while quitting is silenced."""
        interface, _ = nvim
        interface._neovim_being_quited = True
        mocker.patch.object(interface._loop, "sock_sendall", side_effect=BrokenPipeError)
        await interface._request(None, None, "some_method")  # must not raise

    async def test_broken_pipe_outside_quit_raised(self, nvim, mocker):
        """BrokenPipeError outside of a quit sequence is re-raised."""
        interface, _ = nvim
        mocker.patch.object(interface._loop, "sock_sendall", side_effect=BrokenPipeError)
        with pytest.raises(BrokenPipeError):
            await interface._request(None, None, "some_method")


class TestNvimInterfaceQuit:

    async def test_already_finished(self, nvim):
        """Returns immediately without sending anything if neovim has already finished."""
        interface, _ = nvim
        interface._neovim_already_finished = True
        result = await interface.quit()
        assert result is None

    async def test_normal_quit(self, nvim):
        """Sends quit command and Enter, then waits for process to finish."""
        interface, mock = nvim
        quit_task = asyncio.create_task(interface.quit())

        msgid1, method1, params1 = await mock.recv_request()
        msgid2, method2, params2 = await mock.recv_request()
        assert method1 == "nvim_command"
        assert params1 == ["quit"]
        assert method2 == "nvim_input"
        assert params2 == ["\r"]

        mock.exit(0)
        interface._receive_responses()
        result = await quit_task
        assert result is None

    async def test_quit_error(self, nvim):
        """Returns the error message when neovim responds with an error."""
        interface, mock = nvim
        quit_task = asyncio.create_task(interface.quit())

        msgid, _, _ = await mock.recv_request()  # nvim_command quit
        await mock.recv_request()                 # nvim_input \r
        await mock.send_response(msgid, error=[0, "E37: No write since last change"])
        await asyncio.sleep(0)
        result = await quit_task
        assert result == "E37: No write since last change"

    async def test_quit_processed_cleared_for_retry(self, nvim):
        """_quit_processed is cleared after quit to allow future retries."""
        interface, mock = nvim
        quit_task = asyncio.create_task(interface.quit())
        await mock.recv_request()
        await mock.recv_request()
        mock.exit(0)
        interface._receive_responses()
        await quit_task
        assert not interface._quit_processed.is_set()


class TestNvimInterfaceReceiveResponses:

    @time_machine.travel(1000000, tick=False)
    async def test_rate_limit_blocks_fast_call(self, nvim, mocker):
        """Second call within POLL_FREEZE_PERIOD is skipped."""
        interface, _ = nvim
        mock_read = mocker.patch.object(interface, "_read_messages", return_value=iter([]))
        interface.last_poll_timestamp = 1000000 - 0.001  # within freeze period
        interface._receive_responses()
        mock_read.assert_not_called()

    @time_machine.travel(1000000, tick=False)
    async def test_rate_limit_allows_after_period(self, nvim, mocker):
        """Call after POLL_FREEZE_PERIOD is processed normally."""
        interface, _ = nvim
        mock_read = mocker.patch.object(interface, "_read_messages", return_value=iter([]))
        interface.last_poll_timestamp = 1000000 - POLL_FREEZE_PERIOD - 0.001
        interface._receive_responses()
        mock_read.assert_called_once()

    async def test_response_result_calls_callback(self, nvim, mocker):
        """Type 1 message with a result calls the callback; errback is not called."""
        interface, _ = nvim
        callback, errback = MagicMock(), MagicMock()
        interface._callbacks[1] = (callback, errback)
        mocker.patch.object(
            interface, "_read_messages", return_value=iter([[1, 1, None, "value"]]))
        interface._receive_responses()
        callback.assert_called_once_with("value")
        errback.assert_not_called()

    async def test_response_error_calls_errback_and_callback(self, nvim, mocker):
        """Type 1 message with an error calls both errback and callback."""
        interface, _ = nvim
        callback, errback = MagicMock(), MagicMock()
        interface._callbacks[1] = (callback, errback)
        mocker.patch.object(
            interface, "_read_messages",
            return_value=iter([[1, 1, [0, "something broke"], None]]))
        interface._receive_responses()
        errback.assert_called_once_with("something broke")
        callback.assert_called_once_with(None)

    async def test_notification_calls_handler(self, nvim, mocker):
        """Type 2 message (notification) calls the notification handler."""
        interface, _ = nvim
        mocker.patch.object(
            interface, "_read_messages", return_value=iter([[2, "nvim_event", [1, 2]]]))
        interface._receive_responses()
        interface._notif_handler.assert_called_once_with("nvim_event", [1, 2])

    async def test_unknown_type_logged(self, nvim, mocker, logs):
        """Unknown message type logs an error and does not raise."""
        interface, _ = nvim
        mocker.patch.object(
            interface, "_read_messages", return_value=iter([[99, "unknown"]]))
        interface._receive_responses()
        assert "Bad message type" in logs.error

    async def test_process_exit_triggers_quit(self, nvim, mocker):
        """Process finishing sets _neovim_already_finished and calls quit_callback."""
        interface, mock = nvim
        mocker.patch.object(interface, "_read_messages", return_value=iter([]))
        mock.exit(0)
        interface._receive_responses()
        assert interface._neovim_already_finished
        interface._quit_callback.assert_called_once()
        assert interface._quit_processed.is_set()

    async def test_process_exit_processes_messages_first(self, nvim, mocker):
        """Pending messages are processed before handling the process exit."""
        interface, mock = nvim
        callback = MagicMock()
        interface._callbacks[1] = (callback, None)
        mocker.patch.object(
            interface, "_read_messages",
            return_value=iter([[1, 1, None, "last_msg"]]))
        mock.exit(0)
        interface._receive_responses()
        callback.assert_called_once_with("last_msg")
        assert interface._neovim_already_finished


class TestNvimInterfaceFutureRequest:

    async def test_creates_task(self, nvim, mocker):
        """Creates a task in the loop without waiting for the result."""
        interface, _ = nvim
        create_task_spy = mocker.spy(interface._loop, "create_task")
        interface.future_request("some_method", "arg1")
        create_task_spy.assert_called_once()

    async def test_request_is_sent(self, nvim):
        """The request is actually sent after the task executes."""
        interface, mock = nvim
        interface.future_request("some_method", "arg1")
        _, method, params = await mock.recv_request()
        assert method == "some_method"
        assert params == ["arg1"]


class TestReadMessages:

    async def test_yields_all_messages_from_single_recv(self, nvim):
        """All msgpack messages packed in one chunk are yielded."""
        interface, mock = nvim
        data = b"".join(msgpack.packb([1, i, None, f"r{i}"]) for i in range(3))
        await mock.send_raw(data)
        await asyncio.sleep(0)
        messages = list(interface._read_messages())
        assert messages == [[1, i, None, f"r{i}"] for i in range(3)]

    async def test_blocking_io_ends_reading(self, nvim):
        """BlockingIOError from the socket terminates reading (no data case)."""
        interface, _ = nvim
        messages = list(interface._read_messages())
        assert messages == []

    async def test_empty_data_ends_reading(self, nvim):
        """Empty data from a closed connection terminates reading."""
        interface, mock = nvim
        mock._conn.close()
        await asyncio.sleep(0.05)
        messages = list(interface._read_messages())
        assert messages == []
