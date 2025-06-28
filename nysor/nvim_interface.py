# Copyright 2025 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

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
_SOCK_PATH = "/tmp/_nysorsubnvim.s"

# some Neovim translation constants
_EXT_TYPE_CODES = {
    1: "window",
    2: "buffer",
    3: "tabpage",
}

logger = logging.getLogger(__name__)
logging.addLevelName(5, "TRACE")


class NeovimError(Exception):
    """Exception that indicates an error from Neovim."""


def trace(msg, *params):
    """Log in trace level with a prefix."""
    logger.log(5, "[nvim] " + msg, *params)


def ext_hook(code, data):
    """Hook to process other external types."""
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
        self._quit_processed = asyncio.Event()
        self._quit_callback = quit_callback
        self._neovim_already_finished = False
        self._neovim_being_quited = False

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
        self._ui_attached = False

        self._msg_unpacker = msgpack.Unpacker(raw=False, ext_hook=ext_hook)

    async def quit(self):
        """Finish neovim and close the process.

        This is blocking on purpose, it normally should be fast but if it's not, it's a good idea
        to wait for it, maybe show more info at some point.

        Note that there is no real response to the 'quit' command, the process just disappears
        at some point.
        """
        if self._neovim_already_finished:
            return

        holder = [None]  # default, will remain like this if _quit_processed is set after ending OK

        def eback(message):
            """Error with the 'quit' command, pass the message."""
            holder[0] = message
            self._quit_processed.set()

        # this is a weird request; if all continues OK, Neovim will quit and a possible callback
        # is never called; however if there's a situation and Neovim can't quit, the errback
        # will be called
        self._neovim_being_quited = True
        await self._request(None, eback, "nvim_command", "quit")

        # sometimes the quit command needs an extra Enter, so send it!
        await self._request(None, None, "nvim_input", "\r")

        await self._quit_processed.wait()
        self._neovim_being_quited = False
        self._quit_processed.clear()  # for a possible next quitting attempt
        return holder[0]

    async def call(self, method, *params):
        """Call a method with the indicated parameters and wait until result is available."""
        holder = []
        event = asyncio.Event()

        def cback(result):
            holder.append(result)
            event.set()

        def eback(message):
            raise NeovimError(message)

        await self._request(cback, eback, method, *params)
        await event.wait()
        return holder[0]

    def future_request(self, method, *params):
        """Send a request in the future; response/error, if any, will be discarded."""
        self._loop.create_task(self._request(None, None, method, *params))

    async def _request(self, callback, errback, method, *params):
        """Send a 'request' message to run a method with some optional parameters.

        The callback will be executed when the information is available.
        """
        # filter out UI requests if UI still not attached
        if method == "nvim_ui_attach":
            self._ui_attached = True
        if method.startswith("nvim_ui") and not self._ui_attached:
            logger.debug("Ignoring request as UI still not attached; method: %r", method)

            # in case a callback is passed, just call it to close up waiters/coroutines; it's an
            # error condition, but the called method is doomed anyway
            if callback is not None:
                callback(None)
            return

        self._cb_counter += 1
        self._callbacks[self._cb_counter] = (callback, errback)

        # type (0 == request), msgid, method, params
        payload = msgpack.packb([0, self._cb_counter, method.encode("ascii"), params])
        trace("Sending request id=%d method=%r params=%s", self._cb_counter, method, params)
        try:
            await self._loop.sock_sendall(self._client, payload)
        except BrokenPipeError:
            if not self._neovim_being_quited and not self._neovim_already_finished:
                raise

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
                callback, errback = self._callbacks.pop(msgid)

                if error is not None:
                    logger.error("Error from Neovim: %r", error)
                    errback(error[1])
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
            self._neovim_already_finished = True
            self._loop.remove_reader(self._client)
            self._quit_callback()
            self._quit_processed.set()  # 'quit' processed satisfactorily: Neovim ended
