# offline/ipc/server.py
# IPC server for pci.offline — push socket + command socket.
# Follows the same pattern as pci/rtig/ipc/server.py.
#
# Push socket  /tmp/pci.offline.live.sock  — offline → web (one way)
#   1Hz snapshot of full state
#
# Command socket  /tmp/pci.offline.cmd.sock  — web → offline (req/ack)
#   PING     → {"v":1,"t":"pong"}
#   RELOAD   → {"v":1,"t":"ack","cmd":"RELOAD","ok":true}

import json
import logging
import os
import queue
import socket
import threading
import time

log = logging.getLogger('pci.offline.ipc')

_LIVE_SOCK     = '/tmp/pci.offline.live.sock'
_CMD_SOCK      = '/tmp/pci.offline.cmd.sock'
_SNAP_INTERVAL = 1.0


def _clean(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


class IPCServer:
    def __init__(self):
        self._clients   = []
        self._cli_lock  = threading.Lock()
        self._event_q   = queue.Queue()
        self._snap_cb   = None   # () → dict
        self._reload_cb = None   # () → None

    def set_snapshot_callback(self, cb):
        self._snap_cb = cb

    def set_reload_callback(self, cb):
        self._reload_cb = cb

    def push_event(self, ev):
        self._event_q.put(ev)

    def start(self):
        threading.Thread(target=self._push_accept, daemon=True,
                         name='offline-push-accept').start()
        threading.Thread(target=self._push_loop,   daemon=True,
                         name='offline-push-loop').start()
        threading.Thread(target=self._cmd_accept,  daemon=True,
                         name='offline-cmd').start()
        log.info("IPC server started")

    # ── Push socket ───────────────────────────────────────────────────────────

    def _push_accept(self):
        _clean(_LIVE_SOCK)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(_LIVE_SOCK)
        srv.listen(8)
        log.info("push socket %s", _LIVE_SOCK)
        while True:
            try:
                conn, _ = srv.accept()
                with self._cli_lock:
                    self._clients.append(conn)
                    total = len(self._clients)
                if total == 1:
                    log.info("push client connected (1 total)")
                else:
                    log.debug("push client connected (%d total)", total)
                if self._snap_cb:
                    try:
                        snap = self._snap_cb()
                        snap.update({'v': 1, 't': 'snap', 'ts': time.time()})
                        conn.sendall((json.dumps(snap, default=str) + '\n').encode())
                    except Exception as e:
                        log.debug("initial snap error: %s", e)
            except Exception as e:
                log.error("push accept error: %s", e)

    def _push_to_all(self, data: bytes):
        dead = []
        with self._cli_lock:
            for c in list(self._clients):
                try:
                    c.sendall(data)
                except Exception:
                    dead.append(c)
            for c in dead:
                try:
                    c.close()
                except Exception:
                    pass
                self._clients.remove(c)
                remaining = len(self._clients)
                if remaining == 0:
                    log.info("push client disconnected (0 total)")
                else:
                    log.debug("push client disconnected (%d total)", remaining)

    def _push_loop(self):
        last_snap = 0.0
        while True:
            time.sleep(0.05)
            while True:
                try:
                    ev = self._event_q.get_nowait()
                    self._push_to_all(
                        (json.dumps(ev, default=str) + '\n').encode())
                except queue.Empty:
                    break
            now = time.time()
            if self._snap_cb and (now - last_snap) >= _SNAP_INTERVAL:
                last_snap = now
                try:
                    snap = self._snap_cb()
                    snap.update({'v': 1, 't': 'snap', 'ts': now})
                    self._push_to_all(
                        (json.dumps(snap, default=str) + '\n').encode())
                except Exception as e:
                    log.debug("snapshot error: %s", e)

    # ── Command socket ────────────────────────────────────────────────────────

    def _cmd_accept(self):
        _clean(_CMD_SOCK)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(_CMD_SOCK)
        srv.listen(8)
        log.info("command socket %s", _CMD_SOCK)
        while True:
            try:
                conn, _ = srv.accept()
                threading.Thread(target=self._cmd_handle, args=(conn,),
                                 daemon=True).start()
            except Exception as e:
                log.error("cmd accept error: %s", e)

    def _cmd_handle(self, conn):
        try:
            line = conn.makefile('r').readline()
            ack  = self._dispatch(line.strip())
            conn.sendall((json.dumps(ack, default=str) + '\n').encode())
        except Exception as e:
            log.warning("cmd handle error: %s", e)
        finally:
            conn.close()

    def _dispatch(self, line):
        if not line:
            return {'v': 1, 't': 'err', 'msg': 'empty command'}

        cmd = line.split()[0].upper()

        if cmd == 'PING':
            return {'v': 1, 't': 'pong'}

        if cmd == 'RELOAD':
            if self._reload_cb:
                self._reload_cb()
            return {'v': 1, 't': 'ack', 'cmd': 'RELOAD', 'ok': True}

        return {'v': 1, 't': 'err', 'msg': f'unknown: {cmd}'}
