# ug405/ipc/server.py
# IPC server for pci.ug405 — push socket + command socket.
# Follows the same pattern as pci/mova/ipc/server.py.
#
# Push socket  /tmp/pci.ug405.live.sock  — ug405 → web (one way)
#   1Hz snapshot of full state
#   Immediate events: opmode change, signal change, config, log entries
#
# Command socket  /tmp/pci.ug405.cmd.sock  — web → ug405 (req/ack)
#   PING → {"v":1,"t":"pong"}
#   (further commands added when web integration is built in Phase 4.3)

import json
import logging
import os
import queue
import socket
import threading
import time

log = logging.getLogger('pci.ug405.ipc')

_LIVE_SOCK = '/tmp/pci.ug405.live.sock'
_CMD_SOCK  = '/tmp/pci.ug405.cmd.sock'
_SNAP_INTERVAL = 1.0   # seconds between full snapshots


def _clean(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


class IPCServer:
    """
    Push + command IPC server for pci.ug405.

    The service calls push_event(ev) for immediate events (opmode change etc.)
    and registers a snapshot callback via set_snapshot_callback().
    """

    def __init__(self):
        self._clients   = []
        self._cli_lock  = threading.Lock()
        self._event_q   = queue.Queue()
        self._snap_cb   = None    # () → dict with full state

    def set_snapshot_callback(self, cb):
        """cb() must return a dict representing full service state."""
        self._snap_cb = cb

    def push_event(self, ev):
        """Queue an immediate event for all connected push clients."""
        self._event_q.put(ev)

    def push(self, **kw):
        """
        Convenience wrapper called by the service on state changes.
        Mirrors CM5's push_update() signature.
        """
        ts = time.time()
        if 'opmode' in kw:
            self._event_q.put({'v': 1, 't': 'opmode', 'ts': ts,
                                'opmode': kw['opmode']})
        if 'changes' in kw:
            self._event_q.put({'v': 1, 't': 'signal', 'ts': ts,
                                'changes': kw['changes']})
        if 'instation' in kw:
            self._event_q.put({'v': 1, 't': 'config', 'ts': ts,
                                'field': 'instation', 'value': kw['instation']})
        if 'set_log_entry' in kw:
            self._event_q.put({'v': 1, 't': 'log', 'ts': ts,
                                'entry': kw['set_log_entry']})

    def start(self):
        threading.Thread(target=self._push_accept, daemon=True,
                         name='ug405-push-accept').start()
        threading.Thread(target=self._push_loop,   daemon=True,
                         name='ug405-push-loop').start()
        threading.Thread(target=self._cmd_accept,  daemon=True,
                         name='ug405-cmd').start()
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
                log.debug("push client connected (%d total)", len(self._clients))
                # Send current snapshot immediately on connect
                if self._snap_cb:
                    try:
                        snap = self._snap_cb()
                        snap['v'] = 1
                        snap['t'] = 'snap'
                        snap['ts'] = time.time()
                        self._send_to(conn, snap)
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
                log.debug("push client disconnected (%d total)", len(self._clients))

    def _send_to(self, conn, ev):
        conn.sendall((json.dumps(ev, default=str) + '\n').encode())

    def _push_loop(self):
        last_snap = 0.0
        while True:
            time.sleep(0.05)

            # Drain immediate events first
            while True:
                try:
                    ev = self._event_q.get_nowait()
                    self._push_to_all(
                        (json.dumps(ev, default=str) + '\n').encode())
                except queue.Empty:
                    break

            # 1Hz full snapshot
            now = time.time()
            if self._snap_cb and (now - last_snap) >= _SNAP_INTERVAL:
                last_snap = now
                try:
                    snap = self._snap_cb()
                    snap['v']  = 1
                    snap['t']  = 'snap'
                    snap['ts'] = now
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
        parts = line.split()
        if not parts:
            return {'v': 1, 't': 'err', 'msg': 'empty command'}
        cmd = parts[0].upper()

        if cmd == 'PING':
            return {'v': 1, 't': 'pong'}

        return {'v': 1, 't': 'err', 'msg': f'unknown: {parts[0]}'}
