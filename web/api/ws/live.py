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

router          = APIRouter()
_registry       = None
_ug405_client   = None
_rtig_client    = None
_autodim_client  = None
_offline_client  = None
_agd_client      = None
_flir_client     = None


def set_registry(r):
    global _registry
    _registry = r


def set_ug405_client(c):
    global _ug405_client
    _ug405_client = c


def set_rtig_client(c):
    global _rtig_client
    _rtig_client = c


def set_autodim_client(c):
    global _autodim_client
    _autodim_client = c


def set_offline_client(c):
    global _offline_client
    _offline_client = c


def set_agd_client(c):
    global _agd_client
    _agd_client = c


def set_flir_client(c):
    global _flir_client
    _flir_client = c


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
                        None, lambda: q.get(block=True, timeout=2)
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


@router.get("/rtig")
async def rtig_sse():
    if _rtig_client is None:
        raise HTTPException(503, "rtig client not initialised")

    q = _rtig_client.subscribe()

    async def generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    ev = await loop.run_in_executor(
                        None, lambda: q.get(block=True, timeout=2)
                    )
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _rtig_client.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type = "text/event-stream",
        headers    = {
            "Cache-Control"   : "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/offline")
async def offline_sse():
    if _offline_client is None:
        raise HTTPException(503, "offline client not initialised")

    q = _offline_client.subscribe()

    async def generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    ev = await loop.run_in_executor(
                        None, lambda: q.get(block=True, timeout=2)
                    )
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _offline_client.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type = "text/event-stream",
        headers    = {
            "Cache-Control"   : "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/autodim")
async def autodim_sse():
    if _autodim_client is None:
        raise HTTPException(503, "autodim client not initialised")

    q = _autodim_client.subscribe()

    async def generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    ev = await loop.run_in_executor(
                        None, lambda: q.get(block=True, timeout=2)
                    )
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _autodim_client.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type = "text/event-stream",
        headers    = {
            "Cache-Control"   : "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/agd")
async def agd_sse():
    if _agd_client is None:
        raise HTTPException(503, "agd client not initialised")

    q = _agd_client.subscribe()

    async def generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    ev = await loop.run_in_executor(
                        None, lambda: q.get(block=True, timeout=2)
                    )
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _agd_client.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type = "text/event-stream",
        headers    = {
            "Cache-Control"   : "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/flir")
async def flir_sse():
    if _flir_client is None:
        raise HTTPException(503, "flir client not initialised")

    q = _flir_client.subscribe()

    async def generate():
        loop = asyncio.get_event_loop()
        try:
            while True:
                try:
                    ev = await loop.run_in_executor(
                        None, lambda: q.get(block=True, timeout=2)
                    )
                    yield f"data: {json.dumps(ev, default=str)}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _flir_client.unsubscribe(q)

    return StreamingResponse(
        generate(),
        media_type = "text/event-stream",
        headers    = {
            "Cache-Control"   : "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/iobus")
async def iobus_sse():
    _LIVE = '/tmp/pci.iobus.live.sock'

    async def generate():
        try:
            reader, writer = await asyncio.open_unix_connection(_LIVE)
        except Exception as e:
            log.warning("iobus live socket not available: %s", e)
            yield f"data: {json.dumps({'error': 'IOBus not connected'})}\n\n"
            return
        try:
            while True:
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout=2.0)
                    if not line:
                        break
                    yield f"data: {line.decode().strip()}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

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
                        None, lambda: q.get(block=True, timeout=2)
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
