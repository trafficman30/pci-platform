# MOVA REST routes — web process.
# Phase 2: minimal — ping and stream list only.
# Full REST control routes come in a later phase.

import asyncio
import logging
from fastapi import APIRouter, HTTPException

log = logging.getLogger('pci.web.mova')

router   = APIRouter()
_registry = None


def set_registry(r):
    global _registry
    _registry = r


@router.get("/streams")
async def list_streams():
    if _registry is None:
        return {"streams": []}
    return {"streams": _registry.all_ids()}


@router.get("/streams/{stream_id}/ping")
async def ping_stream(stream_id: int):
    if _registry is None:
        raise HTTPException(503, "registry not initialised")
    client = _registry.get(stream_id)
    if client is None:
        raise HTTPException(404, f"stream {stream_id} not configured")
    loop = asyncio.get_event_loop()
    ack = await loop.run_in_executor(None, client.send_command, "PING")
    if ack is None:
        raise HTTPException(503, f"stream {stream_id} kernel not responding")
    return ack


@router.post("/streams/{stream_id}/cmd")
async def send_cmd(stream_id: int, body: dict):
    """Send an arbitrary command string to the kernel. Body: {"cmd": "FORCE_STAGE 2"}"""
    if _registry is None:
        raise HTTPException(503, "registry not initialised")
    client = _registry.get(stream_id)
    if client is None:
        raise HTTPException(404, f"stream {stream_id} not configured")
    cmd = body.get("cmd", "").strip()
    if not cmd:
        raise HTTPException(400, "cmd field required")
    loop = asyncio.get_event_loop()
    ack = await loop.run_in_executor(None, client.send_command, cmd)
    if ack is None:
        raise HTTPException(503, "kernel not responding")
    return ack
