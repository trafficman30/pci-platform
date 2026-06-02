# rtig/iobus_client.py
# IOBus interface for pci.rtig — write-only wrapper.
# No reads, no subscribe — RTIG only outputs signals via PulseEngine.

import logging

from pci.shared.iobus_client import IOBusClient

log = logging.getLogger('pci.rtig.iobus')


class RTIGIOBus:
    def __init__(self):
        self._client = IOBusClient('pci.rtig')

    def write(self, signal, value):
        """Write signal. Ownership enforced server-side via HELLO pci.rtig."""
        ok = self._client.write(signal, value)
        if not ok:
            log.warning("write failed: %s = %s (not connected or not owner)", signal, value)
        return ok

    @property
    def connected(self):
        return self._client.connected
