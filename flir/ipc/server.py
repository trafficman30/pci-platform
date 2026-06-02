import json
import socket
import threading
import logging
import time

log = logging.getLogger('pci.flir')

PUSH_SOCK = '/tmp/pci.flir.live.sock'
CMD_SOCK  = '/tmp/pci.flir.cmd.sock'


class IPCServer:
    def __init__(self, service):
        self._svc       = service
        self._subs      = []
        self._subs_lock = threading.Lock()

    def start(self):
        threading.Thread(target=self._push_loop,  daemon=True, name='flir-push').start()
        threading.Thread(target=self._cmd_server,  daemon=True, name='flir-cmd').start()

    # ── push socket ──────────────────────────────────────────────────────────

    def _push_loop(self):
        import os
        try: os.unlink(PUSH_SOCK)
        except FileNotFoundError: pass

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(PUSH_SOCK)
        srv.listen(8)
        log.info("FLIR push socket: %s", PUSH_SOCK)

        threading.Thread(target=self._accept_subs, args=(srv,),
                         daemon=True, name='flir-push-accept').start()

        while True:
            snap = self._svc.snapshot()
            self._broadcast(json.dumps({'v': 1, 't': 'snap', **snap}) + '\n')
            time.sleep(1)

    def _accept_subs(self, srv):
        while True:
            conn, _ = srv.accept()
            with self._subs_lock:
                self._subs.append(conn)

    def _broadcast(self, line):
        encoded = line.encode()
        dead = []
        with self._subs_lock:
            for conn in self._subs:
                try:
                    conn.sendall(encoded)
                except OSError:
                    dead.append(conn)
            for conn in dead:
                self._subs.remove(conn)

    def push_event(self, event: dict):
        self._broadcast(json.dumps({'v': 1, **event}) + '\n')

    # ── command socket ────────────────────────────────────────────────────────

    def _cmd_server(self):
        import os
        try: os.unlink(CMD_SOCK)
        except FileNotFoundError: pass

        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(CMD_SOCK)
        srv.listen(8)
        log.info("FLIR cmd socket: %s", CMD_SOCK)

        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self._handle_cmd, args=(conn,),
                             daemon=True).start()

    def _handle_cmd(self, conn):
        try:
            line = b''
            while not line.endswith(b'\n'):
                chunk = conn.recv(256)
                if not chunk:
                    return
                line += chunk
            cmd = line.decode().strip()
            if cmd == 'PING':
                conn.sendall(json.dumps({'v': 1, 't': 'pong'}).encode() + b'\n')
            else:
                conn.sendall(json.dumps({'v': 1, 't': 'error',
                                         'msg': f'unknown: {cmd}'}).encode() + b'\n')
        except OSError:
            pass
        finally:
            conn.close()
