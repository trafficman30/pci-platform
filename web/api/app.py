# FastAPI application — PCI web process.
# Aggregates live data from all kernel IPC sockets and serves browser.

import os
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from pci.web.api.routes.mova    import router as mova_router,    set_registry as mova_set_reg
from pci.web.api.routes.ug405   import router as ug405_router,   set_client   as ug405_set_client
from pci.web.api.routes.rtig    import router as rtig_router,    set_client   as rtig_set_client, create_receiver_app
from pci.web.api.routes.autodim import router as autodim_router, set_client   as autodim_set_client
from pci.web.api.routes.offline import router as offline_router, set_client   as offline_set_client
from pci.web.api.routes.agd     import router as agd_router,     set_client   as agd_set_client
from pci.web.api.routes.flir    import router as flir_router,    set_client   as flir_set_client
from pci.web.api.ws.live        import (router as sse_router,    set_registry as sse_set_reg,
                                        set_ug405_client   as sse_set_ug405,
                                        set_rtig_client    as sse_set_rtig,
                                        set_autodim_client as sse_set_autodim,
                                        set_offline_client as sse_set_offline,
                                        set_agd_client     as sse_set_agd,
                                        set_flir_client    as sse_set_flir)

log = logging.getLogger('pci.web')

_STATIC = os.path.join(os.path.dirname(__file__), '..', 'static')


def create_app(registry, ug405_client=None, rtig_client=None,
               autodim_client=None, offline_client=None,
               agd_client=None, flir_client=None):
    mova_set_reg(registry)
    sse_set_reg(registry)
    if ug405_client is not None:
        ug405_set_client(ug405_client)
        sse_set_ug405(ug405_client)
    if rtig_client is not None:
        rtig_set_client(rtig_client)
        sse_set_rtig(rtig_client)
    if autodim_client is not None:
        autodim_set_client(autodim_client)
        sse_set_autodim(autodim_client)
    if offline_client is not None:
        offline_set_client(offline_client)
        sse_set_offline(offline_client)
    if agd_client is not None:
        agd_set_client(agd_client)
        sse_set_agd(agd_client)
    if flir_client is not None:
        flir_set_client(flir_client)
        sse_set_flir(flir_client)

    app = FastAPI(title="PCI Web", version="2.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins     = ["*"],
        allow_credentials = True,
        allow_methods     = ["*"],
        allow_headers     = ["*"],
    )

    app.include_router(mova_router,    prefix="/api/mova",    tags=["MOVA"])
    app.include_router(ug405_router,   prefix="/api/ug405",   tags=["UG405"])
    app.include_router(rtig_router,    prefix="/api/rtig",    tags=["RTIG"])
    app.include_router(autodim_router, prefix="/api/autodim", tags=["Autodim"])
    app.include_router(offline_router, prefix="/api/offline", tags=["Offline"])
    app.include_router(agd_router,     prefix="/api/agd",     tags=["AGD"])
    app.include_router(flir_router,    prefix="/api/flir",    tags=["FLIR"])
    app.include_router(sse_router,     prefix="/sse",         tags=["SSE"])

    if os.path.isdir(_STATIC):
        app.mount("/static", StaticFiles(directory=_STATIC), name="static")

        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(os.path.join(_STATIC, "index.html"))

        @app.get("/ug405", include_in_schema=False)
        async def ug405_page():
            return FileResponse(os.path.join(_STATIC, "ug405.html"))

    log.info("FastAPI app created  streams=%s", registry.all_ids())
    return app
