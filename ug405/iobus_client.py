# ug405/iobus_client.py
# IOBus interface for pci.ug405 — wraps shared IOBusClient.
#
# Provides read/write/subscribe() compatible with the CM5 IOBus contract
# used by UG405Service, ScootSampler, and RBEService.
#
# subscribe() is poll-based: a background thread reads monitored signals via
# BATCH every 100ms and fires callbacks on value changes. Call set_monitored()
# with the list of reply signals after loading ug405.cfg.
#
# Callback signature: cb(signal_name, conditioned_value, source='iobus')

import logging
import threading
import time

from pci.shared.iobus_client import IOBusClient

log = logging.getLogger('pci.ug405.iobus')

_POLL_INTERVAL = 0.1   # 100ms — matches SCOOT sampler and CM5 bus update rate


class UG405IOBus:
    """
    IOBus interface for pci.ug405.

    Wraps IOBusClient and adds:
      read(sig)          — single signal read with '!' inversion support
      write(sig, value)  — ownership enforced by IOBus server (HELLO pci.ug405)
      subscribe(cb)      — register change callback (poll-based)
      set_monitored(...)  — declare signals to poll (call once after config load)
      zero_owned(sigs)   — write 0 to all listed signals (standalone drop)
    """

    def __init__(self):
        self._client    = IOBusClient('pci.ug405')
        self._subs      = []
        self._monitored = []
        self._prev      = {}
        self._lock      = threading.Lock()
        self._polling   = False

    # ── Public API ────────────────────────────────────────────────────────────

    def read(self, sig):
        """Read a signal. Strips '!' prefix and returns inverted value if present."""
        invert = sig.startswith('!')
        raw    = sig[1:] if invert else sig
        val    = self._client.batch([raw])[0]
        return (1 - val) if invert else val

    def write(self, sig, value, source=None):
        """Write a signal. Ownership enforced server-side via HELLO pci.ug405."""
        return self._client.write(sig, value)

    def subscribe(self, cb):
        """Register a change callback: cb(name, value, source)."""
        with self._lock:
            self._subs.append(cb)

    def set_monitored(self, signal_names):
        """
        Declare which raw signal names to poll for changes.

        Called once after load_ug405() — pass all reply signal names (without '!').
        Starts the background poll thread on first call.
        """
        with self._lock:
            self._monitored = list(signal_names)
            self._prev      = {s: None for s in signal_names}
            if not self._polling:
                self._polling = True
                threading.Thread(
                    target=self._poll_loop, daemon=True,
                    name='ug405-iobus-poll',
                ).start()

    def zero_owned(self, signal_names):
        """Write 0 to all listed signals (used when dropping to opMode 1 or 2)."""
        for sig in signal_names:
            self._client.write(sig, 0)

    @property
    def connected(self):
        return self._client.connected

    # ── Poll loop ─────────────────────────────────────────────────────────────

    def _poll_loop(self):
        while True:
            time.sleep(_POLL_INTERVAL)

            with self._lock:
                monitored = list(self._monitored)

            if not monitored or not self._client.connected:
                continue

            values = self._client.batch(monitored)

            changed = []
            with self._lock:
                for sig, val in zip(monitored, values):
                    if self._prev.get(sig) != val:
                        self._prev[sig] = val
                        changed.append((sig, val))
                subs = list(self._subs)

            for sig, val in changed:
                for cb in subs:
                    try:
                        cb(sig, val, 'iobus')
                    except Exception as e:
                        log.error("subscribe callback error: %s", e)
