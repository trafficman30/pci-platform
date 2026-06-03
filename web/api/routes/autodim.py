# web/api/routes/autodim.py
# Autodim REST routes — mounted at /api/autodim.

import asyncio
import logging

from fastapi import APIRouter, HTTPException

log = logging.getLogger('pci.web.autodim')

router  = APIRouter()
_client = None


def set_client(c):
    global _client
    _client = c


@router.get("/ping")
async def ping():
    if _client is None:
        raise HTTPException(503, "autodim client not initialised")
    loop = asyncio.get_event_loop()
    ack = await loop.run_in_executor(None, _client.send_command, "PING")
    if ack is None:
        raise HTTPException(503, "autodim not responding")
    return ack


@router.post("/set_enabled/{value}")
async def set_enabled(value: int):
    if _client is None:
        raise HTTPException(503, "autodim client not initialised")
    loop = asyncio.get_running_loop()
    ack  = await loop.run_in_executor(
        None, _client.send_command, f"SET_ENABLED {1 if value else 0}"
    )
    if ack is None:
        raise HTTPException(503, "autodim not responding")
    return ack
