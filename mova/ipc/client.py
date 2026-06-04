# MOVA kernel IPC client — used by the web process.
#
# KernelClient  — connects to one kernel instance's live + command sockets.
#                 Background thread reads the push stream and distributes to
#                 subscribers (each subscriber gets a threading.Queue).
#
# KernelRegistry — manages N KernelClient instances, keyed by stream_id.
#
# Thread model: one background thread per kernel reads the live socket.
# SSE endpoint: subscribes, blocks on queue.get(timeout=30) in run_in_executor.

import json
import logging
import queue
import socket
import threading
import time

log = logging.getLogger('pci.mova.ipc.client')

_RETRY_DELAY = 2.0
_CMD_TIMEOUT = 3.0


class KernelClient:
    """
    Connects to one kernel instance's IPC sockets.

    Subscribe to the live push stream via subscribe() — returns a Queue.
    Send commands via send_command() — returns ack dict or None on error.
    """

    def __init__(self, stream_id):
        self._sid         = stream_id
        self._subs        = []
        self._sub_lock    = threading.Lock()
        self.connected    = False
        self._latest_snap = {}
        threading.Thread(
            target=self._read_loop, daemon=True,
            name=f'kernel-client-{stream_id}',
        ).start()

    def latest_snap(self) -> dict:
        """Return the most recently received snap event, or {} if none yet."""
        return self._latest_snap

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
        Returns None if the kernel is not reachable.
        """
        path = f'/tmp/pci.mova.{self._sid}.cmd.sock'
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(_CMD_TIMEOUT)
            sock.connect(path)
            sock.sendall((cmd_line.strip() + '\n').encode())
            f    = sock.makefile('r')
            line = f.readline()
            sock.close()
            return json.loads(line)
        except Exception as e:
            log.warning("stream %d: send_command failed: %s", self._sid, e)
            return None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _read_loop(self):
        while True:
            try:
                self._connect_and_read()
            except Exception as e:
                log.debug("stream %d: live socket: %s", self._sid, e)
            self.connected = False
            time.sleep(_RETRY_DELAY)

    def _connect_and_read(self):
        path = f'/tmp/pci.mova.{self._sid}.live.sock'
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(_RETRY_DELAY)
        sock.connect(path)
        sock.settimeout(None)
        self.connected = True
        log.info("stream %d: connected to live push socket", self._sid)
        for line in sock.makefile('r'):
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if ev.get('t') == 'snap':
                self._latest_snap = ev
            with self._sub_lock:
                dead = []
                for q in list(self._subs):
                    try:
                        q.put_nowait(ev)
                    except queue.Full:
                        dead.append(q)
                for q in dead:
                    self._subs.remove(q)
                    log.debug("stream %d: dropped slow subscriber", self._sid)


class KernelRegistry:
    """
    Manages KernelClient instances for all configured streams.
    Passed to the web layer at startup.
    """

    def __init__(self, stream_ids):
        self._clients = {sid: KernelClient(sid) for sid in stream_ids}

    def get(self, stream_id):
        """Return KernelClient for stream_id, or None if not registered."""
        return self._clients.get(stream_id)

    def all_ids(self):
        return list(self._clients.keys())
