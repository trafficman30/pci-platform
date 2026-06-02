# UG405 IPC client — used by the web process.
#
# UG405Client — connects to the ug405 push + command sockets.
#               Background thread reads the push stream and distributes to
#               subscribers (each subscriber gets a threading.Queue).
#
# UG405 is a singleton — no registry needed, one fixed socket path.

import json
import logging
import queue
import socket
import threading
import time

log = logging.getLogger('pci.ug405.ipc.client')

_LIVE_SOCK   = '/tmp/pci.ug405.live.sock'
_CMD_SOCK    = '/tmp/pci.ug405.cmd.sock'
_RETRY_DELAY = 2.0
_CMD_TIMEOUT = 3.0


class UG405Client:
    """
    Connects to pci.ug405's IPC sockets.

    Subscribe to the live push stream via subscribe() — returns a Queue.
    Send commands via send_command() — returns ack dict or None on error.
    """

    def __init__(self):
        self._subs     = []
        self._sub_lock = threading.Lock()
        self.connected = False
        threading.Thread(
            target=self._read_loop, daemon=True,
            name='ug405-client',
        ).start()

    def subscribe(self):
        """Return a Queue that will receive event dicts from the push socket."""
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
        """
        Send a one-shot command, return the ack dict.
        Returns None if the service is not reachable.
        """
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(_CMD_TIMEOUT)
            sock.connect(_CMD_SOCK)
            sock.sendall((cmd_line.strip() + '\n').encode())
            f    = sock.makefile('r')
            line = f.readline()
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
        log.info("connected to ug405 live push socket")
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
