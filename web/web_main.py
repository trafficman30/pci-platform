# pci.web — web process entry point.
# FastAPI + uvicorn on :8080.
# Connects to all configured kernel IPC sockets via KernelRegistry.
#
# Run:
#   PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.web.web_main

import sys
import os
import logging

sys.path.insert(0, '/opt')   # pci.* packages live under /opt/pci → /opt

import configparser

import uvicorn

from pci.mova.ipc.client import KernelRegistry
from pci.web.api.app import create_app

_PLATFORM_CFG = os.path.join(os.path.dirname(__file__), '..', 'config', 'platform.cfg')


def _stream_ids():
    """Read enabled MOVA stream indices from platform.cfg."""
    cfg = configparser.ConfigParser()
    cfg.read(_PLATFORM_CFG)
    ids_str = cfg.get('mova', 'streams', fallback='0')
    return [int(x.strip()) for x in ids_str.split(',') if x.strip().isdigit()]


def main():
    logging.basicConfig(
        level   = logging.INFO,
        format  = '%(asctime)s pci.web %(name)s %(levelname)s %(message)s',
        datefmt = '%H:%M:%S',
    )
    log = logging.getLogger('pci.web')
    log.info("starting  pid=%d", os.getpid())

    stream_ids = _stream_ids()
    log.info("connecting to MOVA stream IDs: %s", stream_ids)

    registry = KernelRegistry(stream_ids)
    app      = create_app(registry)

    port = int(os.getenv('PCI_WEB_PORT', '8081'))
    uvicorn.run(app, host='0.0.0.0', port=port, log_level='info')


if __name__ == '__main__':
    main()
