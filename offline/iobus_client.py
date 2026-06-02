# offline/iobus_client.py
# IOBus interface for pci.offline — write-only.
# Writes plan control signals (Fn/Dn/SFn and single-bit) to IOBus.
# Signals must be declared in signals.cfg as owned by pci.offline (or shared).

import logging

from pci.shared.iobus_client import IOBusClient

log = logging.getLogger('pci.offline.iobus')


class OfflineIOBus:
    def __init__(self):
        self._client = IOBusClient('pci.offline')

    def write(self, signal, value):
        ok = self._client.write(signal, value)
        if not ok:
            log.warning("write failed: %s = %s (not connected or not owner)", signal, value)
        return ok

    @property
    def connected(self):
        return self._client.connected
