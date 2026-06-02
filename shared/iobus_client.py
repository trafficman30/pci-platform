# IOBus client — used by every service that reads from or writes to the IOBus.
#
# Connects to /tmp/pci.iobus.sock and sends HELLO on connect.
# If the IOBus server is not running, fails silently and retries every second
# in a background thread. Services start cleanly regardless of IOBus state.
# Callers receive safe defaults (0 / False) while disconnected.

import logging
import socket
import threading
import time

log = logging.getLogger('pci.iobus.client')

_SOCK_PATH    = '/tmp/pci.iobus.sock'
_RETRY_DELAY  = 1.0   # seconds between reconnect attempts
_SOCK_TIMEOUT = 2.0   # seconds — request/response timeout


class IOBusClient:
    """
    Thread-safe IOBus socket client.

    Instantiate once per service, pass service_name matching signals.cfg ownership.
    The background reconnect thread starts immediately; calling code does not
    need to wait for the connection before using batch() or write().
    """

    def __init__(self, service_name):
        self._service = service_name
        self._sock    = None
        self._file    = None
        self._lock    = threading.Lock()
        threading.Thread(
            target=self._connect_loop, daemon=True,
            name=f'iobus-{service_name}',
        ).start()

    # ── Public API ────────────────────────────────────────────────────────────

    def batch(self, signals):
        """
        Read multiple signals in one round trip.
        Returns list of int values in the same order as signals.
        Returns [0, ...] if not connected.
        """
        with self._lock:
            if self._sock is None:
                return [0] * len(signals)
            try:
                self._sock.sendall(('BATCH ' + ' '.join(signals) + '\n').encode())
                resp = self._file.readline().strip()
                return [int(v) for v in resp.split()]
            except Exception:
                self._drop()
                return [0] * len(signals)

    def write(self, signal, value):
        """
        Write one signal value.
        Returns True on OK, False if not connected or rejected by ownership check.
        """
        with self._lock:
            if self._sock is None:
                return False
            try:
                self._sock.sendall(f'W {signal} {value}\n'.encode())
                resp = self._file.readline().strip()
                return resp == 'OK'
            except Exception:
                self._drop()
                return False

    def ping(self):
        """Returns True if connected and IOBus responds to PING."""
        with self._lock:
            if self._sock is None:
                return False
            try:
                self._sock.sendall(b'PING\n')
                return self._file.readline().strip() == 'PONG'
            except Exception:
                self._drop()
                return False

    @property
    def connected(self):
        with self._lock:
            return self._sock is not None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _connect_loop(self):
        while True:
            with self._lock:
                up = self._sock is not None
            if not up:
                self._try_connect()
            time.sleep(_RETRY_DELAY)

    def _try_connect(self):
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(_SOCK_TIMEOUT)
            sock.connect(_SOCK_PATH)
            sock.sendall(f'HELLO {self._service}\n'.encode())
            f = sock.makefile('r')
            if f.readline().strip() != 'OK':
                sock.close()
                return
            with self._lock:
                if self._sock is not None:   # connected by another path — shouldn't happen
                    sock.close()
                    return
                self._sock = sock
                self._file = f
            log.info("connected to IOBus as '%s'", self._service)
        except Exception as e:
            log.debug("IOBus not available: %s", e)

    def _drop(self):
        # always called with _lock held
        try:
            self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._file = None
        log.warning("lost IOBus connection — retrying")
