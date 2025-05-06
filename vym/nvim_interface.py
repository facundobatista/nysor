"""Interface with the Neovim subprocess."""

import asyncio
import os
import subprocess
import socket
import time

import msgpack

SOCK_PATH = "/tmp/_vymsubnvim.s"

# FIXME: logger!


class NvimInterface:
    """Interface with a Neovim process through RPC on a file socket."""

    def __init__(self, loop, notification_handler, quit_callback):
        self._loop = loop
        self._notif_handler = notification_handler

        # the event is to wait for process finalization when we receive the order to close it; at
        # all times the callback is called, as the finalization may be initiated by Neovim itself
        self._quit_event = asyncio.Event()
        self._quit_callback = quit_callback

        if os.path.exists(SOCK_PATH):
            os.remove(SOCK_PATH)

        print("Starting nvim")  # info
        self._proc = subprocess.Popen(["nvim", "--headless", "--listen", SOCK_PATH])
        tini = time.time()
        while True:
            if os.path.exists(SOCK_PATH):
                break
            print("    wait")  # debug
            # yes, we block; if we want to go fully async here we need to find out a better
            # way to ensure that nvim is really up
            time.sleep(.05)
        tdelta = time.time() - tini
        print(f"    started! it took {int(tdelta / 1000)} ms")  # debug

        self._client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._client.setblocking(False)
        self._client.connect(SOCK_PATH)
        self._loop.add_reader(self._client, self._receive_responses)
        print("    done")  # info

        self._callbacks = {}
        self._cb_counter = 0

        self._msg_unpacker = msgpack.Unpacker(raw=False)

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

    async def request(self, callback, method, *params):
        """Send a 'request' message to run a method with some optional parameters.

        The callback will be executed when the information is available.
        """
        self._cb_counter += 1
        self._callbacks[self._cb_counter] = callback

        # type (0 == request), msgid, method, params
        payload = msgpack.packb([0, self._cb_counter, method.encode("ascii"), params])
        print(f"Sending request; msgid={self._cb_counter} method={method!r} params={params}")  # d
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
        print("Reading response; rc", return_code)  # debug

        for msgtype, *rest in self._read_messages():
            if msgtype == 1:
                # response
                msgid, error, result = rest
                cr = "no" if result is None else "yes"
                ce = "no" if error is None else "yes"
                print(f"    msgid={msgid} result={cr} error={ce}")  # debug
                callback = self._callbacks.pop(msgid)

                if error is not None:
                    print(f"    ERROR, response: {error!r}")  # error
                if callback is not None:
                    callback(result)

            elif msgtype == 2:
                # notification
                method, params = rest
                self._notif_handler(method, params)

            else:
                print(f"    ERROR, bad msg type: {msgtype!r}; rest={rest!r}")  # error

        if return_code is not None:
            # process finished; do this closing at the end of the reading in any case there is a
            # final response to read
            self._loop.remove_reader(self._client)
            self._quit_callback()
            self._quit_event.set()
