# driver_xkop.py — XKOP TCP client driver, runs inside pci.iobus.
# Bridges IOBus signal table ↔ TLC XKOP server.
#
# Reads  xkop.i.* from TLC → writes to signal table
# Reads  xkop.o.* from signal table → sends to TLC
#
# Event-driven send: table.subscribe() wakes send immediately on xkop.o.* change.
# Connection: client mode (connect out to TLC) or server mode (TLC connects in).
# Exponential backoff on failure: 2s → 300s max.
# Keepalive every 6s. Dead-connection detected after 18s silence → reconnect.
#
# Entry point: start(table, config_path) — called by server.py main().
# Config: [xkop] ip/port/mode in platform.cfg (same directory as signals.cfg).

import configparser
import logging
import os
import socket
import threading
import time

log = logging.getLogger('pci.iobus.xkop')

PACKET_LENGTH       = 17
HEADER              = bytes([0xCA, 0x35])
DATA_PACKET_TYPE    = 0x00
KEEP_ALIVE_PACKET   = bytearray.fromhex("CA35020000000000000000000000009BA0")
KEEP_ALIVE_INTERVAL = 6.0
DEAD_TIMEOUT        = KEEP_ALIVE_INTERVAL * 3   # 18s

DRIVER_SOURCE       = 'pci.iobus'   # all IOBus drivers own signals as pci.iobus

# ── CRC16 (from /opt/CM5/core/crc_utils.py) ──────────────────────────────────

_CRC_TABLE = [
    0x0000, 0x0f89, 0x1f12, 0x109b, 0x3e24, 0x31ad, 0x2136, 0x2ebf,
    0x7c48, 0x73c1, 0x635a, 0x6cd3, 0x426c, 0x4de5, 0x5d7e, 0x52f7,
    0xf081, 0xff08, 0xef93, 0xe01a, 0xcea5, 0xc12c, 0xd1b7, 0xde3e,
    0x8cc9, 0x8340, 0x93db, 0x9c52, 0xb2ed, 0xbd64, 0xadff, 0xa276,
    0xe102, 0xee8b, 0xfe10, 0xf199, 0xdf26, 0xd0af, 0xc034, 0xcfbd,
    0x9d4a, 0x92c3, 0x8258, 0x8dd1, 0xa36e, 0xace7, 0xbc7c, 0xb3f5,
    0x1183, 0x1e0a, 0x0e91, 0x0118, 0x2fa7, 0x202e, 0x30b5, 0x3f3c,
    0x6dcb, 0x6242, 0x72d9, 0x7d50, 0x53ef, 0x5c66, 0x4cfd, 0x4374,
    0xc204, 0xcd8d, 0xdd16, 0xd29f, 0xfc20, 0xf3a9, 0xe332, 0xecbb,
    0xbe4c, 0xb1c5, 0xa15e, 0xaed7, 0x8068, 0x8fe1, 0x9f7a, 0x90f3,
    0x3285, 0x3d0c, 0x2d97, 0x221e, 0x0ca1, 0x0328, 0x13b3, 0x1c3a,
    0x4ecd, 0x4144, 0x51df, 0x5e56, 0x70e9, 0x7f60, 0x6ffb, 0x6072,
    0x2306, 0x2c8f, 0x3c14, 0x339d, 0x1d22, 0x12ab, 0x0230, 0x0db9,
    0x5f4e, 0x50c7, 0x405c, 0x4fd5, 0x616a, 0x6ee3, 0x7e78, 0x71f1,
    0xd387, 0xdc0e, 0xcc95, 0xc31c, 0xeda3, 0xe22a, 0xf2b1, 0xfd38,
    0xafcf, 0xa046, 0xb0dd, 0xbf54, 0x91eb, 0x9e62, 0x8ef9, 0x8170,
    0x8408, 0x8b81, 0x9b1a, 0x9493, 0xba2c, 0xb5a5, 0xa53e, 0xaab7,
    0xf840, 0xf7c9, 0xe752, 0xe8db, 0xc664, 0xc9ed, 0xd976, 0xd6ff,
    0x7489, 0x7b00, 0x6b9b, 0x6412, 0x4aad, 0x4524, 0x55bf, 0x5a36,
    0x08c1, 0x0748, 0x17d3, 0x185a, 0x36e5, 0x396c, 0x29f7, 0x267e,
    0x650a, 0x6a83, 0x7a18, 0x7591, 0x5b2e, 0x54a7, 0x443c, 0x4bb5,
    0x1942, 0x16cb, 0x0650, 0x09d9, 0x2766, 0x28ef, 0x3874, 0x37fd,
    0x958b, 0x9a02, 0x8a99, 0x8510, 0xabaf, 0xa426, 0xb4bd, 0xbb34,
    0xe9c3, 0xe64a, 0xf6d1, 0xf958, 0xd7e7, 0xd86e, 0xc8f5, 0xc77c,
    0x460c, 0x4985, 0x591e, 0x5697, 0x7828, 0x77a1, 0x673a, 0x68b3,
    0x3a44, 0x35cd, 0x2556, 0x2adf, 0x0460, 0x0be9, 0x1b72, 0x14fb,
    0xb68d, 0xb904, 0xa99f, 0xa616, 0x88a9, 0x8720, 0x97bb, 0x9832,
    0xcac5, 0xc54c, 0xd5d7, 0xda5e, 0xf4e1, 0xfb68, 0xebf3, 0xe47a,
    0xa70e, 0xa887, 0xb81c, 0xb795, 0x992a, 0x96a3, 0x8638, 0x89b1,
    0xdb46, 0xd4cf, 0xc454, 0xcbdd, 0xe562, 0xeaeb, 0xfa70, 0xf5f9,
    0x578f, 0x5806, 0x489d, 0x4714, 0x69ab, 0x6622, 0x76b9, 0x7930,
    0x2bc7, 0x244e, 0x34d5, 0x3b5c, 0x15e3, 0x1a6a, 0x0af1, 0x0578,
]


def _write_crc16(data, length, skip_bytes=2):
    crc1 = crc0 = 0
    for i in range(skip_bytes, length):
        temp = (crc1 ^ data[i]) & 0xFF
        crc1 = (crc0 ^ (_CRC_TABLE[temp] & 0xFF)) & 0xFF
        crc0 = (_CRC_TABLE[temp] >> 8) & 0xFF
    data[length]     = crc1
    data[length + 1] = crc0
    return data


# ── Packet builder ────────────────────────────────────────────────────────────

def _make_packet(updates):
    """Build a 17-byte XKOP data packet from up to 4 (signal_name, value) tuples."""
    packet = bytearray(PACKET_LENGTH)
    packet[0:2] = HEADER
    packet[2]   = DATA_PACKET_TYPE
    for i in range(4):
        if i < len(updates):
            name, val = updates[i]
            xkop_idx = int(name.split('.')[-1])
            packet[3 + i*3] = xkop_idx
            packet[4 + i*3] = (val >> 8) & 0xFF
            packet[5 + i*3] = val & 0xFF
        else:
            packet[3 + i*3] = 0xFF
    _write_crc16(packet, length=15, skip_bytes=2)
    return packet


# ── Driver ────────────────────────────────────────────────────────────────────

class XKOPDriver:
    """
    XKOP IO driver — runs inside pci.iobus.

    Parameters
    ----------
    table          : SignalTable
    ip             : str   — remote IP (client) or bind IP (server)
    port           : int   — TCP port
    mode           : str   — 'client' or 'server'
    input_signals  : list  — xkop.i.* names owned by pci.iobus (to zero on reconnect)
    output_signals : list  — xkop.o.* names to monitor and send
    """

    def __init__(self, table, ip, port, mode, input_signals, output_signals):
        self._table           = table
        self.ip               = ip
        self.port             = port
        self.mode             = mode
        self._input_signals   = list(input_signals)
        self._output_signals  = list(output_signals)

        self.conn             = None
        self.running          = False
        self.connected        = False

        self._last_sent       = {}
        self._last_ka         = 0.0
        self._last_recv       = 0.0
        self._send_event      = threading.Event()
        self._retry_event     = threading.Event()
        self._backoff         = 2.0
        self._fail_count      = 0

    def start(self):
        self._last_sent = {name: -1 for name in self._output_signals}
        self._table.subscribe(self._on_table_change)
        self.running = True
        threading.Thread(target=self._connect_loop, daemon=True,
                         name='xkop-connect').start()
        log.info("XKOP driver starting (%s) → %s:%d", self.mode, self.ip, self.port)
        log.info("monitoring %d output signals, zeroing %d input signals on reconnect",
                 len(self._output_signals), len(self._input_signals))

    def stop(self):
        self.running = False
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
        log.info("XKOP driver stopped")

    def status(self):
        return {
            'driver':     'xkop',
            'mode':       self.mode,
            'ip':         self.ip,
            'port':       self.port,
            'connected':  self.connected,
            'backoff':    int(self._backoff),
            'fail_count': self._fail_count,
        }

    # ── Connection loop ───────────────────────────────────────────────────────

    def _connect_loop(self):
        while self.running:
            self.conn = self._connect()
            if not self.conn:
                if self._fail_count == 0:
                    log.warning("cannot connect to %s:%d — retrying with backoff (max 5 min)",
                                self.ip, self.port)
                self._fail_count += 1
                self._retry_event.clear()
                self._retry_event.wait(timeout=self._backoff)
                self._backoff = min(self._backoff * 2, 300.0)
                continue

            if self._fail_count > 0:
                log.info("connected to %s:%d after %d attempt(s)",
                         self.ip, self.port, self._fail_count)
            self._backoff    = 2.0
            self._fail_count = 0
            self.connected   = True
            self._last_recv  = time.time()

            # Zero inputs so stale values don't persist if TLC restarted
            self._zero_inputs()

            # Reset to force full state push on connect
            self._last_sent = {name: -1 for name in self._output_signals}
            self._last_ka   = time.time()

            try:
                self.conn.send(KEEP_ALIVE_PACKET)
                self._last_ka = time.time()
            except Exception:
                pass

            threading.Thread(target=self._recv_loop, daemon=True,
                             name='xkop-recv').start()

            self._send_loop()   # blocks until disconnect

            self.connected = False
            if self.conn:
                try:
                    self.conn.close()
                except Exception:
                    pass
            self.conn = None
            log.warning("disconnected from %s:%d — retrying", self.ip, self.port)
            self._fail_count += 1
            self._retry_event.clear()
            self._retry_event.wait(timeout=self._backoff)
            self._backoff = min(self._backoff * 2, 300.0)

    def _connect(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5.0)
        try:
            if self.mode == 'client':
                sock.connect((self.ip, self.port))
                sock.setblocking(False)
                log.info("connected to %s:%d", self.ip, self.port)
                return sock
            else:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((self.ip, self.port))
                sock.listen(1)
                log.info("waiting for connection on %s:%d", self.ip, self.port)
                conn, addr = sock.accept()
                sock.close()
                conn.setblocking(False)
                log.info("accepted connection from %s", addr)
                return conn
        except Exception as e:
            sock.close()
            log.debug("connect attempt failed: %s", e)
            return None

    # ── Send loop ─────────────────────────────────────────────────────────────

    def _send_loop(self):
        """Event-driven — wakes on table change or keepalive timeout."""
        while self.running and self.conn:
            self._send_event.wait(timeout=KEEP_ALIVE_INTERVAL)
            self._send_event.clear()

            if not self.conn:
                break

            # Collect changed output signals
            pending = []
            for name in self._output_signals:
                val = self._table.read(name)
                if val != self._last_sent.get(name, -1):
                    pending.append((name, val))
                    self._last_sent[name] = val

            # Send in batches of 4
            while pending and self.conn:
                batch   = pending[:4]
                pending = pending[4:]
                try:
                    self.conn.send(_make_packet(batch))
                    self._last_ka = time.time()
                    for name, val in batch:
                        log.debug("SEND %s = %d", name, val)
                except Exception as e:
                    log.error("send error: %s", e)
                    return

            # Keepalive
            if time.time() - self._last_ka >= KEEP_ALIVE_INTERVAL:
                try:
                    self.conn.send(KEEP_ALIVE_PACKET)
                    self._last_ka = time.time()
                except Exception as e:
                    log.error("keepalive error: %s", e)
                    return

            # Dead-connection check — nothing heard for 3× keepalive interval
            if (self._last_recv > 0 and
                    time.time() - self._last_recv > DEAD_TIMEOUT):
                log.warning("no data received for %.0fs — treating connection as dead",
                            time.time() - self._last_recv)
                return

    def _on_table_change(self, name, value, source):
        """Wake send loop immediately when any xkop.o.* signal changes."""
        if name.startswith('xkop.o.'):
            self._send_event.set()

    # ── Receive loop ──────────────────────────────────────────────────────────

    def _recv_loop(self):
        """Receive XKOP packets → write xkop.i.* signals to table."""
        buf = bytearray()
        while self.running and self.conn:
            try:
                data = self.conn.recv(4096)
                if not data:
                    break
                self._last_recv = time.time()
                buf.extend(data)

                while len(buf) >= PACKET_LENGTH:
                    pkt = buf[:PACKET_LENGTH]
                    buf = buf[PACKET_LENGTH:]

                    if pkt[:2] != HEADER:
                        # Resync: discard one byte and retry
                        buf = bytearray(pkt[1:]) + buf
                        break

                    if pkt[2] == DATA_PACKET_TYPE:
                        for s in range(4):
                            xkop_idx = pkt[3 + s*3]
                            if xkop_idx == 0xFF:
                                continue
                            val  = (pkt[4 + s*3] << 8) | pkt[5 + s*3]
                            name = f"xkop.i.{xkop_idx}"
                            ok   = self._table.write(name, val, DRIVER_SOURCE)
                            if ok and val != 0:
                                log.debug("RECV %s = %d", name, val)

            except BlockingIOError:
                time.sleep(0.02)
            except Exception as e:
                log.error("recv error: %s", e)
                break

        # Wake send loop so it exits promptly
        self._send_event.set()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _zero_inputs(self):
        """Write 0 to all owned xkop.i.* signals — called on every (re)connect."""
        for name in self._input_signals:
            self._table.write(name, 0, DRIVER_SOURCE)


# ── Entry point ───────────────────────────────────────────────────────────────

def start(table, config_path):
    """Called by server.py main() when driver = xkop in platform.cfg."""
    platform_path = os.path.join(os.path.dirname(config_path), 'platform.cfg')
    plat = configparser.ConfigParser()
    plat.read(platform_path)

    if not plat.has_section('xkop'):
        log.error("no [xkop] section in %s — driver not started", platform_path)
        return

    ip   = plat.get('xkop', 'ip',   fallback='127.0.0.1')
    port = plat.getint('xkop', 'port', fallback=8001)
    mode = plat.get('xkop', 'mode', fallback='client').strip()

    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read(config_path)

    input_signals  = []
    output_signals = []
    if cfg.has_section('signals'):
        for name, owner in cfg.items('signals'):
            if name.startswith('xkop.i.'):
                input_signals.append(name)
            elif name.startswith('xkop.o.'):
                output_signals.append(name)

    log.info("xkop.i.*: %d signals  xkop.o.*: %d signals",
             len(input_signals), len(output_signals))

    driver = XKOPDriver(table, ip, port, mode, input_signals, output_signals)
    driver.start()
