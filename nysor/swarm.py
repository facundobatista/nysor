import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)


DISCOVERY_PORT = 47890
PREFIX = b"PATH:"
PATH_IS_HERE = b"1"


class ListenerProtocol(asyncio.DatagramProtocol):
    def __init__(self, path_finder_callback):
        self.path_finder_callback = path_finder_callback
        self.prefix_len = len(PREFIX)

    def datagram_received(self, data, addr):
        """Process a received message."""
        if data[:self.prefix_len] == PREFIX:
            path = data[self.prefix_len:].decode("utf8")
            is_here = self.path_finder_callback(path)
            logger.debug("Swarm listener received a path to check: {!r} here={}", path, is_here)
            resp = PATH_IS_HERE if is_here else b"0"
            self.transport.sendto(resp, addr)
        else:
            logger.debug("Swarm listener; garbage? {!r}", data)

    def connection_made(self, transport):
        """Store the transport for a new connection."""
        self.transport = transport


class DiscoverProtocol(asyncio.DatagramProtocol):
    """Protocol to discover other swarm elements."""

    def __init__(self):
        self.responses = []

    def datagram_received(self, data, addr):
        """Register responses."""
        self.responses.append(data)
        print("===== resp", data)

    def error_received(self, exc):
        """Almost ignore errors."""
        logger.debug("Swarm discover protocol, error received: {!r}", exc)

    def connection_lost(self, exc):
        """Almost ignore connection problems."""
        logger.debug("Swarm discover protocol, connection lost {}", exc)


class Swarm:
    def __init__(self, loop):
        self.loop = loop
        self.pid = os.getpid()
        self.close_event = asyncio.Event()

    async def _listen(self, path_finder_callback):
        """Really listen other Nysors, answer back if we have this path."""
        transport, protocol = await self.loop.create_datagram_endpoint(
            lambda: ListenerProtocol(path_finder_callback),
            local_addr=("0.0.0.0", DISCOVERY_PORT),
            allow_broadcast=True,
            reuse_port=True,
        )

        try:
            logger.debug("Swarm listener up in {}", self.pid)
            await self.close_event.wait()
        finally:
            transport.close()

    def listen(self, path_finder_callback):
        """Listen other Nysors, answer back if we have this path."""
        self.loop.create_task(self._listen(path_finder_callback))

    def close(self):
        """Close internal structures."""
        self.close_event.set()

    async def discover(self, path):
        """Find out if the indicated path is open in other Nysor instance."""
        transport, protocol = await self.loop.create_datagram_endpoint(
            lambda: DiscoverProtocol(),  #FIXME: directamente?
            local_addr=("0.0.0.0", 0),
            allow_broadcast=True,
        )

        try:
            msg = PREFIX + path.encode("utf8")
            transport.sendto(msg, ("255.255.255.255", DISCOVERY_PORT))

            # as we don't know who will be answering we just need to wait; 200 ms is a LOT
            # of time for other processes to return, and barely noticeable for the human
            # in the startup
            await asyncio.sleep(0.2)
            responses = protocol.responses

            logger.debug("Swarm discover responses: {}", responses)
        finally:
            transport.close()

        return any(resp == PATH_IS_HERE for resp in responses)


async def main():
    if len(sys.argv) < 2:
        print("acts: listen | search")
        return

    act = sys.argv[1]
    swarm = Swarm()

    if act == "listen":
        await swarm.start_listener()
    elif act == "search":
        responses = await swarm.discover()
        print(f"Swarm {swarm.pid} arrancó ok, others: {responses}")
    else:
        print("acts: listen | search")


if __name__ == "__main__":
    asyncio.run(main())
