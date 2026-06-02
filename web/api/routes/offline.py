# web/api/routes/offline.py
# Offline plan REST routes — mounted at /api/offline.

import asyncio
import logging

from fastapi import APIRouter, HTTPException

log = logging.getLogger('pci.web.offline')

router  = APIRouter()
_client = None


def set_client(c):
    global _client
    _client = c


@router.get("/ping")
async def ping():
    if _client is None:
        raise HTTPException(503, "offline client not initialised")
    ack = _client.send_command("PING")
    if ack is None:
        raise HTTPException(503, "offline not responding")
    return ack


@router.post("/reload")
async def reload():
    if _client is None:
        raise HTTPException(503, "offline client not initialised")
    loop = asyncio.get_event_loop()
    ack  = await loop.run_in_executor(None, _client.send_command, "RELOAD")
    if ack is None:
        raise HTTPException(503, "offline not responding")
    return ack
