# pci.web — web process entry point.
# FastAPI + uvicorn on :8080 (main UI) and :9010 (RTIG HTTP receiver).
#
# Run:
#   PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.web.web_main

import sys
import os
import asyncio
import concurrent.futures
import logging

sys.path.insert(0, '/opt')   # pci.* packages live under /opt/pci → /opt

import configparser

import uvicorn

from pci.mova.ipc.client        import KernelRegistry
from pci.ug405.ipc.client       import UG405Client
from pci.rtig.ipc.client        import RTIGClient
from pci.autodim.ipc.client     import AutodimClient
from pci.offline.ipc.client     import OfflineClient
from pci.agd.ipc.client         import AGDClient
from pci.flir.ipc.client        import FLIRClient
from pci.web.api.app            import create_app
from pci.web.api.routes.rtig    import create_receiver_app

_PLATFORM_CFG = os.path.join(os.path.dirname(__file__), '..', 'config', 'platform.cfg')


def _read_config():
    cfg = configparser.ConfigParser()
    cfg.read(_PLATFORM_CFG)
    stream_ids = [int(x.strip())
                  for x in cfg.get('mova', 'streams', fallback='0').split(',')
                  if x.strip().isdigit()]
    rtig_port  = cfg.getint('rtig', 'http_port', fallback=9010)
    return stream_ids, rtig_port


async def _serve(app, port, rtig_app, rtig_port):
    log = logging.getLogger('pci.web')
    # Default pool (min(32, cpu+4)) is too small on 2-CPU hosts — SSE zombie
    # threads (one per live SSE connection) can exhaust it in a few navigations.
    loop = asyncio.get_running_loop()
    loop.set_default_executor(concurrent.futures.ThreadPoolExecutor(max_workers=32))
    main_cfg = uvicorn.Config(app,      host='0.0.0.0', port=port,
                              log_level='info')
    rtig_cfg = uvicorn.Config(rtig_app, host='0.0.0.0', port=rtig_port,
                              log_level='warning', access_log=False)

    main_srv = uvicorn.Server(main_cfg)
    rtig_srv = uvicorn.Server(rtig_cfg)
    rtig_srv.install_signal_handlers = False   # only main server handles SIGTERM

    async def _rtig_guarded():
        try:
            await rtig_srv.serve()
        except SystemExit:
            log.warning("RTIG HTTP receiver could not bind on :%d — port in use, RTIG disabled", rtig_port)

    rtig_task = asyncio.ensure_future(_rtig_guarded())
    await main_srv.serve()
    rtig_task.cancel()
    await asyncio.gather(rtig_task, return_exceptions=True)


def main():
    from pci.shared.log import setup
    setup('pci.web')
    log = logging.getLogger('pci.web')
    log.info("starting  pid=%d", os.getpid())

    stream_ids, rtig_port = _read_config()
    log.info("MOVA streams: %s  RTIG HTTP port: %d", stream_ids, rtig_port)

    registry        = KernelRegistry(stream_ids)
    ug405_client    = UG405Client()
    rtig_client     = RTIGClient()
    autodim_client  = AutodimClient()
    offline_client  = OfflineClient()
    agd_client      = AGDClient()
    flir_client     = FLIRClient()

    app      = create_app(registry, ug405_client=ug405_client,
                          rtig_client=rtig_client, autodim_client=autodim_client,
                          offline_client=offline_client,
                          agd_client=agd_client, flir_client=flir_client)
    rtig_app = create_receiver_app(rtig_client)

    port = int(os.getenv('PCI_WEB_PORT', '8081'))
    asyncio.run(_serve(app, port, rtig_app, rtig_port))


if __name__ == '__main__':
    main()
