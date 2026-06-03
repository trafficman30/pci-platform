# web/api/routes/iobus.py
# IOBus REST routes — mounted at /api/iobus.
# GET /api/iobus/signals  — full signal table snapshot via SNAP command.

import asyncio
import json
import logging
import socket

from fastapi import APIRouter, HTTPException

log = logging.getLogger('pci.web.iobus')

router = APIRouter()

_SOCK_PATH = '/tmp/pci.iobus.sock'


def _recv_line(sock):
    buf = b''
    while b'\n' not in buf:
        chunk = sock.recv(256)
        if not chunk:
            return buf
        buf += chunk
    return buf


def _snap_blocking():
    """Blocking: open IOBus command socket, HELLO + SNAP, return signal dict."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(2.0)
    try:
        sock.connect(_SOCK_PATH)
        sock.sendall(b'HELLO pci.web\n')
        _recv_line(sock)           # discard OK\n
        sock.sendall(b'SNAP\n')
        buf = b''
        while b'\n' not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed before newline")
            buf += chunk
        return json.loads(buf.split(b'\n')[0])
    finally:
        try:
            sock.close()
        except Exception:
            pass


@router.get("/signals")
async def get_signals():
    """Return current conditioned values for all registered IOBus signals."""
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, _snap_blocking)
        return result
    except Exception as e:
        raise HTTPException(503, f"IOBus not available: {e}")
