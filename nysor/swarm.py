# Copyright 2026 Facundo Batista
# Licensed under the Apache v2 License
# For further info, check https://github.com/facundobatista/nysor

"""Handle all the interprocess communication."""

import asyncio
import logging
import os
import socket
import struct

logger = logging.getLogger(__name__)

# unique port to dialog between swarm elements
DISCOVERY_PORT = 47890

# this prefix is all the requests we have, yet we laid foundations for any possible grow; so far
# it's simple and static (version 0, command PATH), in the future it may grow other commands, or
# increase the number for completely different conversations
PREFIX = b"0:PATH:"
PATH_NOT_FOUND = b"-"

# multicast group used only inside this machine.
DISCOVERY_GROUP = "239.255.0.1"
LOOPBACK = "127.0.0.1"


def create_multicast_listener_socket():
    """Create a UDP socket that receives every packet sent to our multicast group."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    # allow multiple processes on the same machine to bind the same multicast port
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    sock.bind(("", DISCOVERY_PORT))

    # join the multicast group on loopback
    membership = struct.pack("4s4s", socket.inet_aton(DISCOVERY_GROUP), socket.inet_aton(LOOPBACK))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)

    return sock


def create_multicast_sender_socket():
    """Create a UDP socket that sends multicast packets through loopback."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    # make multicast packets sent by this socket visible to listeners on the same machine.
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)

    # explicitly use loopback as the outgoing multicast interface.
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(LOOPBACK))

    return sock


class ListenerProtocol(asyncio.DatagramProtocol):
    """Protocol to listen to other swarm elements."""

    def __init__(self, path_finder_callback, pid):
        self.pid_bytes = str(pid).encode()
        self.path_finder_callback = path_finder_callback
        self.prefix_len = len(PREFIX)

    def datagram_received(self, data, addr):
        """Process a received message."""
        print("========== Listener recv", repr(data))
        if data[:self.prefix_len] == PREFIX:
            path = data[self.prefix_len:].decode("utf8")
            is_here = self.path_finder_callback(path)
            logger.debug("Swarm listener received a path to check: {!r} here={}", path, is_here)
            resp = self.pid_bytes if is_here else PATH_NOT_FOUND
            self.transport.sendto(resp, addr)
        else:
            logger.debug("Swarm listener protocol; garbage? {!r}", data)

    def connection_made(self, transport):
        """Store the transport for a new connection."""
        self.transport = transport


class DiscoverProtocol(asyncio.DatagramProtocol):
    """Protocol to discover other swarm elements."""

    def __init__(self):
        self.responses = []

    def datagram_received(self, data, addr):
        """Register responses."""
        logger.debug("======= TEMP!!! discover recv {!r}", data)
        if data == PATH_NOT_FOUND:
            return

        try:
            pid = int(data.decode())
        except Exception:
            logger.debug("Swarm discover protocol; garbage? {!r}", data)
            return
        self.responses.append(pid)

    def error_received(self, exc):
        """Almost ignore errors."""
        logger.debug("Swarm discover protocol, error received: {!r}", exc)

    def connection_lost(self, exc):
        """Ignore clean connection closure."""


class SwarmServer:
    """Let the swarm fly."""

    def __init__(self, loop, path_finder_callback):
        self.loop = loop
        self.close_event = asyncio.Event()
        self.loop.create_task(self._listen(path_finder_callback))

    async def _listen(self, path_finder_callback):
        """Really listen other Nysors, answer back if we have this path."""
        pid = os.getpid()
        transport, protocol = await self.loop.create_datagram_endpoint(
            lambda: ListenerProtocol(path_finder_callback, pid),
            sock=create_multicast_listener_socket(),
        )

        try:
            logger.debug("Swarm listener up in pid {}", pid)
            await self.close_event.wait()
        finally:
            transport.close()

    def close(self):
        """Close internal structures."""
        self.close_event.set()


async def discover(loop, path):
    """Find out if the indicated path is open in other Nysor instance."""
    transport, protocol = await loop.create_datagram_endpoint(
        DiscoverProtocol,
        sock=create_multicast_sender_socket(),
    )

    try:
        msg = PREFIX + path.encode("utf8")
        transport.sendto(msg, (DISCOVERY_GROUP, DISCOVERY_PORT))

        # as we don't know who will be answering we just need to wait; 200 ms is a LOT
        # of time for other processes to return, and barely noticeable for the human
        # in the startup
        await asyncio.sleep(0.2)
        responses = protocol.responses

    finally:
        transport.close()

    other_pids = [(resp != PATH_NOT_FOUND and resp) for resp in responses]
    logger.debug("Swarm discover result: {} (total={})", other_pids, len(responses))
    return other_pids
