# SSE live push endpoint — web → browser.
# One SSE stream per kernel instance.
# Reads from KernelClient subscriber queue (blocking, run_in_executor).

import asyncio
import json
import logging
import queue

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

log = logging.getLogger('pci.web.sse')

router         = APIRouter()
_registry      = None
_ug405_client  = None


def set_registry(r):
    global _registry
    _registry = r


def set_ug405_client(c):
    global _ug405_client
    _ug405_client = c


@router.get("/mova/{stream_id}")
async def mova_sse(stream_id: int):
    if _registry is None:
        raise HTTPException(503, "registry not initialised")
    client = _registry.get(stream_id)
    if client is None:
        raise HTTPException(404, f"stream {stream_id} not configured")

    q = client.subscribe()

    async def generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    ev = await loop.run_in_executor(
                        None, lambda: q.get(block=True, timeout=25)
                    )
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except queue.Empty:
                    # Keepalive comment — prevents proxy timeouts
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            client.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type = "text/event-stream",
        headers    = {
            "Cache-Control"   : "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/ug405")
async def ug405_sse():
    if _ug405_client is None:
        raise HTTPException(503, "ug405 client not initialised")

    q = _ug405_client.subscribe()

    async def generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    ev = await loop.run_in_executor(
                        None, lambda: q.get(block=True, timeout=25)
                    )
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _ug405_client.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type = "text/event-stream",
        headers    = {
            "Cache-Control"   : "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
