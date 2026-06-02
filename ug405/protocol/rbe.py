# ug405/protocol/rbe.py
# Reply By Exception (RBE) INFORM sender for pci.ug405.
# Ported from /opt/CM5/ug405/svc_ug405_rbe.py — logic is identical.
#
# Monitors IOBus via UG405IOBus.subscribe() for reply signal changes.
# Batches changes and sends a single SNMPv2c INFORM to the instation.
# Implements retry, holdoff, keepalive, and queue depth protection.
#
# Raw SNMPv2c INFORM packet is built by hand (BER encoded) — no pysnmp
# dependency in the send path, matching the CM5 implementation exactly.

import logging
import socket
import struct
import threading
import time

from pci.ug405.protocol.config import (
    REPLY_COLS, BITMASK_REPLY, scn_to_oid_suffix, sig_raw, sig_read,
)

log = logging.getLogger('pci.ug405.rbe')

NOTIFY_OID = (1, 3, 6, 1, 4, 1, 13267, 3, 2, 6, 0, 1)
BASE_REPLY  = (1, 3, 6, 1, 4, 1, 13267, 3, 2, 5, 1, 1)
TIMESTAMP   = 1


class RBEService:
    """
    Reply By Exception INFORM sender.

    io               : UG405IOBus
    mapping          : ug405 config dict (from load_ug405)
    live             : live config dict
    op_mode          : {'value': 1/2/3} shared with UG405Service
    notify_depth_ref : [int] shared backlog counter with watchdog
    community        : SNMP community string
    """

    def __init__(self, io, mapping, live, op_mode,
                 notify_depth_ref=None, community='UTMC'):
        self.io               = io
        self.mapping          = mapping
        self.live             = live
        self.op_mode          = op_mode
        self.notify_depth_ref = notify_depth_ref or [0]
        self.community        = community

        self._last_sent       = {}
        self._last_inform     = 0.0
        self._pending         = {}
        self._lock            = threading.Lock()
        self._event           = threading.Event()
        self._scn_last_inform = {}
        self._request_id      = 1

        self._reply_signals = set()
        self._vsn_signals   = set()
        for scn in mapping['scns']:
            for field, bits in mapping['reply'][scn].items():
                for sig in bits.values():
                    self._reply_signals.add(sig_raw(sig))
                    if field == 'VSn':
                        self._vsn_signals.add(sig_raw(sig))

        self._last_scoot_packed = {}
        self._last_scoot_inform = {}

        io.subscribe(self._on_bus_change)

    def _on_bus_change(self, name, value, source):
        if name not in self._reply_signals:
            return
        if self.op_mode['value'] < 2:
            return
        if name in self._vsn_signals:
            return
        with self._lock:
            self._pending[name] = value
        self._event.set()

    def start(self):
        threading.Thread(target=self._send_loop,      daemon=True,
                         name='ug405-rbe-send').start()
        threading.Thread(target=self._keepalive_loop, daemon=True,
                         name='ug405-rbe-keepalive').start()
        log.info("RBE service started")

    def push_scoot(self, scn, packed, change_only=True):
        """Called by ScootSampler at each interval — sends INFORM with VSn data."""
        if self.op_mode['value'] < 2:
            return

        keepalive  = self.live.get('ReplyByExceptionKeepAlive', 0)
        last_sent  = self._last_scoot_packed.get(scn)
        last_time  = self._last_scoot_inform.get(scn, 0)
        time_since = time.time() - last_time

        if change_only and packed == last_sent:
            if keepalive <= 0 or time_since < keepalive:
                log.debug("SCOOT %s unchanged — suppressed", scn)
                return
            log.debug("SCOOT %s keepalive INFORM (%ds since last)", scn, int(time_since))

        vsn_signals = {}
        for field, bits in self.mapping['reply'].get(scn, {}).items():
            if field == 'VSn':
                vsn_signals.update(bits)
        if not vsn_signals:
            return

        col        = REPLY_COLS['VSn']
        scn_suffix = tuple(int(x) for x in scn_to_oid_suffix(scn).split('.'))
        inst_idx   = (1,) + scn_suffix
        col_oid    = BASE_REPLY + (col,) + inst_idx
        varbinds   = [(col_oid, 'octet', packed)]

        instation = self.live.get('InstationAddress', '0.0.0.0')
        port      = int(self.live.get('InstationPort', 1162))
        retries   = int(self.live.get('ReplyByExceptionRetryCount', 4))
        delay_ms  = int(self.live.get('ReplyByExceptionRetryDelay', 200))

        if instation in ('0.0.0.0', '', None):
            return

        log.debug("SCOOT INFORM → %s:%d  %s  packed=%s",
                  instation, port, scn, packed.hex())
        self._send_inform_direct(instation, port, varbinds, retries, delay_ms)
        self._last_scoot_packed[scn] = packed
        self._last_scoot_inform[scn] = time.time()

    def _send_inform_direct(self, instation, port, varbinds, retries, delay_ms):
        for attempt in range(retries + 1):
            try:
                req_id = self._next_request_id()
                pkt    = self._build_inform_packet(varbinds, req_id)
                sent   = self._udp_send_recv(pkt, instation, port,
                                             timeout=delay_ms / 1000.0,
                                             expected_request_id=req_id)
                if sent:
                    log.info("SCOOT INFORM acknowledged (attempt %d)", attempt + 1)
                    return
            except Exception as e:
                log.warning("SCOOT INFORM error attempt %d: %s", attempt + 1, e)

    # ── Send loop ─────────────────────────────────────────────────────────────

    def _send_loop(self):
        while True:
            self._event.wait(timeout=1.0)
            self._event.clear()

            if self.op_mode['value'] < 2:
                with self._lock:
                    self._pending.clear()
                continue

            with self._lock:
                if not self._pending:
                    continue
                batch = dict(self._pending)
                self._pending.clear()

            changed = {
                sig: val for sig, val in batch.items()
                if self._last_sent.get(sig) != val
            }
            if not changed:
                continue

            scn_signals = self._group_by_scn(changed)
            if not scn_signals:
                continue

            holdoff = self.live.get('ReplyByExceptionResendHoldoff', 1)
            blocked = False
            for scn in scn_signals:
                last = self._scn_last_inform.get(scn, 0)
                if holdoff > 0 and (time.time() - last) < holdoff:
                    log.debug("RBE holdoff active for %s", scn)
                    with self._lock:
                        self._pending.update(changed)
                    blocked = True
                    break

            if not blocked:
                self._send_inform(scn_signals)

    def _keepalive_loop(self):
        while True:
            time.sleep(5)
            if self.op_mode['value'] < 2:
                continue
            keepalive = self.live.get('ReplyByExceptionKeepAlive', 0)
            if keepalive <= 0:
                continue
            if (time.time() - self._last_inform) >= keepalive:
                log.debug("RBE keepalive INFORM")
                all_signals = {}
                for scn in self.mapping['scns']:
                    for field, bits in self.mapping['reply'][scn].items():
                        for bit, sig in bits.items():
                            all_signals[sig_raw(sig)] = sig_read(self.io, sig)
                if all_signals:
                    self._send_inform(self._group_by_scn(all_signals))

    # ── Grouping / varbind building ───────────────────────────────────────────

    def _group_by_scn(self, signals):
        result = {}
        for scn in self.mapping['scns']:
            scn_sigs = {}
            for field, bits in self.mapping['reply'][scn].items():
                for bit, sig in bits.items():
                    raw = sig_raw(sig)
                    if raw in signals:
                        scn_sigs[raw] = signals[raw]
            if scn_sigs:
                result[scn] = scn_sigs
        return result

    def _build_varbinds(self, scn_signals):
        varbinds = []
        for scn, signals in scn_signals.items():
            scn_suffix = tuple(int(x) for x in scn_to_oid_suffix(scn).split('.'))
            inst_idx   = (TIMESTAMP,) + scn_suffix

            for field, bits in self.mapping['reply'][scn].items():
                field_signals = {bit: sig for bit, sig in bits.items()
                                 if sig_raw(sig) in signals}
                if not field_signals:
                    continue

                col     = REPLY_COLS[field]
                col_oid = BASE_REPLY + (col,) + inst_idx

                if field in BITMASK_REPLY:
                    result = 0
                    for bit, sig in bits.items():
                        result |= (sig_read(self.io, sig) & 1) << (bit - 1)
                    varbinds.append((col_oid, 'octet', bytes([result])))
                else:
                    sig = list(bits.values())[0]
                    varbinds.append((col_oid, 'int', sig_read(self.io, sig)))

        return varbinds

    # ── INFORM sender ─────────────────────────────────────────────────────────

    def _send_inform(self, scn_signals):
        instation = self.live.get('InstationAddress', '0.0.0.0')
        port      = int(self.live.get('InstationPort', 1162))
        retries   = int(self.live.get('ReplyByExceptionRetryCount', 4))
        delay_ms  = int(self.live.get('ReplyByExceptionRetryDelay', 200))

        if instation in ('0.0.0.0', '', None):
            log.debug("RBE: no instation — suppressed")
            return

        varbinds = self._build_varbinds(scn_signals)
        if not varbinds:
            return

        log.debug("RBE INFORM → %s:%d  varbinds=%d  scns=%s",
                  instation, port, len(varbinds), list(scn_signals.keys()))

        sent = False
        for attempt in range(retries + 1):
            try:
                req_id = self._next_request_id()
                pkt    = self._build_inform_packet(varbinds, req_id)
                sent   = self._udp_send_recv(pkt, instation, port,
                                             timeout=delay_ms / 1000.0,
                                             expected_request_id=req_id)
                if sent:
                    log.info("RBE INFORM acknowledged (attempt %d)", attempt + 1)
                    break
                log.warning("RBE INFORM no ACK (attempt %d/%d)", attempt + 1, retries + 1)
            except Exception as e:
                log.warning("RBE INFORM error attempt %d: %s", attempt + 1, e)

        if sent:
            for scn in scn_signals:
                self._scn_last_inform[scn] = time.time()
            for scn, signals in scn_signals.items():
                for sig, val in signals.items():
                    self._last_sent[sig] = val
            self._last_inform = time.time()
            self.notify_depth_ref[0] = max(0, self.notify_depth_ref[0] - 1)
        else:
            self.notify_depth_ref[0] += 1
            log.warning("RBE INFORM failed after %d retries — queue depth=%d",
                        retries, self.notify_depth_ref[0])

    # ── Raw SNMPv2c INFORM packet builder (BER) ───────────────────────────────

    def _next_request_id(self):
        self._request_id = (self._request_id % 0x7FFFFFFF) + 1
        return self._request_id

    def _encode_oid(self, oid):
        if len(oid) < 2:
            raise ValueError("OID too short")
        body = bytes([40 * oid[0] + oid[1]])
        for v in oid[2:]:
            if v == 0:
                body += b'\x00'
            else:
                parts = []
                while v:
                    parts.append(v & 0x7F)
                    v >>= 7
                parts.reverse()
                for i, p in enumerate(parts):
                    body += bytes([p | (0x80 if i < len(parts) - 1 else 0)])
        return b'\x06' + self._encode_length(len(body)) + body

    def _encode_length(self, n):
        if n < 0x80:
            return bytes([n])
        elif n < 0x100:
            return bytes([0x81, n])
        else:
            return bytes([0x82, (n >> 8) & 0xFF, n & 0xFF])

    def _encode_int(self, v):
        if v == 0:
            return b'\x02\x01\x00'
        n = v
        parts = []
        while n:
            parts.append(n & 0xFF)
            n >>= 8
        if v > 0 and parts[-1] & 0x80:
            parts.append(0)
        parts.reverse()
        return b'\x02' + self._encode_length(len(parts)) + bytes(parts)

    def _encode_octet(self, b):
        return b'\x04' + self._encode_length(len(b)) + b

    def _encode_sequence(self, content):
        return b'\x30' + self._encode_length(len(content)) + content

    def _encode_varbind(self, oid, vtype, value):
        oid_enc = self._encode_oid(oid)
        if vtype == 'int':
            val_enc = self._encode_int(value)
        elif vtype == 'octet':
            val_enc = self._encode_octet(value)
        elif vtype == 'oid':
            val_enc = self._encode_oid(value)
        else:
            val_enc = b'\x05\x00'
        return self._encode_sequence(oid_enc + val_enc)

    def _build_inform_packet(self, varbinds, request_id):
        uptime_oid = (1, 3, 6, 1, 2, 1, 1, 3, 0)
        uptime_val = int(time.monotonic() * 100) & 0xFFFFFFFF
        uptime_enc = (self._encode_oid(uptime_oid) +
                      b'\x43' + self._encode_length(4) +
                      struct.pack('>I', uptime_val))
        uptime_vb  = self._encode_sequence(uptime_enc)

        trap_oid_oid = (1, 3, 6, 1, 6, 3, 1, 1, 4, 1, 0)
        trap_oid_enc = self._encode_oid(trap_oid_oid) + self._encode_oid(NOTIFY_OID)
        trap_oid_vb  = self._encode_sequence(trap_oid_enc)

        extra_vbs = b''
        for oid, vtype, value in varbinds:
            extra_vbs += self._encode_varbind(oid, vtype, value)

        all_vbs = self._encode_sequence(uptime_vb + trap_oid_vb + extra_vbs)

        req_id  = self._encode_int(request_id)
        err_st  = self._encode_int(0)
        err_idx = self._encode_int(0)

        pdu_body = req_id + err_st + err_idx + all_vbs
        pdu = b'\xa6' + self._encode_length(len(pdu_body)) + pdu_body

        version   = self._encode_int(1)
        community = self._encode_octet(self.community.encode())
        msg_body  = version + community + pdu
        return self._encode_sequence(msg_body)

    def _udp_send_recv(self, packet, host, port, timeout=0.5, expected_request_id=None):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        try:
            sock.sendto(packet, (host, port))
            try:
                data, addr = sock.recvfrom(4096)
                if addr[0] != host:
                    log.debug("ACK discarded: source %s != instation %s", addr[0], host)
                    return False
                if expected_request_id is not None:
                    resp_id = self._parse_response_request_id(data)
                    if resp_id != expected_request_id:
                        log.debug("ACK discarded: request-id %s != expected %d",
                                  resp_id, expected_request_id)
                        return False
                return True
            except socket.timeout:
                return False
        finally:
            sock.close()

    def _decode_length(self, data, pos):
        first = data[pos]
        if first < 0x80:
            return pos + 1, first
        n = first & 0x7F
        length = 0
        for i in range(n):
            length = (length << 8) | data[pos + 1 + i]
        return pos + 1 + n, length

    def _parse_response_request_id(self, data):
        try:
            pos = 0
            if data[pos] != 0x30:
                return None
            pos, _ = self._decode_length(data, pos + 1)
            if data[pos] != 0x02:
                return None
            pos += 1
            pos, vlen = self._decode_length(data, pos)
            pos += vlen
            if data[pos] != 0x04:
                return None
            pos += 1
            pos, clen = self._decode_length(data, pos)
            pos += clen
            if data[pos] != 0xA2:
                return None
            pos += 1
            pos, _ = self._decode_length(data, pos)
            if data[pos] != 0x02:
                return None
            pos += 1
            pos, rlen = self._decode_length(data, pos)
            return int.from_bytes(data[pos:pos + rlen], 'big')
        except Exception:
            return None
