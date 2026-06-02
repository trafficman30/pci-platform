# FastAPI application — PCI web process.
# Aggregates live data from all kernel IPC sockets and serves browser.

import os
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pci.web.api.routes.mova  import router as mova_router,  set_registry as mova_set_reg
from pci.web.api.routes.ug405 import router as ug405_router, set_client   as ug405_set_client
from pci.web.api.routes.rtig  import router as rtig_router,  set_client   as rtig_set_client, create_receiver_app
from pci.web.api.ws.live      import (router as sse_router,  set_registry as sse_set_reg,
                                      set_ug405_client as sse_set_ug405,
                                      set_rtig_client  as sse_set_rtig)

log = logging.getLogger('pci.web')

_STATIC = os.path.join(os.path.dirname(__file__), '..', 'static')


def create_app(registry, ug405_client=None, rtig_client=None):
    mova_set_reg(registry)
    sse_set_reg(registry)
    if ug405_client is not None:
        ug405_set_client(ug405_client)
        sse_set_ug405(ug405_client)
    if rtig_client is not None:
        rtig_set_client(rtig_client)
        sse_set_rtig(rtig_client)

    app = FastAPI(title="PCI Web", version="2.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins     = ["*"],
        allow_credentials = True,
        allow_methods     = ["*"],
        allow_headers     = ["*"],
    )

    app.include_router(mova_router,  prefix="/api/mova",  tags=["MOVA"])
    app.include_router(ug405_router, prefix="/api/ug405", tags=["UG405"])
    app.include_router(rtig_router,  prefix="/api/rtig",  tags=["RTIG"])
    app.include_router(sse_router,   prefix="/sse",        tags=["SSE"])

    if os.path.isdir(_STATIC):
        app.mount("/static", StaticFiles(directory=_STATIC), name="static")

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(os.path.join(_STATIC, "index.html"))

    log.info("FastAPI app created  streams=%s", registry.all_ids())
    return app
