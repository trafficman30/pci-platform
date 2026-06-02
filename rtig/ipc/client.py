# rtig/ipc/client.py
# RTIG IPC client — used by the web process.
# Mirrors pci/ug405/ipc/client.py exactly — singleton, fixed socket paths.

import json
import logging
import queue
import socket
import threading
import time

log = logging.getLogger('pci.rtig.ipc.client')

_LIVE_SOCK   = '/tmp/pci.rtig.live.sock'
_CMD_SOCK    = '/tmp/pci.rtig.cmd.sock'
_RETRY_DELAY = 2.0
_CMD_TIMEOUT = 3.0


class RTIGClient:
    def __init__(self):
        self._subs     = []
        self._sub_lock = threading.Lock()
        self.connected = False
        threading.Thread(
            target=self._read_loop, daemon=True,
            name='rtig-client',
        ).start()

    def subscribe(self):
        q = queue.Queue(maxsize=512)
        with self._sub_lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._sub_lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def send_command(self, cmd_line):
        """Send a one-shot command, return ack dict or None on error."""
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(_CMD_TIMEOUT)
            sock.connect(_CMD_SOCK)
            sock.sendall((cmd_line.strip() + '\n').encode())
            line = sock.makefile('r').readline()
            sock.close()
            return json.loads(line)
        except Exception as e:
            log.warning("send_command failed: %s", e)
            return None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _read_loop(self):
        while True:
            try:
                self._connect_and_read()
            except Exception as e:
                log.debug("live socket: %s", e)
            self.connected = False
            time.sleep(_RETRY_DELAY)

    def _connect_and_read(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(_RETRY_DELAY)
        sock.connect(_LIVE_SOCK)
        sock.settimeout(None)
        self.connected = True
        log.info("connected to rtig live push socket")
        for line in sock.makefile('r'):
            try:
                ev = json.loads(line)
            except Exception:
                continue
            with self._sub_lock:
                dead = []
                for q in list(self._subs):
                    try:
                        q.put_nowait(ev)
                    except queue.Full:
                        dead.append(q)
                for q in dead:
                    self._subs.remove(q)
                    log.debug("dropped slow subscriber")
