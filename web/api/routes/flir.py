import logging
from fastapi import APIRouter, HTTPException

log    = logging.getLogger('pci.web')
router = APIRouter()
_client = None


def set_client(c):
    global _client
    _client = c


@router.get("/ping")
async def ping():
    if _client is None:
        raise HTTPException(503, "flir client not initialised")
    return _client.send_command("PING")
