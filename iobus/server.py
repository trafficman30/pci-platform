# IOBus server — signal table, ownership enforcement, conditioning, unix sockets.
#
# Sockets:
#   /tmp/pci.iobus.sock      — services read/write signals (BATCH / W)
#   /tmp/pci.iobus.live.sock — web subscribes, receives JSON push on every change
#
# Protocol (command socket):
#   HELLO <service>\n   → OK\n               identify connection (required before W)
#   PING\n              → PONG\n
#   BATCH s1 s2 ...\n   → v1 v2 ...\n        read multiple signals (conditioned)
#   W signal value\n    → OK\n | ERR msg\n   write one signal (ownership enforced)
#
# Run:
#   /opt/pci/venv/bin/python -m pci.iobus.server

import configparser
import importlib
import json
import logging
import os
import signal
import socket
import threading
import time

log = logging.getLogger('pci.iobus')

SOCK_PATH      = '/tmp/pci.iobus.sock'
LIVE_SOCK_PATH = '/tmp/pci.iobus.live.sock'


# ── Signal table ──────────────────────────────────────────────────────────────

class SignalTable:
    """
    In-memory signal table.  Drivers write raw values directly; all readers
    (socket clients and drivers) receive conditioned values.
    One registered owner per signal; non-owner writes are rejected.
    """

    def __init__(self):
        self._values = {}    # name → raw int
        self._owners = {}    # name → str
        self._cond   = {}    # name → {'invert': bool, 'scale': float}
        self._subs   = []    # change callbacks: fn(name, conditioned_value, source)
        self._lock   = threading.Lock()

    def register(self, name, owner, *, invert=False, scale=1.0):
        with self._lock:
            existing = self._owners.get(name)
            if existing and existing != owner:
                raise ValueError(
                    f"signal '{name}' already owned by '{existing}', "
                    f"cannot register for '{owner}'"
                )
            self._values.setdefault(name, 0)
            self._owners[name] = owner
            c = {}
            if invert:
                c['invert'] = True
            if scale != 1.0:
                c['scale'] = scale
            if c:
                self._cond[name] = c

    def write(self, name, value, source):
        """Write a raw value. Returns True on success, False if rejected."""
        with self._lock:
            owner = self._owners.get(name)
            if owner is None:
                log.warning("rejected write '%s' by '%s' — not registered", name, source)
                return False
            if owner != source:
                log.warning("rejected write '%s' by '%s' — owner is '%s'",
                            name, source, owner)
                return False
            if self._values[name] == value:
                return True
            self._values[name] = value
            conditioned = self._apply(name, value)
            subs = list(self._subs)

        for cb in subs:
            try:
                cb(name, conditioned, source)
            except Exception as e:
                log.error("subscriber error: %s", e)
        return True

    def read(self, name):
        """Read conditioned value. Returns 0 for unknown signals."""
        with self._lock:
            return self._apply(name, self._values.get(name, 0))

    def snapshot(self):
        """Return all signal conditioned values as {name: value}."""
        with self._lock:
            return {n: self._apply(n, v) for n, v in self._values.items()}

    def subscribe(self, cb):
        with self._lock:
            self._subs.append(cb)

    def _apply(self, name, raw):
        # called with lock held
        c = self._cond.get(name)
        if not c:
            return raw
        v = int(raw * c.get('scale', 1.0))
        if c.get('invert'):
            v = 0 if v else 1
        return v


# ── Live push socket (IOBus → web) ────────────────────────────────────────────

class LivePushServer:
    """
    Accepts connections on /tmp/pci.iobus.live.sock.
    Pushes one JSON line per signal change to all connected clients.
    {"name": "...", "value": ..., "ts": ..., "src": "..."}
    """

    def __init__(self, table):
        self._clients = []
        self._lock    = threading.Lock()
        table.subscribe(self._on_change)

    def start(self):
        threading.Thread(target=self._accept, daemon=True, name='iobus-live').start()

    def _accept(self):
        _clean(LIVE_SOCK_PATH)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(LIVE_SOCK_PATH)
        srv.listen(8)
        log.info("live push: %s", LIVE_SOCK_PATH)
        while True:
            try:
                conn, _ = srv.accept()
                with self._lock:
                    self._clients.append(conn)
                log.debug("live: client connected (%d total)", len(self._clients))
            except Exception as e:
                log.error("live accept: %s", e)

    def _on_change(self, name, value, source):
        msg = (json.dumps({'name': name, 'value': value,
                           'ts': time.time(), 'src': source}) + '\n').encode()
        dead = []
        with self._lock:
            for c in list(self._clients):
                try:
                    c.sendall(msg)
                except Exception:
                    dead.append(c)
            for c in dead:
                self._clients.remove(c)
                log.debug("live: client disconnected (%d total)", len(self._clients))


# ── Command socket (services ↔ IOBus) ─────────────────────────────────────────

class IOBusServer:
    """
    Serves BATCH / W / PING / HELLO on /tmp/pci.iobus.sock.
    Each client connection is handled on its own daemon thread.
    HELLO must be sent before any W command so the server knows the caller's identity.
    """

    def __init__(self, table):
        self._table = table

    def start(self):
        threading.Thread(target=self._accept, daemon=True, name='iobus-cmd').start()

    def _accept(self):
        _clean(SOCK_PATH)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(SOCK_PATH)
        srv.listen(16)
        log.info("command socket: %s", SOCK_PATH)
        while True:
            try:
                conn, _ = srv.accept()
                threading.Thread(
                    target=self._handle, args=(conn,), daemon=True
                ).start()
            except Exception as e:
                log.error("accept error: %s", e)

    def _handle(self, conn):
        identity = None
        try:
            for line in conn.makefile('r'):
                parts = line.split()
                if not parts:
                    continue
                cmd = parts[0].upper()

                if cmd == 'HELLO' and len(parts) == 2:
                    identity = parts[1]
                    conn.sendall(b'OK\n')

                elif cmd == 'PING':
                    conn.sendall(b'PONG\n')

                elif cmd == 'BATCH' and len(parts) > 1:
                    vals = ' '.join(str(self._table.read(s)) for s in parts[1:])
                    conn.sendall((vals + '\n').encode())

                elif cmd == 'W' and len(parts) == 3:
                    if identity is None:
                        conn.sendall(b'ERR no identity - send HELLO first\n')
                        continue
                    try:
                        value = int(parts[2])
                    except ValueError:
                        conn.sendall(b'ERR bad value\n')
                        continue
                    ok = self._table.write(parts[1], value, identity)
                    conn.sendall(b'OK\n' if ok else b'ERR rejected\n')

                else:
                    conn.sendall(b'ERR unknown\n')
        except Exception:
            pass
        finally:
            conn.close()


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path):
    cfg = configparser.ConfigParser()
    cfg.optionxform = str    # preserve signal name case
    cfg.read(path)

    signals = {}
    if cfg.has_section('signals'):
        for name, owner in cfg.items('signals'):
            signals[name] = {'owner': owner.strip()}

    if cfg.has_section('conditioning'):
        for name, spec in cfg.items('conditioning'):
            if name not in signals:
                log.warning("conditioning for unregistered signal '%s' — ignored", name)
                continue
            for token in spec.split():
                if token == 'invert':
                    signals[name]['invert'] = True
                elif token.startswith('scale='):
                    try:
                        signals[name]['scale'] = float(token[6:])
                    except ValueError:
                        log.warning("bad scale token for '%s': %s", name, token)

    return signals


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
        datefmt='%H:%M:%S',
    )

    config_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'signals.cfg')
    signals = load_config(config_path)

    table = SignalTable()
    for name, props in signals.items():
        table.register(
            name,
            props['owner'],
            invert=props.get('invert', False),
            scale=props.get('scale', 1.0),
        )
    log.info("registered %d signals", len(signals))

    platform_path = os.path.join(os.path.dirname(__file__), '..', 'config', 'platform.cfg')
    plat = configparser.ConfigParser()
    plat.read(platform_path)
    driver_name = plat.get('iobus', 'driver', fallback='').strip()
    if driver_name:
        try:
            mod = importlib.import_module(f'pci.iobus.driver_{driver_name}')
            mod.start(table, config_path)
        except Exception as e:
            log.error("driver '%s' failed to start: %s", driver_name, e)

    LivePushServer(table).start()
    IOBusServer(table).start()

    log.info("pci.iobus ready")

    done = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: done.set())
    signal.signal(signal.SIGINT,  lambda *_: done.set())
    done.wait()
    log.info("pci.iobus stopped")


def _clean(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


if __name__ == '__main__':
    main()
