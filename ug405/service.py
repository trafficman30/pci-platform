# ug405/service.py
# pci.ug405 — UTC Type 2 SNMP outstation agent.
# Ported from /opt/CM5/ug405/svc_ug405.py — logic is identical.
#
# Run:
#   PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.ug405.service
#
# SNMP agent on UDP 161 — instation polls us.
# IOBus client — reads reply signals, writes control signals.
# IPC push socket /tmp/pci.ug405.live.sock → web.

import datetime
import logging
import os
import signal
import socket
import threading
import time

from pysnmp.entity import engine, config
from pysnmp.entity.rfc3413 import cmdrsp, context
from pysnmp.carrier.asyncore.dgram import udp
from pysnmp.proto import rfc1902

from pci.ug405.protocol.config import (
    load_ug405, persist_live,
    REPLY_COLS, CONTROL_COLS, BITMASK_REPLY, BITMASK_CONTROL,
    scn_to_oid_suffix, sig_raw, sig_read, sig_to_id,
)
from pci.ug405.protocol.scoot import ScootSampler
from pci.ug405.protocol.rbe import RBEService
from pci.ug405.iobus_client import UG405IOBus
from pci.ug405.ipc.server import IPCServer

log = logging.getLogger('pci.ug405')

COMMUNITY   = 'UTMC'
LISTEN_IP   = '0.0.0.0'
LISTEN_PORT = int(os.getenv('PCI_UG405_SNMP_PORT', '161'))

VERSION_INFO = {
    'MIBVersion':    '1.0',
    'AppVersion':    '1.0.0',
    'AppPartNumber': 'SWARCO-PCI',
    'VendorID':      'Swarco',
    'HardwareType':  'PCI',
    'HardwareID':    '',
}

CFG_LIMITS = {
    'InstationPort':                 (1,     65535),
    'OperationModeTimeout':          (0,     180),
    'ScootSampleReportInterval':     (1,     16),
    'ReplyByException':              (0,     1),
    'ReplyByExceptionRetryDelay':    (1,     10000),
    'ReplyByExceptionRetryCount':    (0,     100),
    'ReplyByExceptionKeepAlive':     (0,     30),
    'ReplyByExceptionResendHoldoff': (0,     180),
}

NOTIFY_QUEUE_LIMIT     = 10
LATE_TIMESTAMP_LIMIT   = 30
FUTURE_TIMESTAMP_LIMIT = 10
CLOCK_JITTER_ALLOWED   = 10


def _valid_ip(addr):
    try:
        socket.inet_aton(str(addr))
        return True
    except socket.error:
        return False


def _validate_timestamp(ts_value):
    if ts_value == 0 or ts_value == 1:
        return True, None
    now      = time.time()
    midnight = now - (now % 86400)
    ts_abs   = midnight + ts_value
    age      = now - ts_abs
    if age > LATE_TIMESTAMP_LIMIT:
        return False, f"stale timestamp ({age:.0f}s old)"
    if age < -FUTURE_TIMESTAMP_LIMIT:
        return False, f"future timestamp ({-age:.0f}s ahead)"
    return True, None


class UG405Service:
    """
    UTC Type 2 SNMP outstation agent.

    io       : UG405IOBus
    cfg      : dict from load_ug405()
    cfg_path : str — path to ug405.cfg (for persist_live)
    ipc      : IPCServer — for pushing state to web
    """

    def __init__(self, io, cfg, cfg_path, ipc):
        self._io       = io
        self._cfg      = cfg
        self._cfg_path = cfg_path
        self._ipc      = ipc

        self.live    = cfg['live']
        self.op_mode = {'value': 1, 'last_set': 0.0}

        # Control signals — all owned by pci.ug405, zeroed on standalone drop
        self._control_signals = list(cfg['signals'].keys())

        self.notify_depth_ref = [0]

        self._io_fault_timeout = int(cfg['services'].get('io_fault_timeout', '30'))
        self._fault_timer      = None
        self._fault_lock       = threading.Lock()
        log.info("IO fault standalone delay: %ds", self._io_fault_timeout)

        self._clock_jitter_enabled = (
            cfg['services'].get('clock_jitter_check', 'disabled') == 'enabled')
        self._clock_jitter_grace = int(
            cfg['services'].get('clock_jitter_grace', '60'))
        self._last_clock   = time.time()
        self._opmode2_time = None

        if self._clock_jitter_enabled:
            log.info("Clock jitter detection enabled (%ds tolerance, %ds grace)",
                     CLOCK_JITTER_ALLOWED, self._clock_jitter_grace)
        else:
            log.info("Clock jitter detection disabled")

        # Build reply signal sets for subscribe filter
        self._reply_signals   = set()
        self._inverted_signals = set()
        for scn in cfg['scns']:
            for bits in cfg['reply'][scn].values():
                for sig in bits.values():
                    raw = sig_raw(sig)
                    self._reply_signals.add(raw)
                    if sig.startswith('!'):
                        self._inverted_signals.add(raw)

        io.subscribe(self._on_bus_change)

        # SCOOT sampler
        _scoot_change_only = (
            cfg['services'].get('scoot_inform_on_change_only', 'true').lower() == 'true')

        def _on_scoot_sample(scn, packed):
            if self._rbe and self.op_mode['value'] >= 2:
                log.debug("SCOOT %s packed=%s", scn, packed.hex())
                self._rbe.push_scoot(scn, packed, change_only=_scoot_change_only)

        self._scoot = ScootSampler(io, cfg, self.live, on_sample=_on_scoot_sample)
        self._rbe   = None

    # ── IPC push helpers ──────────────────────────────────────────────────────

    def _push_update(self, **kw):
        self._ipc.push(**kw)

    def _snapshot(self):
        """Full state snapshot for 1Hz IPC push."""
        changes = []
        for scn in self._cfg['scns']:
            for field, bits in self._cfg['control'][scn].items():
                for bit, sig in bits.items():
                    changes.append([sig_to_id('ctrl', sig), self._io.read(sig)])
            for field, bits in self._cfg['reply'][scn].items():
                for bit, sig in bits.items():
                    changes.append([sig_to_id('reply', sig), self._io.read(sig)])
        return {
            'opmode':    self.op_mode['value'],
            'instation': f"{self.live['InstationAddress']}:{self.live['InstationPort']}",
            'changes':   changes,
        }

    # ── Bus change subscriber ─────────────────────────────────────────────────

    def _on_bus_change(self, name, value, source):
        if name not in self._reply_signals:
            return
        v = (1 - value) if name in self._inverted_signals else value
        self._push_update(changes=[[sig_to_id('reply', name), v]])

    # ── IO fault / standalone handling ────────────────────────────────────────

    def _schedule_standalone(self, reason):
        delay = self._io_fault_timeout
        if delay <= 0:
            self._drop_to_standalone(reason)
            return
        with self._fault_lock:
            if self._fault_timer is not None:
                return
            log.warning("IO fault: %s — standalone in %ds if not recovered",
                        reason, delay)
            self._fault_timer = threading.Timer(
                delay, self._fault_timeout, args=[reason])
            self._fault_timer.daemon = True
            self._fault_timer.start()

    def _fault_timeout(self, reason):
        with self._fault_lock:
            self._fault_timer = None
        self._drop_to_standalone(reason)

    def _cancel_fault(self):
        with self._fault_lock:
            if self._fault_timer is not None:
                self._fault_timer.cancel()
                self._fault_timer = None
                log.info("IO connection recovered — standalone countdown cancelled")

    def _drop_to_standalone(self, reason):
        cur = self.op_mode['value']
        if cur > 1:
            log.warning("opMode → standalone(1): %s", reason)
            self.op_mode['value']  = 1
            self._opmode2_time     = None
            self._io.zero_owned(self._control_signals)
            self._push_update(
                opmode=1,
                set_log_entry={
                    'ts':     datetime.datetime.now().strftime('%H:%M:%S'),
                    'type':   'opMode',
                    'scn':    None,
                    'field':  'opMode',
                    'value':  f'{cur}→1',
                    'reason': reason,
                }
            )

    # ── Watchdog ──────────────────────────────────────────────────────────────

    def _watchdog(self):
        while True:
            time.sleep(5)
            if self.op_mode['value'] > 1:
                timeout = self.live['OperationModeTimeout']
                if timeout > 0 and (time.time() - self.op_mode['last_set']) > timeout:
                    self._drop_to_standalone('opMode timeout')

                if self.notify_depth_ref[0] > NOTIFY_QUEUE_LIMIT:
                    self._drop_to_standalone(
                        f"notification backlog ({self.notify_depth_ref[0]})")
                    self.notify_depth_ref[0] = 0

                now = time.time()
                if self._clock_jitter_enabled:
                    grace_elapsed = (
                        (now - self._opmode2_time) >= self._clock_jitter_grace
                        if self._opmode2_time else False)
                    if grace_elapsed:
                        jitter = abs(now - (self._last_clock + 5.0))
                        if jitter > CLOCK_JITTER_ALLOWED:
                            self._drop_to_standalone(f"clock jitter ({jitter:.1f}s)")
                self._last_clock = now

    # ── Start ─────────────────────────────────────────────────────────────────

    def start(self):
        self._ipc.set_snapshot_callback(self._snapshot)
        threading.Thread(target=self._watchdog, daemon=True,
                         name='ug405-watchdog').start()
        self._scoot.start()

        if self.live.get('ReplyByException', 0):
            self._start_rbe()

        self._run_snmp()   # blocks

    def _start_rbe(self):
        self._rbe = RBEService(
            io               = self._io,
            mapping          = self._cfg,
            live             = self.live,
            op_mode          = self.op_mode,
            notify_depth_ref = self.notify_depth_ref,
            community        = COMMUNITY,
        )
        self._rbe.start()
        log.info("RBE sender started")

    # ── SNMP agent ────────────────────────────────────────────────────────────

    def _run_snmp(self):
        io       = self._io
        cfg      = self._cfg
        live     = self.live
        op_mode  = self.op_mode
        pu       = self._push_update
        self_ref = self
        cfg_path = self._cfg_path
        ctrl_sigs = self._control_signals

        snmpEngine  = engine.SnmpEngine()
        config.addTransport(snmpEngine, udp.domainName,
            udp.UdpTransport().openServerMode((LISTEN_IP, LISTEN_PORT)))
        config.addV1System(snmpEngine, 'utmc-area', COMMUNITY)
        config.addVacmUser(snmpEngine, 2, 'utmc-area', 'noAuthNoPriv',
                           (1, 3, 6, 1, 4, 1, 13267),
                           (1, 3, 6, 1, 4, 1, 13267))

        snmpContext = context.SnmpContext(snmpEngine)
        mibBuilder  = snmpEngine.getMibBuilder()
        MibScalar, MibScalarInstance = mibBuilder.importSymbols(
            'SNMPv2-SMI', 'MibScalar', 'MibScalarInstance')

        exports = []

        def ro_str(oid, value):
            return [MibScalar(oid, rfc1902.OctetString()).setMaxAccess('readonly'),
                    MibScalarInstance(oid, (0,), rfc1902.OctetString(value))]

        def ro_int(oid, value):
            return [MibScalar(oid, rfc1902.Integer32()).setMaxAccess('readonly'),
                    MibScalarInstance(oid, (0,), rfc1902.Integer32(value))]

        def rw_str(oid, value, key):
            class RWStr(MibScalarInstance):
                def getValue(self, name, idx):
                    return self.syntax.clone(live[key])
                def setValue(self, value, name, idx):
                    v = str(value)
                    if key == 'InstationAddress':
                        if not _valid_ip(v):
                            log.warning("Config SET rejected: %s='%s' (invalid IP)", key, v)
                            return self.syntax.clone(live[key])
                    live[key] = v
                    log.info("Config SET: %s = %s", key, v)
                    if cfg_path:
                        persist_live(live, cfg_path)
                    pu(instation=f"{live['InstationAddress']}:{live['InstationPort']}",
                       set_log_entry={
                           'ts':    datetime.datetime.now().strftime('%H:%M:%S'),
                           'type':  'Config', 'scn': None,
                           'field': key, 'value': str(v),
                       })
                    return self.syntax.clone(v)
            return [MibScalar(oid, rfc1902.OctetString()).setMaxAccess('readwrite'),
                    RWStr(oid, (0,), rfc1902.OctetString(value))]

        def rw_int(oid, value, key):
            class RWInt(MibScalarInstance):
                def getValue(self, name, idx):
                    return self.syntax.clone(live[key])
                def setValue(self, value, name, idx):
                    v = int(value)
                    if key in CFG_LIMITS:
                        lo, hi = CFG_LIMITS[key]
                        if not (lo <= v <= hi):
                            log.warning("Config SET rejected: %s=%s (range %d-%d)",
                                        key, v, lo, hi)
                            return self.syntax.clone(live[key])
                    live[key] = v
                    log.info("Config SET: %s = %s", key, v)
                    if cfg_path:
                        persist_live(live, cfg_path)
                    if key == 'ReplyByException' and v == 1 and not self_ref._rbe:
                        self_ref._start_rbe()
                    pu(set_log_entry={
                        'ts':    datetime.datetime.now().strftime('%H:%M:%S'),
                        'type':  'Config', 'scn': None,
                        'field': key, 'value': str(v),
                    })
                    return self.syntax.clone(v)
            return [MibScalar(oid, rfc1902.Integer32()).setMaxAccess('readwrite'),
                    RWInt(oid, (0,), rfc1902.Integer32(value))]

        # ── .3.2.1.x — version ───────────────────────────────────────────────
        BASE_VER = (1, 3, 6, 1, 4, 1, 13267, 3, 2, 1)
        for col, key in enumerate(
                ['MIBVersion', 'AppVersion', 'AppPartNumber',
                 'VendorID', 'HardwareType', 'HardwareID'], start=1):
            exports += ro_str(BASE_VER + (col,), VERSION_INFO[key])

        # ── .3.2.2.x — config ────────────────────────────────────────────────
        BASE_CFG = (1, 3, 6, 1, 4, 1, 13267, 3, 2, 2)
        exports += ro_str(BASE_CFG + (1,),  live['ConfigLastChanged'])
        exports += rw_str(BASE_CFG + (2,),  live['InstationAddress'],              'InstationAddress')
        exports += rw_int(BASE_CFG + (3,),  live['InstationPort'],                 'InstationPort')
        exports += rw_int(BASE_CFG + (4,),  live['OperationModeTimeout'],          'OperationModeTimeout')
        exports += rw_int(BASE_CFG + (5,),  live['ScootSampleReportInterval'],     'ScootSampleReportInterval')
        exports += rw_int(BASE_CFG + (6,),  live['ReplyByException'],              'ReplyByException')
        exports += rw_int(BASE_CFG + (7,),  live['ReplyByExceptionRetryDelay'],    'ReplyByExceptionRetryDelay')
        exports += rw_int(BASE_CFG + (8,),  live['ReplyByExceptionRetryCount'],    'ReplyByExceptionRetryCount')
        exports += rw_int(BASE_CFG + (9,),  live['ReplyByExceptionKeepAlive'],     'ReplyByExceptionKeepAlive')
        exports += rw_int(BASE_CFG + (10,), live['ReplyByExceptionResendHoldoff'], 'ReplyByExceptionResendHoldoff')

        # ── .3.2.3.x — status ────────────────────────────────────────────────
        BASE_STS = (1, 3, 6, 1, 4, 1, 13267, 3, 2, 3)
        exports += ro_int(BASE_STS + (1,), self_ref._scoot.detector_count)

        class LiveTimestamp(MibScalarInstance):
            def getValue(self, name, idx):
                return self.syntax.clone(
                    datetime.datetime.utcnow().strftime('%Y%m%d%H%M%S') + 'Z')

        exports += [
            MibScalar(BASE_STS + (2,), rfc1902.OctetString()).setMaxAccess('readonly'),
            LiveTimestamp(BASE_STS + (2,), (0,), rfc1902.OctetString()),
        ]

        # ── .3.2.4.1 — operation mode ─────────────────────────────────────────
        BASE_CTL = (1, 3, 6, 1, 4, 1, 13267, 3, 2, 4)

        class OpModeInstance(MibScalarInstance):
            def getValue(self, name, idx):
                return self.syntax.clone(op_mode['value'])

            def setValue(self, value, name, idx):
                new = int(value)
                cur = op_mode['value']
                if new < 1 or new > 3 or new > cur + 1:
                    log.warning("opMode REJECTED %d→%d", cur, new)
                    return self.syntax.clone(cur)
                op_mode['value']    = new
                op_mode['last_set'] = time.time()
                if new < 3:
                    io.zero_owned(ctrl_sigs)
                if new >= 2 and self_ref._opmode2_time is None:
                    self_ref._opmode2_time = time.time()
                    log.info("Clock jitter grace period started (%ds)",
                             self_ref._clock_jitter_grace)
                log.info("opMode %d → %d  instation=%s:%s",
                         cur, new,
                         live.get('InstationAddress', '?'),
                         live.get('InstationPort', '?'))
                pu(opmode=new,
                   set_log_entry={
                       'ts':     datetime.datetime.now().strftime('%H:%M:%S'),
                       'type':   'opMode', 'scn': None,
                       'field':  'opMode', 'value': f'{cur}→{new}',
                       'reason': 'instation SET',
                   })
                return self.syntax.clone(new)

        exports += [
            MibScalar(BASE_CTL + (1,), rfc1902.Integer32()).setMaxAccess('readwrite'),
            OpModeInstance(BASE_CTL + (1,), (0,), rfc1902.Integer32(1)),
        ]

        # ── .3.2.5.1.1 / .3.2.4.2.1 — reply and control tables ───────────────
        BASE_REPLY  = (1, 3, 6, 1, 4, 1, 13267, 3, 2, 5, 1, 1)
        BASE_CTRL   = (1, 3, 6, 1, 4, 1, 13267, 3, 2, 4, 2, 1)
        TIMESTAMP   = 1
        registered_cols = set()

        for scn in cfg['scns']:
            scn_suffix = tuple(int(x) for x in scn_to_oid_suffix(scn).split('.'))
            inst_idx   = (TIMESTAMP,) + scn_suffix

            # Reply table
            for field, bits in cfg['reply'][scn].items():
                col     = REPLY_COLS[field]
                col_oid = BASE_REPLY + (col,)

                if col_oid not in registered_cols:
                    if field in BITMASK_REPLY:
                        exports.append(
                            MibScalar(col_oid, rfc1902.OctetString()).setMaxAccess('readonly'))
                    else:
                        exports.append(
                            MibScalar(col_oid, rfc1902.Integer32()).setMaxAccess('readonly'))
                    registered_cols.add(col_oid)

                if field == 'VSn':
                    class ScootReply(MibScalarInstance):
                        def __init__(self, *a, _scn=scn, **kw):
                            super().__init__(*a, **kw)
                            self._scn = _scn
                        def getValue(self, name, idx):
                            return self.syntax.clone(
                                self_ref._scoot.get_packed(self._scn) or b'\x00')
                    exports.append(
                        ScootReply(col_oid, inst_idx, rfc1902.OctetString(b'\x00')))

                elif field in BITMASK_REPLY:
                    class ReplyBitmask(MibScalarInstance):
                        def __init__(self, *a, _bits=bits, **kw):
                            super().__init__(*a, **kw)
                            self._bits = _bits
                        def getValue(self, name, idx):
                            result = 0
                            for bit, sig in self._bits.items():
                                result |= (sig_read(io, sig) & 1) << (bit - 1)
                            return self.syntax.clone(bytes([result]))
                    exports.append(
                        ReplyBitmask(col_oid, inst_idx, rfc1902.OctetString(b'\x00')))

                else:
                    sig = list(bits.values())[0]
                    class ReplySingle(MibScalarInstance):
                        def __init__(self, *a, _sig=sig, **kw):
                            super().__init__(*a, **kw)
                            self._sig = _sig
                        def getValue(self, name, idx):
                            return self.syntax.clone(sig_read(io, self._sig))
                    exports.append(
                        ReplySingle(col_oid, inst_idx, rfc1902.Integer32(0)))

            # Control table
            for field, bits in cfg['control'][scn].items():
                col     = CONTROL_COLS[field]
                col_oid = BASE_CTRL + (col,)

                if col_oid not in registered_cols:
                    if field in BITMASK_CONTROL:
                        exports.append(
                            MibScalar(col_oid, rfc1902.OctetString()).setMaxAccess('readwrite'))
                    else:
                        exports.append(
                            MibScalar(col_oid, rfc1902.Integer32()).setMaxAccess('readwrite'))
                    registered_cols.add(col_oid)

                if field in BITMASK_CONTROL:
                    class ControlBitmask(MibScalarInstance):
                        def __init__(self, *a, _bits=bits, _scn=scn, _field=field, **kw):
                            super().__init__(*a, **kw)
                            self._bits  = _bits
                            self._scn   = _scn
                            self._field = _field

                        def getValue(self, name, idx):
                            result = 0
                            for bit, sig in self._bits.items():
                                result |= (io.read(sig) & 1) << (bit - 1)
                            return self.syntax.clone(bytes([result]))

                        def setValue(self, value, name, idx):
                            ts = idx[0] if idx else 1
                            valid, reason = _validate_timestamp(ts)
                            if not valid:
                                log.warning("Control SET rejected: %s", reason)
                                return self.syntax.clone(
                                    bytes(value) if value else b'\x00')

                            raw = bytes(value)[0] if value else 0
                            log.info("Control SET: SCN %s %s = 0x%02X",
                                     self._scn, self._field, raw)
                            pu(set_log_entry={
                                'ts':    datetime.datetime.now().strftime('%H:%M:%S'),
                                'type':  'Control',
                                'scn':   self._scn,
                                'field': self._field,
                                'value': f'0x{raw:02X}',
                            })

                            # Anti-drop: set new bits first, clear old bits second
                            for bit, sig in self._bits.items():
                                v = (raw >> (bit - 1)) & 1 if op_mode['value'] >= 3 else 0
                                if v == 1:
                                    io.write(sig, 1)
                                    pu(changes=[[sig_to_id('ctrl', sig), 1]])

                            for bit, sig in self._bits.items():
                                v = (raw >> (bit - 1)) & 1 if op_mode['value'] >= 3 else 0
                                if v == 0:
                                    io.write(sig, 0)
                                    pu(changes=[[sig_to_id('ctrl', sig), 0]])

                            return self.syntax.clone(bytes([raw]))

                    exports.append(
                        ControlBitmask(col_oid, inst_idx, rfc1902.OctetString(b'\x00')))

                else:
                    sig = list(bits.values())[0]
                    class ControlSingle(MibScalarInstance):
                        def __init__(self, *a, _sig=sig, _scn=scn, _field=field, **kw):
                            super().__init__(*a, **kw)
                            self._sig   = _sig
                            self._scn   = _scn
                            self._field = _field

                        def getValue(self, name, idx):
                            return self.syntax.clone(io.read(self._sig))

                        def setValue(self, value, name, idx):
                            ts = idx[0] if idx else 1
                            valid, reason = _validate_timestamp(ts)
                            if not valid:
                                log.warning("Control SET rejected: %s", reason)
                                return self.syntax.clone(int(value))
                            v = int(value) if op_mode['value'] >= 3 else 0
                            log.info("Control SET: SCN %s %s = %d",
                                     self._scn, self._field, v)
                            io.write(self._sig, v)
                            pu(changes=[[sig_to_id('ctrl', self._sig), v]],
                               set_log_entry={
                                   'ts':    datetime.datetime.now().strftime('%H:%M:%S'),
                                   'type':  'Control',
                                   'scn':   self._scn,
                                   'field': self._field,
                                   'value': str(v),
                               })
                            return self.syntax.clone(v)

                    exports.append(
                        ControlSingle(col_oid, inst_idx, rfc1902.Integer32(0)))

        mibBuilder.exportSymbols('__UG405', *exports)

        cmdrsp.GetCommandResponder(snmpEngine, snmpContext)
        cmdrsp.NextCommandResponder(snmpEngine, snmpContext)
        cmdrsp.BulkCommandResponder(snmpEngine, snmpContext)
        cmdrsp.SetCommandResponder(snmpEngine, snmpContext)

        # SIGTERM → clean dispatcher stop
        def _sigterm(*_):
            snmpEngine.transportDispatcher.jobFinished(1)
        signal.signal(signal.SIGTERM, _sigterm)

        log.info("SNMP agent listening on %s:%d", LISTEN_IP, LISTEN_PORT)
        snmpEngine.transportDispatcher.jobStarted(1)
        try:
            snmpEngine.transportDispatcher.runDispatcher()
        except KeyboardInterrupt:
            pass
        finally:
            snmpEngine.transportDispatcher.closeDispatcher()
            log.info("SNMP agent stopped")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    cfg_path = os.environ.get(
        'PCI_UG405_CFG',
        os.path.join(os.path.dirname(__file__), '..', 'config', 'ug405.cfg'),
    )
    cfg_path = os.path.abspath(cfg_path)

    from pci.shared.log import setup
    setup('pci.ug405')

    log.info("pci.ug405 starting — config: %s", cfg_path)

    cfg = load_ug405(cfg_path)
    log.info("loaded %d SCNs, %d control signals",
             len(cfg['scns']), len(cfg['signals']))

    io = UG405IOBus()

    # Set reply signals for poll-based subscribe (raw names, no '!' prefix)
    reply_signals = set()
    for scn in cfg['scns']:
        for bits in cfg['reply'][scn].values():
            for sig in bits.values():
                reply_signals.add(sig_raw(sig))
    io.set_monitored(list(reply_signals))

    ipc = IPCServer()
    ipc.start()

    svc = UG405Service(io, cfg, cfg_path, ipc)
    svc.start()   # blocks on SNMP dispatcher


if __name__ == '__main__':
    main()
