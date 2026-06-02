import json
import socket
import threading
import logging
import queue

log = logging.getLogger('pci.web')

PUSH_SOCK = '/tmp/pci.agd.live.sock'
CMD_SOCK  = '/tmp/pci.agd.cmd.sock'


class AGDClient:
    def __init__(self):
        self._subs      = []
        self._subs_lock = threading.Lock()
        self._thread    = threading.Thread(target=self._reader, daemon=True,
                                           name='agd-client')
        self._thread.start()

    def subscribe(self):
        q = queue.Queue(maxsize=64)
        with self._subs_lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q):
        with self._subs_lock:
            try:
                self._subs.remove(q)
            except ValueError:
                pass

    def _reader(self):
        while True:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.connect(PUSH_SOCK)
                buf = b''
                while True:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    while b'\n' in buf:
                        line, buf = buf.split(b'\n', 1)
                        if line:
                            self._distribute(line.decode())
                sock.close()
            except OSError:
                pass
            import time; time.sleep(2)

    def _distribute(self, line):
        with self._subs_lock:
            for q in self._subs:
                try:
                    q.put_nowait(line)
                except queue.Full:
                    pass

    def send_command(self, cmd: str) -> dict:
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(CMD_SOCK)
            sock.sendall((cmd + '\n').encode())
            resp = b''
            while not resp.endswith(b'\n'):
                chunk = sock.recv(256)
                if not chunk:
                    break
                resp += chunk
            sock.close()
            return json.loads(resp.decode().strip())
        except OSError as e:
            return {'v': 1, 't': 'error', 'msg': str(e)}
