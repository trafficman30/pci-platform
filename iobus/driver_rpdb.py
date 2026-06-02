# driver_rpdb.py — RPDB TCP client driver, runs inside pci.iobus.
# Connects to a Peek PTC-1 / EC-2 controller via Remote Par DB protocol (TCP 35340).
#
# Signal naming on table:
#   rpdb.i.xsg.N   ← XSG.CSC element N  (phase/signal group states)
#   rpdb.i.xdet.N  ← XDET.STS element N (detector states)
#   rpdb.i.xout.N  ← XOUT.CSC element N (output readback)
#   rpdb.o.xout.N  → SET_VALUE on XOUT.CSC?id=N
#
# Subscription behaviour (PTC-1 firmware constraint):
#   on-change (0x10) is NOT supported — returns UNSUPPORTED_COMMAND.
#   on-time (0x11) pushes on value change AND heartbeat every N ticks.
#   1 tick = 100ms.  ticks=600 = push on change + heartbeat every 60s.
#
# Output batching:
#   All SET_VALUE requests for one URI are pipelined on a single socket —
#   sent back-to-back before reading any ACKs. This prevents the inter-bit
#   gap that causes controllers to drop UTC mode when multiple XIN bits
#   change together.
#
# Entry point: start(table, config_path) — called by server.py main().
# Config: config/rpdb.cfg  (separate from signals.cfg — too complex for platform.cfg)

import configparser
import logging
import os
import socket
import struct
import threading
import time

log = logging.getLogger('pci.iobus.rpdb')

# ── Session constants ──────────────────────────────────────────────────────────

MSG_HANDSHAKE = 0x01
MSG_LOGIN     = 0x02
MSG_CLOSE     = 0x0F
MSG_PING      = 0x03
MSG_PONG      = 0x04
MSG_MESSAGE   = 0x05

HS_ACCEPT   = 0x01
LOGIN_LOGIN  = 0x00
LOGIN_ACCEPT = 0x01

APP_REQUEST = 0x00

REQ_GET_VALUE              = 0x01
REQ_GET_TEXT               = 0x02
REQ_SET_VALUE              = 0x21
REQ_SUBSCRIBE_VALUE_ONTIME = 0x11
REQ_GET_SYSTEM_TYPE        = 0x61
REQ_GET_SYSTEM_STRING      = 0x62

RESP_SUBSCRIPTION_ACCEPTED = 0x01
RESP_SET_OK                = 0x03
RESP_SYSTEM_TYPE           = 0x11
RESP_SYSTEM_STRING         = 0x12
RESP_VALUE                 = 0x14
RESP_TEXT                  = 0x15
RESP_ERROR_WRONG_URI       = 0x72
RESP_ERROR_NOT_ALLOWED     = 0x75

PING_INTERVAL   = 55.0
RECONNECT_DELAY = 5.0
RECV_TIMEOUT    = 5.0

DRIVER_SOURCE = 'pci.iobus'

SYSTEM_TYPES = {
    0x00: 'EC1_PPC_VXWORKS', 0x01: 'EC2',     0x02: 'EC1_PPC_LINUX',
    0x03: 'PTC1',            0x10: 'SIM_EC1',  0x11: 'SIM_EC2',
    0x13: 'SIM_PTC1',        0x80: 'OSGI',     0x81: 'TFT',
    0x82: 'WEBSERVER',
}


# ── Framing ────────────────────────────────────────────────────────────────────

def _build(msg_type, payload=b''):
    total = 4 + len(payload)
    return struct.pack('>BBH', 0x00, msg_type, total) + payload


def _recv_exact(sock, n):
    buf = b''
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError('connection closed by remote')
        buf += chunk
    return buf


def _recv_msg(sock):
    header = _recv_exact(sock, 4)
    _, msg_type, length = struct.unpack('>BBH', header)
    payload = _recv_exact(sock, length - 4) if length > 4 else b''
    return msg_type, payload


def _build_request(command, uri='', data=b''):
    uri_bytes   = uri.encode('ascii') + b'\x00' if uri else b''
    data_offset = (4 + len(uri_bytes)) if data else 0
    app_header  = struct.pack('>BBH', APP_REQUEST, command, data_offset)
    return _build(MSG_MESSAGE, app_header + uri_bytes + data)


def _parse_response(payload):
    if len(payload) < 16:
        return None, '', None
    _, command, data_offset = struct.unpack('>BBH', payload[0:4])
    if data_offset > 0:
        uri      = payload[16:data_offset].rstrip(b'\x00').decode('ascii', errors='replace')
        raw_data = payload[data_offset:]
    else:
        uri      = payload[16:].rstrip(b'\x00').decode('ascii', errors='replace')
        raw_data = b''
    if command == RESP_VALUE and raw_data:
        n = len(raw_data) // 4
        return command, uri, list(struct.unpack(f'>{n}i', raw_data[:n * 4]))
    elif command == RESP_TEXT and raw_data:
        parts = raw_data.rstrip(b'\x00').split(b'\x00')
        return command, uri, [p.decode('ascii', errors='replace') for p in parts if p]
    return command, uri, None


# ── Session helpers ────────────────────────────────────────────────────────────

def _do_handshake(sock):
    mt, pl = _recv_msg(sock)
    if mt != MSG_HANDSHAKE or len(pl) < 4:
        raise ConnectionError(f'expected HANDSHAKE, got 0x{mt:02X}')
    _, cmd, major, minor = struct.unpack('>BBBB', pl[:4])
    sock.sendall(_build(MSG_HANDSHAKE, struct.pack('>BBBB', 0x00, HS_ACCEPT, major, minor)))
    return major, minor


def _do_login(sock, username, password):
    payload = bytes([LOGIN_LOGIN]) + username.encode() + b'\x00' + password.encode() + b'\x00'
    sock.sendall(_build(MSG_LOGIN, payload))
    mt, pl = _recv_msg(sock)
    if mt != MSG_LOGIN or not pl:
        raise ConnectionError('no login response')
    if pl[0] != LOGIN_ACCEPT:
        raise PermissionError(f"login rejected for user '{username}'")


def _fresh_connection(host, port, username, password, timeout=5.0):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect((host, port))
    _do_handshake(sock)
    _do_login(sock, username, password)
    return sock


def _one_shot_get_text(host, port, username, password, uri):
    try:
        sock = _fresh_connection(host, port, username, password)
        sock.sendall(_build_request(REQ_GET_TEXT, uri))
        sock.settimeout(5.0)
        mt, pl = _recv_msg(sock)
        cmd, _, data = _parse_response(pl)
        sock.sendall(_build(MSG_CLOSE))
        sock.close()
        if cmd == RESP_TEXT and data:
            return data
    except Exception as e:
        log.warning('GET_TEXT %s failed: %s', uri, e)
    return []


def _one_shot_get_value(host, port, username, password, uri):
    try:
        sock = _fresh_connection(host, port, username, password)
        sock.sendall(_build_request(REQ_GET_VALUE, uri))
        sock.settimeout(5.0)
        mt, pl = _recv_msg(sock)
        cmd, _, data = _parse_response(pl)
        sock.sendall(_build(MSG_CLOSE))
        sock.close()
        if cmd == RESP_VALUE and data:
            return data
    except Exception as e:
        log.warning('GET_VALUE %s failed: %s', uri, e)
    return []


# ── Driver ─────────────────────────────────────────────────────────────────────

class RPDBDriver:
    """
    RPDB IO driver — runs inside pci.iobus.

    Parameters
    ----------
    table          : SignalTable
    config_path    : str   — path to signals.cfg (used to scan rpdb.o.* signals)
    host           : str   — controller IP
    port           : int   — RPDB TCP port (default 35340)
    username       : str
    password       : str
    write_password : str   — pincode for SET_VALUE (typically '3145')
    subscriptions  : list  — [(uri, bus_prefix, ticks), ...]
    name_params    : dict  — {data_uri: name_uri}
    static_outputs : dict  — {bus_sig: (uri, idx)}
    """

    def __init__(self, table, config_path,
                 host='192.168.71.97', port=35340,
                 username='user', password='Peek', write_password='3145',
                 subscriptions=None, name_params=None, static_outputs=None):
        self._table         = table
        self._config_path   = config_path
        self.host           = host
        self.port           = port
        self.username       = username
        self.password       = password
        self.write_password = write_password

        self.subscriptions = subscriptions or [
            ('pardb:///XSG.CSC',  'rpdb.i.xsg',  600),
            ('pardb:///XDET.STS', 'rpdb.i.xdet', 600),
        ]
        self.name_params = name_params or {
            'pardb:///XSG.CSC':  'pardb:///XSG.I',
            'pardb:///XDET.STS': 'pardb:///XDET.I',
        }

        self.running      = False
        self.connected    = False
        self._sock        = None
        self._send_event  = threading.Event()
        self._retry_event = threading.Event()
        self._backoff     = RECONNECT_DELAY
        self._fail_count  = 0
        self._write_sock  = None
        self._write_lock  = threading.Lock()

        self._static_outputs = static_outputs or {}
        self._index_to_name  = {}
        self._name_to_index  = {}
        self._input_signals  = {}
        self._output_signals = {}
        self._last_sent      = {}
        self._last_recv      = {}
        self._uri_to_prefix  = {}

    # ── Public ────────────────────────────────────────────────────────────────

    def start(self):
        log.info('RPDB driver starting → %s:%d', self.host, self.port)
        self._fetch_names()
        self._register_inputs()
        self._register_outputs()
        self._table.subscribe(self._on_table_change)
        self.running = True
        threading.Thread(target=self._connect_loop, daemon=True,
                         name='rpdb-connect').start()

    def stop(self):
        self.running = False
        if self._sock:
            try:
                self._sock.sendall(_build(MSG_CLOSE))
                self._sock.close()
            except Exception:
                pass
        log.info('RPDB driver stopped')

    def status(self):
        return {
            'driver':     'rpdb',
            'host':       self.host,
            'port':       self.port,
            'connected':  self.connected,
            'backoff':    int(self._backoff),
            'fail_count': self._fail_count,
        }

    # ── Startup ───────────────────────────────────────────────────────────────

    def _fetch_names(self):
        for uri, name_uri in self.name_params.items():
            names = _one_shot_get_text(
                self.host, self.port, self.username, self.password, name_uri)
            if names:
                self._index_to_name[uri] = {i: n for i, n in enumerate(names)}
                self._name_to_index[uri] = {n: i for i, n in enumerate(names)}
                log.info('RPDB names %s: %d elements (%s to %s)',
                         name_uri, len(names), names[0], names[-1])
            else:
                log.warning('RPDB could not fetch names from %s — numeric indexes only',
                            name_uri)

    def _register_inputs(self):
        for uri, prefix, ticks in self.subscriptions:
            data  = _one_shot_get_value(
                self.host, self.port, self.username, self.password, uri)
            count = len(data)
            if count == 0:
                log.warning('RPDB could not determine element count for %s', uri)
                continue
            for idx in range(count):
                sig = f'{prefix}.{idx}'
                try:
                    self._table.register(sig, DRIVER_SOURCE)
                except ValueError as e:
                    log.warning('%s', e)
                self._input_signals[sig] = (uri, idx)
            log.info('RPDB registered %d elements from %s (prefix %s)',
                     count, uri, prefix)

    def _initial_fetch(self):
        """Fetch current values for all subscriptions and write to table immediately.
        Called after every (re)connect so signals reflect current controller state
        without waiting up to 60s for the first subscription heartbeat."""
        total = 0
        for uri, prefix, ticks in self.subscriptions:
            data = _one_shot_get_value(
                self.host, self.port, self.username, self.password, uri)
            if not data:
                log.warning('RPDB initial fetch failed for %s', uri)
                continue
            full_uri = uri if uri.startswith('pardb:///') else f'pardb:///{uri.upper()}'
            name_map = self._index_to_name.get(full_uri, {})
            for idx, val in enumerate(data):
                sig = f'{prefix}.{idx}'
                if sig in self._input_signals:
                    self._table.write(sig, val, DRIVER_SOURCE)
                    total += 1
                    log.debug('RPDB init %s[%d]=%s (%s)',
                              uri, idx, val, name_map.get(idx, ''))
        log.info('RPDB initial fetch complete: %d signals refreshed', total)

    def _register_outputs(self):
        # Static outputs defined in [OUTPUTS] in rpdb.cfg
        for bus_sig, (uri, idx) in self._static_outputs.items():
            self._output_signals[bus_sig] = (uri, idx)
            self._last_sent[bus_sig] = -1
            log.info('RPDB static output: %s → %s[%d]', bus_sig, uri, idx)

        # Dynamic outputs — scan signals.cfg for rpdb.o.* instead of io.registered_signals()
        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        cfg.read(self._config_path)
        if cfg.has_section('signals'):
            for name, owner in cfg.items('signals'):
                if not name.startswith('rpdb.o.'):
                    continue
                if name in self._output_signals:
                    continue   # already covered by static outputs
                resolved = self._resolve_output(name)
                if resolved:
                    uri, idx = resolved
                    self._output_signals[name] = (uri, idx)
                    self._last_sent[name] = -1
                    log.info('RPDB output: %s → %s[%d]', name, uri, idx)
                else:
                    log.warning('RPDB output %s — not found in name maps, ignored', name)

    def _resolve_output(self, bus_name):
        tail = bus_name[len('rpdb.o.'):]
        for uri, prefix, _ in self.subscriptions:
            sub = prefix[len('rpdb.i.'):]
            if tail.startswith(sub + '.'):
                try:
                    return uri, int(tail[len(sub) + 1:])
                except ValueError:
                    pass
            name_map = self._name_to_index.get(uri, {})
            if tail in name_map:
                return uri, name_map[tail]
        return None

    # ── Connection loop ────────────────────────────────────────────────────────

    def _connect_loop(self):
        while self.running:
            # If the controller was down at startup, _input_signals will be
            # empty. Re-run registration now so signals are ready before
            # subscribing. Retried on every pass until the controller responds.
            if not self._input_signals:
                log.info('RPDB re-fetching names and registering inputs')
                self._fetch_names()
                self._register_inputs()
                self._register_outputs()

            sock = self._connect()
            if not sock:
                if self._fail_count == 0:
                    log.warning('RPDB cannot connect to %s:%d — retrying with backoff (max 5 min)',
                                self.host, self.port)
                self._fail_count += 1
                self._retry_event.clear()
                self._retry_event.wait(timeout=self._backoff)
                self._backoff = min(self._backoff * 2, 300.0)
                continue

            if self._fail_count > 0:
                log.info('RPDB connected to %s:%d after %d attempt(s)',
                         self.host, self.port, self._fail_count)
            self._backoff    = RECONNECT_DELAY
            self._fail_count = 0

            self._sock      = sock
            self.connected  = True
            self._last_sent = {n: -1 for n in self._output_signals}

            # Snapshot current controller state onto the table immediately —
            # don't wait up to 60s for the first subscription heartbeat.
            self._initial_fetch()

            # Open dedicated write socket so SET_VALUE never races with recv_loop
            try:
                self._write_sock = _fresh_connection(
                    self.host, self.port, self.username, self.write_password)
                log.info('RPDB write socket connected')
            except Exception as e:
                log.warning('RPDB write socket failed: %s — will use fresh connections per batch', e)
                self._write_sock = None

            recv_thread = threading.Thread(
                target=self._recv_loop, args=(sock,), daemon=True, name='rpdb-recv')
            recv_thread.start()
            self._send_loop(sock)

            self.connected = False
            self._sock     = None
            if self._write_sock:
                try:
                    self._write_sock.close()
                except Exception:
                    pass
                self._write_sock = None
            try:
                sock.close()
            except Exception:
                pass

            log.warning('RPDB disconnected from %s:%d — retrying', self.host, self.port)
            self._fail_count += 1
            self._retry_event.clear()
            self._retry_event.wait(timeout=self._backoff)
            self._backoff = min(self._backoff * 2, 300.0)

    def _connect(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((self.host, self.port))
            major, minor = _do_handshake(sock)
            _do_login(sock, self.username, self.password)
            sock.settimeout(RECV_TIMEOUT)
            self._query_system_info(sock)

            self._uri_to_prefix = {}
            for sub_uri, sub_prefix, _ in self.subscriptions:
                bare = sub_uri.replace('pardb:///', '').upper()
                self._uri_to_prefix[bare]    = sub_prefix
                self._uri_to_prefix[sub_uri] = sub_prefix

            for uri, prefix, ticks in self.subscriptions:
                data = struct.pack('>I', int(ticks))
                sock.sendall(_build_request(REQ_SUBSCRIBE_VALUE_ONTIME, uri, data))
                log.info('RPDB subscribed: %s every %d tick(s)', uri, ticks)

            log.info('RPDB connected to %s:%d (v%d.%d)',
                     self.host, self.port, major, minor)
            return sock
        except Exception as e:
            log.debug('RPDB connect attempt failed: %s', e)
            try:
                sock.close()
            except Exception:
                pass
            return None

    def _query_system_info(self, sock):
        for req_cmd, resp_cmd, label in [
            (REQ_GET_SYSTEM_TYPE,   RESP_SYSTEM_TYPE,   'type'),
            (REQ_GET_SYSTEM_STRING, RESP_SYSTEM_STRING, 'name'),
        ]:
            sock.sendall(_build_request(req_cmd))
            sock.settimeout(3.0)
            try:
                mt, pl = _recv_msg(sock)
                rc, _, data = _parse_response(pl)
                if label == 'type' and rc == resp_cmd and data:
                    t = data[0] if isinstance(data, list) else data
                    log.info('RPDB system type: %s', SYSTEM_TYPES.get(t, f'0x{t:02X}'))
                elif label == 'name' and rc == resp_cmd and data:
                    log.info('RPDB system name: %s',
                             data[0] if isinstance(data, list) else data)
            except socket.timeout:
                pass
            finally:
                sock.settimeout(RECV_TIMEOUT)

    # ── Receive loop ──────────────────────────────────────────────────────────

    def _recv_loop(self, sock):
        last_ping = time.monotonic()
        while self.running and sock:
            if time.monotonic() - last_ping >= PING_INTERVAL:
                try:
                    sock.sendall(_build(MSG_PING))
                    last_ping = time.monotonic()
                except Exception:
                    break
            try:
                mt, payload = _recv_msg(sock)
            except socket.timeout:
                continue
            except Exception as e:
                log.error('RPDB recv error: %s', e)
                break

            if mt == MSG_PING:
                try:
                    sock.sendall(_build(MSG_PONG))
                except Exception:
                    break
                continue
            if mt == MSG_PONG:
                continue
            if mt == MSG_CLOSE:
                log.info('RPDB server sent CLOSE')
                break
            if mt != MSG_MESSAGE:
                continue

            cmd, uri, data = _parse_response(payload)
            if cmd == RESP_SUBSCRIPTION_ACCEPTED:
                log.debug('RPDB subscription confirmed: %s', uri)
                continue
            if cmd in (RESP_ERROR_WRONG_URI, RESP_ERROR_NOT_ALLOWED):
                log.warning('RPDB subscription error 0x%02X for %s', cmd, uri)
                continue
            if cmd != RESP_VALUE or not data:
                continue

            bare_uri = uri.replace('pardb:///', '').split('?')[0].strip().upper()
            full_uri = f'pardb:///{bare_uri}'
            prefix   = self._uri_to_prefix.get(bare_uri) or self._uri_to_prefix.get(full_uri)
            if prefix is None:
                log.debug('RPDB unmatched push URI: %s', bare_uri)
                continue

            name_map = self._index_to_name.get(full_uri, {})
            for idx, val in enumerate(data):
                sig = f'{prefix}.{idx}'
                if sig in self._input_signals:
                    self._table.write(sig, val, DRIVER_SOURCE)
                    if self._last_recv.get(sig) != val:
                        self._last_recv[sig] = val
                        log.debug('RPDB RECV %s[%d]=%s (%s)',
                                  bare_uri, idx, val, name_map.get(idx, ''))

        # Signal send loop to exit promptly
        self.connected = False
        self._send_event.set()

    # ── Send loop ─────────────────────────────────────────────────────────────

    def _send_loop(self, sock):
        while self.running and self.connected:
            self._send_event.wait(timeout=PING_INTERVAL)
            self._send_event.clear()
            if not self.connected:
                break

            # Group changed signals by URI — pipeline all changes per URI
            # in one TCP burst so the controller sees them atomically
            dirty_by_uri = {}
            for bus_name, (uri, idx) in self._output_signals.items():
                val = self._table.read(bus_name)
                if val != self._last_sent.get(bus_name, -1):
                    dirty_by_uri.setdefault(uri, {})[idx] = (val, bus_name)

            if not dirty_by_uri:
                continue

            for uri, changes in dirty_by_uri.items():
                ok = self._set_values_pipeline(uri, changes)
                if ok:
                    for idx, (val, bus_name) in changes.items():
                        self._last_sent[bus_name] = val
                        log.info('RPDB SET %s[%d] = %d (%s)', uri, idx, val, bus_name)
                else:
                    log.warning('RPDB pipeline SET failed for %s (%d signals)',
                                uri, len(changes))

    def _on_table_change(self, name, value, source):
        if name.startswith('rpdb.o.'):
            self._send_event.set()

    def _set_values_pipeline(self, uri, changes):
        """
        Pipeline all SET_VALUE requests for one URI on a single fresh connection.

        All requests sent back-to-back before reading any ACKs — controller
        receives all bits as one TCP burst, eliminating the inter-bit gap that
        causes controllers to drop UTC mode when multiple XIN bits change together.

        changes: {idx: (value, bus_name)}
        """
        if not changes:
            return True

        with self._write_lock:
            try:
                sock = _fresh_connection(
                    self.host, self.port, self.username, self.write_password)
            except Exception as e:
                log.error('RPDB pipeline connect failed: %s', e)
                return False
            try:
                for idx, (val, _) in changes.items():
                    sock.sendall(_build_request(
                        REQ_SET_VALUE,
                        f'{uri}?id={idx}',
                        struct.pack('>i', int(val))
                    ))
                ok = True
                sock.settimeout(3.0)
                for _ in changes:
                    mt, pl = _recv_msg(sock)
                    if not (mt == MSG_MESSAGE and len(pl) >= 2 and pl[1] == RESP_SET_OK):
                        ok = False
                sock.sendall(_build(MSG_CLOSE))
                sock.close()
                return ok
            except Exception as e:
                log.error('RPDB pipeline SET error: %s', e)
                try:
                    sock.close()
                except Exception:
                    pass
                return False


# ── Config loader ──────────────────────────────────────────────────────────────

def _load_rpdb_config(path):
    """Load config/rpdb.cfg and return kwargs for RPDBDriver.__init__."""
    cp = configparser.ConfigParser(strict=False)
    cp.optionxform = str
    cp.read(path)

    sec            = dict(cp['RPDB']) if cp.has_section('RPDB') else {}
    host           = sec.get('host',           '127.0.0.1')
    port           = int(sec.get('port',       '35340'))
    username       = sec.get('username',       'user')
    password       = sec.get('password',       'Peek')
    write_password = sec.get('write_password', '3145')

    subscriptions = []
    if cp.has_section('SUBSCRIBE'):
        for param, val in cp.items('SUBSCRIBE'):
            parts    = [x.strip() for x in val.split(',')]
            prefix   = parts[0]
            tick_str = parts[1].split('#')[0].strip() if len(parts) > 1 else '600'
            ticks    = int(tick_str) if tick_str.isdigit() else 600
            uri      = f'pardb:///{param.upper()}'
            subscriptions.append((uri, prefix, ticks))

    name_params = {}
    if cp.has_section('NAMES'):
        for param, name_param in cp.items('NAMES'):
            uri      = f'pardb:///{param.upper()}'
            name_uri = f'pardb:///{name_param.split("#")[0].strip().upper()}'
            name_params[uri] = name_uri

    static_outputs = {}
    if cp.has_section('OUTPUTS'):
        for bus_sig, val in cp.items('OUTPUTS'):
            parts = [x.strip() for x in val.split(',')]
            if len(parts) >= 2:
                param = parts[0].split('#')[0].strip().upper()
                idx   = int(parts[1].split('#')[0].strip())
                uri   = f'pardb:///{param}'
                static_outputs[bus_sig] = (uri, idx)

    return {
        'host':           host,
        'port':           port,
        'username':       username,
        'password':       password,
        'write_password': write_password,
        'subscriptions':  subscriptions,
        'name_params':    name_params,
        'static_outputs': static_outputs,
    }


# ── Entry point ────────────────────────────────────────────────────────────────

def start(table, config_path):
    """Called by server.py main() when driver = rpdb in platform.cfg."""
    rpdb_cfg_path = os.path.join(os.path.dirname(config_path), 'rpdb.cfg')
    if not os.path.exists(rpdb_cfg_path):
        log.error('rpdb.cfg not found at %s — driver not started', rpdb_cfg_path)
        return

    kwargs = _load_rpdb_config(rpdb_cfg_path)
    driver = RPDBDriver(table, config_path, **kwargs)
    driver.start()
