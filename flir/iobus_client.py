import logging
from pci.shared.iobus_client import IOBusClient

log = logging.getLogger('pci.flir')


class FLIRIOBus:
    def __init__(self):
        self._client = IOBusClient('pci.flir')

    def connect(self):
        self._client.connect()

    def write(self, signal, value):
        ok = self._client.write(signal, value)
        if not ok:
            log.warning("IOBus write rejected: %s = %d", signal, value)

    def read(self, signal):
        vals = self._client.batch([signal])
        return vals[0] if vals else 0
