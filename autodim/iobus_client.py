# autodim/iobus_client.py
# IOBus interface for pci.autodim — write-only wrapper.
# Writes dim value (0=bright, 1=dim) to one configured output signal.

import logging

from pci.shared.iobus_client import IOBusClient

log = logging.getLogger('pci.autodim.iobus')


class AutodimIOBus:
    def __init__(self):
        self._client = IOBusClient('pci.autodim')

    def write(self, signal, value):
        ok = self._client.write(signal, value)
        if not ok:
            log.warning("write failed: %s = %s (not connected or not owner)", signal, value)
        return ok

    @property
    def connected(self):
        return self._client.connected
