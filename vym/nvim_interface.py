"""Interface with the Neovim subprocess."""

import os
import subprocess
import socket
import time

import msgpack

SOCK_PATH = "/tmp/_vymsubnvim.s"

# FIXME: logger!


class NvimInterface:

    def __init__(self, loop, notification_handler):
        self.loop = loop

        if os.path.exists(SOCK_PATH):
            os.remove(SOCK_PATH)

        print("Starting nvim")
        self._nvim_proc = subprocess.Popen(["nvim", "--headless", "--listen", SOCK_PATH])
        tini = time.time()
        while True:
            if os.path.exists(SOCK_PATH):
                break
            print("    wait")
            # XXX: yes, we block; if we want to go fully async here we need to find out a better
            # way to ensure that nvim is really up
            time.sleep(.05)
        tdelta = time.time() - tini
        print(f"    started! it took {int(tdelta / 1000)} ms")

        print("Connecting")
        self._client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._client.setblocking(False)
        self._client.connect(SOCK_PATH)
        loop.add_reader(self._client, self._receive_responses)
        print("    done")

        self._callbacks = {}
        self._cb_counter = 0
        self._notif_handler = notification_handler

        self._msg_unpacker = msgpack.Unpacker(raw=False)

    async def request(self, callback, method, *params):
        """Send a 'request' message to run a method with some optional parameters.

        The callback will be executed when the information is available.
        """
        self._cb_counter += 1
        self._callbacks[self._cb_counter] = callback

        # type (0 == request), msgid, method, params
        payload = msgpack.packb([0, self._cb_counter, method.encode("ascii"), params])
        print(f"Sending request; msgid={self._cb_counter} method={method!r} params={params}")
        await self.loop.sock_sendall(self._client, payload)

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
        """Receive responses from the nvim process; unpack, log, and send payloads to callbacks."""
        print("Reading response")
        for msgtype, *rest in self._read_messages():
            if msgtype == 1:
                # response
                msgid, error, result = rest
                cr = "no" if result is None else "yes"
                ce = "no" if error is None else "yes"
                print(f"    msgid={msgid} result={cr} error={ce}")
                callback = self._callbacks.pop(msgid)

                if error is not None:
                    print(f"    ERROR, response: {error!r}")
                if callback is not None:
                    callback(result)

            elif msgtype == 2:
                # notification
                method, params = rest
                self._notif_handler(method, params)

            else:
                print(f"    ERROR, bad msg type: {msgtype!r}; rest={rest!r}")
