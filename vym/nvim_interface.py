"""Interface with the Neovim subprocess."""

import asyncio
import logging
import os
import subprocess
import socket
import time

import msgpack

# the socket path to communicate with Neovim
# FIXME: make this more unique
_SOCK_PATH = "/tmp/_vymsubnvim.s"

# some Neovim translation constants
_EXT_TYPE_CODES = {
    1: "window",
    2: "buffer",
    3: "tabpage",
}

logger = logging.getLogger(__name__)
logging.addLevelName(5, "TRACE")


def trace(msg, *params):
    """Log in trace level with a prefix."""
    logger.log(5, "[nvim] " + msg, *params)


def ext_hook(code, data):
    """Hook to process other external types."""
    trace("====== ext hook! %r %r", code, data)
    # code is the type of object
    obj_type = _EXT_TYPE_CODES[code]

    # Neovim encodes IDs as uint16 or uint32
    obj_id = int.from_bytes(data, byteorder='big')

    return [obj_type, obj_id]


class NvimInterface:
    """Interface with a Neovim process through RPC on a file socket."""

    def __init__(self, nvim_exec_path, loop, notification_handler, quit_callback):
        self._loop = loop
        self._notif_handler = notification_handler

        # the event is to wait for process finalization when we receive the order to close it; at
        # all times the callback is called, as the finalization may be initiated by Neovim itself
        self._quit_event = asyncio.Event()
        self._quit_callback = quit_callback

        if os.path.exists(_SOCK_PATH):
            os.remove(_SOCK_PATH)

        logger.info("Starting Neovim process")
        if nvim_exec_path is None:
            nvim_exec_path = "nvim"
        self._proc = subprocess.Popen([nvim_exec_path, "--headless", "--listen", _SOCK_PATH])
        tini = time.time()
        while True:
            if os.path.exists(_SOCK_PATH):
                break
            logger.debug("Wait nvim process to start")
            # yes, we block; if we want to go fully async here we need to find out a better
            # way to ensure that nvim is really up
            time.sleep(.05)
        tdelta = time.time() - tini
        logger.debug("Neovim process started! it took %d ms", int(tdelta / 1000))

        self._client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._client.setblocking(False)
        self._client.connect(_SOCK_PATH)
        self._loop.add_reader(self._client, self._receive_responses)
        logger.info("Neovim setup done")

        self._callbacks = {}
        self._cb_counter = 0

        self._msg_unpacker = msgpack.Unpacker(raw=False, ext_hook=ext_hook)

    async def quit(self):
        """Finish neovim and close the process.

        This is blocking on purpose, it normally should be fast but if it's not, it's a good idea
        to wait for it, maybe show more info at some point.

        Note that there is no real response to the 'quit' command, the process just disappears
        at some point.
        """
        if self._quit_event.is_set():
            # neovim process already finished
            return

        await self.request(None, "nvim_command", "quit")
        await self._quit_event.wait()

    def future_call(self, method, *params):
        """Call a method with the indicated parameters and wait until result is available."""
        self._loop.create_task(self.call(method, *params))

    async def call(self, method, *params):
        """Call a method with the indicated parameters."""
        holder = []
        event = asyncio.Event()

        def cback(result):
            holder.append(result)
            event.set()

        await self.request(cback, method, *params)
        await event.wait()
        return holder[0]

    async def request(self, callback, method, *params):
        """Send a 'request' message to run a method with some optional parameters.

        The callback will be executed when the information is available.
        """
        self._cb_counter += 1
        self._callbacks[self._cb_counter] = callback

        # type (0 == request), msgid, method, params
        payload = msgpack.packb([0, self._cb_counter, method.encode("ascii"), params])
        trace("Sending request id=%d method=%r params=%s", self._cb_counter, method, params)
        await self._loop.sock_sendall(self._client, payload)

    def _read_messages(self):
        """Get messages from nvim."""
        while True:
            try:
                data = self._client.recv(4096)
            except BlockingIOError:
                data = ""
            if not data:
                break

            self._msg_unpacker.feed(data)
            for msg in self._msg_unpacker:
                yield msg

    def _receive_responses(self):
        """Receive responses from the nvim process; unpack, log, and send payloads to callbacks.

        Here is where we check if the process is still running, as it allows a frequent
        verification.
        """
        return_code = self._proc.poll()
        logger.debug("Reading response; rc %s", return_code)

        for msgtype, *rest in self._read_messages():
            if msgtype == 1:
                # response
                msgid, error, result = rest
                trace("Receiving response msgid=%d error=%r result=%r", msgid, error, result)
                callback = self._callbacks.pop(msgid)

                if error is not None:
                    logger.error("Error from Neovim: %r", error)
                if callback is not None:
                    callback(result)

            elif msgtype == 2:
                # notification
                method, params = rest
                trace("Receiving notification method=%r params=%r", method, params)
                self._notif_handler(method, params)

            else:
                logger.error("Bad message type from nvim: %r (rest=%r)", msgtype, rest)

        if return_code is not None:
            # process finished; do this closing at the end of the reading in any case there is a
            # final response to read
            logger.info("Neovim process finished")
            self._loop.remove_reader(self._client)
            self._quit_callback()
            self._quit_event.set()
