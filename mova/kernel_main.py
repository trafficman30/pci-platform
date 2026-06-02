# pci.mova.kernel_main — entry point for one MOVA kernel instance.
#
# Run:
#   PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.mova.kernel_main <stream_id>
#
# Threading model:
#   main thread  — tick loop (MovaStream._loop, 100ms)
#                  SIGTERM lands here → clean stop
#   IPC thread   — push socket + command socket (IPCServer)
#
# sys.path manipulation must happen before any pci_mova imports.

import sys
import os

if '/opt/MOVA' not in sys.path:
    sys.path.insert(0, '/opt/MOVA')

import logging
import signal
import time

from pci_mova.core.stream import MovaStream
from pci_mova.core.model.enums import MovaStatus

from pci.mova.kernel_io import KernelIO, load_stream_map
from pci.shared.iobus_client import IOBusClient
from pci.mova.ipc.server import IPCServer

_STREAMS_JSON = os.path.join(os.path.dirname(__file__), '..', 'config', 'streams.json')


class _PciMovaStream(MovaStream):
    """
    MovaStream adapted for PCI kernel_main: tick runs on the caller's thread,
    not a daemon thread.  Overrides start() so load_dataset() does not spawn
    a background tick thread — kernel_main drives _loop() on the main thread.
    """

    def start(self):
        if self._dataset is None:
            self.status.set(MovaStatus.NO_DATASET)
            return
        self._running.set()
        self.status.set(MovaStatus.WARMUP)
        log = logging.getLogger('pci.mova.kernel')
        log.info("stream %d: dataset loaded — entering warmup", self.stream_id)


def _load_dataset(path):
    """Load and return a MovaDataset from path, or None on error."""
    try:
        from pci_mova.dataset.parser import load as ds_load
        return ds_load(path)
    except Exception as e:
        logging.getLogger('pci.mova.kernel').error(
            "dataset load failed '%s': %s", path, e
        )
        return None


def main():
    if len(sys.argv) < 2:
        print(f"usage: {sys.argv[0]} <stream_id>", file=sys.stderr)
        sys.exit(1)

    stream_id = int(sys.argv[1])

    from pci.shared.log import setup
    setup(f'pci.mova.{stream_id}')
    log = logging.getLogger('pci.mova.kernel')
    log.info("starting stream %d  pid=%d", stream_id, os.getpid())

    # Signal map from streams.json
    stream_map = load_stream_map(_STREAMS_JSON, stream_id)

    # IOBus client — connects in background, safe before IOBus is ready
    iobus = IOBusClient(f'pci.mova.kernel@{stream_id}')

    # IO layer: IOBus ↔ kernel buffers bridge
    kernel_io = KernelIO(iobus, stream_map, stream_id=stream_id)

    # IPC server — push + command sockets
    ipc = IPCServer(stream_id=stream_id)
    ipc.set_load_callback(_load_dataset)

    # Fault callback → immediate push event
    def _on_fault(stream, fault_dict):
        ipc.push_event({
            "v"        : 1,
            "t"        : "fault",
            "ts"       : time.time(),
            "error_id" : fault_dict.get("error_id", 0),
            "data"     : fault_dict.get("data", 0),
        })

    # MOVA stream — tick loop runs on main thread via _loop()
    stream = _PciMovaStream(
        stream_id = stream_id,
        io_layer  = kernel_io,
        on_fault  = _on_fault,
    )

    ipc.set_stream(stream)
    ipc.start()

    # SIGTERM / SIGINT → stop tick loop cleanly
    def _stop(*_):
        log.info("signal received — stopping stream %d", stream_id)
        stream._running.clear()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT,  _stop)

    log.info("stream %d: tick loop starting on main thread", stream_id)

    # Set running and enter the tick loop — blocks until _running cleared
    stream._running.set()
    try:
        stream._loop()
    finally:
        try:
            stream.stream_logger.flush()
        except Exception:
            pass
        log.info("stream %d: stopped  pid=%d", stream_id, os.getpid())


if __name__ == '__main__':
    main()
