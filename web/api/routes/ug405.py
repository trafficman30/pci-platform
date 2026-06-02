# UG405 REST routes — web process.

import logging
from fastapi import APIRouter, HTTPException

log = logging.getLogger('pci.web.ug405')

router  = APIRouter()
_client = None


def set_client(c):
    global _client
    _client = c


@router.get("/ping")
async def ping():
    if _client is None:
        raise HTTPException(503, "ug405 client not initialised")
    ack = _client.send_command("PING")
    if ack is None:
        raise HTTPException(503, "ug405 not responding")
    return ack
