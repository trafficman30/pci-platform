# web/api/routes/rtig.py
# RTIG REST routes (main app) + receiver app factory (port 9010).

import asyncio
import logging

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import Response

log = logging.getLogger('pci.web.rtig')

# ── Main app router — mounted at /api/rtig ────────────────────────────────────

router  = APIRouter()
_client = None


def set_client(c):
    global _client
    _client = c


@router.get("/ping")
async def ping():
    if _client is None:
        raise HTTPException(503, "rtig client not initialised")
    loop = asyncio.get_event_loop()
    ack = await loop.run_in_executor(None, _client.send_command, "PING")
    if ack is None:
        raise HTTPException(503, "rtig not responding")
    return ack


@router.post("/reload_rules")
async def reload_rules():
    if _client is None:
        raise HTTPException(503, "rtig client not initialised")
    loop = asyncio.get_event_loop()
    ack  = await loop.run_in_executor(None, _client.send_command, "RELOAD_RULES")
    if ack is None:
        raise HTTPException(503, "rtig not responding")
    return ack


# ── Receiver app — mounted on port 9010 ──────────────────────────────────────

def create_receiver_app(rtig_client):
    """
    Minimal FastAPI app for the RTIG HTTP listener on port 9010.
    Accepts POST from the UTC instation / RTIG system, forwards XML to
    pci.rtig via IPC command socket.
    """
    app = FastAPI(title="PCI RTIG Receiver", docs_url=None, redoc_url=None)

    @app.post("/{path:path}")
    @app.post("/")
    async def receive_tlp(request: Request, path: str = ""):
        body     = (await request.body()).decode('utf-8', errors='replace')
        xml_line = ' '.join(body.split())   # flatten to single line for IPC
        if xml_line and rtig_client is not None:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, rtig_client.send_command, 'RECEIVE ' + xml_line
            )
        return Response(content='OK', status_code=200)

    return app
