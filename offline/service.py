# offline/service.py
# pci.offline — UTC offline fixed-time plan player.
#
# Runs fixed-time plans from offline_plan.json when the UG405 outstation is
# in standalone (opMode=1) or monitor (opMode=2). Stands down immediately
# when opMode reaches 3 (UTC control by instation).
#
# opMode is tracked by subscribing to pci.ug405's live IPC socket.
# Defaults to opMode=1 (standalone) when pci.ug405 is not running —
# plans activate rather than leaving the junction with no plan.
#
# Signal ownership: control signals must be declared as shared in signals.cfg:
#   xkop.o.101 = pci.ug405, pci.offline
#
# Run:
#   PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.offline.service

import json
import logging
import os
import signal
import socket
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta

from pci.offline.iobus_client import OfflineIOBus
from pci.offline.ipc.server   import IPCServer
from pci.ug405.protocol.config import load_ug405

log = logging.getLogger('pci.offline')

_DEFAULT_CFG   = os.path.join(os.path.dirname(__file__), '..', 'config', 'offline.cfg')
_TICK_INTERVAL = 1.0

BITMASK_CONTROL = {'Fn', 'Dn', 'SFn'}

_SINGLE_FIELDS = [
    ('go', 'GO'), ('fm', 'FM'), ('ts', 'TS'), ('so', 'SO'),
    ('mo', 'MO'), ('dx', 'DX'), ('pv', 'PV'), ('px', 'PX'),
    ('sg', 'SG'), ('lo', 'LO'), ('ll', 'LL'), ('to', 'TO'),
    ('hi', 'HI'), ('cp', 'CP'), ('ep', 'EP'), ('ff', 'FF'),
]


# ── opMode tracker ────────────────────────────────────────────────────────────

class UG405OpModeTracker:
    """
    Background subscriber to pci.ug405 live IPC socket.
    Tracks current opMode. Falls back to 1 (standalone) when disconnected —
    plans run rather than leaving the junction uncontrolled.
    """

    _LIVE_SOCK   = '/tmp/pci.ug405.live.sock'
    _RETRY_DELAY = 2.0

    def __init__(self):
        self._op_mode = 1
        self._lock    = threading.Lock()
        threading.Thread(
            target=self._read_loop, daemon=True, name='offline-opmode-track'
        ).start()

    @property
    def value(self):
        with self._lock:
            return self._op_mode

    def _read_loop(self):
        while True:
            try:
                self._connect_and_read()
            except Exception as e:
                log.debug("ug405 live socket: %s", e)
            with self._lock:
                self._op_mode = 1   # standalone on disconnect
            time.sleep(self._RETRY_DELAY)

    def _connect_and_read(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self._RETRY_DELAY)
        sock.connect(self._LIVE_SOCK)
        sock.settimeout(None)
        log.info("connected to ug405 live socket — tracking opMode")
        for line in sock.makefile('r'):
            try:
                ev = json.loads(line)
                if 'opmode' in ev:
                    with self._lock:
                        self._op_mode = ev['opmode']
                    log.debug("opMode = %d", ev['opmode'])
            except Exception:
                pass
        with self._lock:
            self._op_mode = 1


# ── Offline plan service ──────────────────────────────────────────────────────

class OfflinePlanService:
    """
    Runs fixed-time plans from offline_plan.json.

    Plan file structure (JSON):
      {
        "settings": {
          "base_time_mode": "datetime" | "time" | "none",
          "base_time": "01012020 0000"   (datetime mode) or "hhmm" (time mode),
          "active_in_modes": [1, 2]
        },
        "timetable": [
          { "days": ["Mon","Tue",...], "start": "0700", "plan": "AM" }
        ],
        "scns": {
          "J0331": {
            "plans": {
              "AM": { "cycle_secs": 90, "offsets": [ {"at":0,"fn":"0x01","dn":"0x02"} ] }
            }
          }
        }
      }
    """

    def __init__(self, iobus, scns, control, plan_file):
        self._iobus      = iobus
        self._scns       = scns       # list of SCN name strings
        self._control    = control    # {scn: {field: {bit: signal}}}
        self._plan_file  = plan_file

        self._settings    = {}
        self._active_modes= {1, 2}
        self._timetable   = []
        self._plan_config = {}
        self._file_mtime  = 0

        self._active_plan     = {}   # scn → plan_name | None
        self._activation_time = {}   # scn → monotonic at activation
        self._last_offset_idx = {}   # scn → last applied offset index

        self._state_lock = threading.Lock()

        self._load_plan_file()

    # ── File load / hot-reload ────────────────────────────────────────────────

    def _load_plan_file(self):
        try:
            mtime = os.path.getmtime(self._plan_file)
            with open(self._plan_file) as f:
                raw = json.load(f)

            settings  = raw.get('settings', {})
            timetable = raw.get('timetable', [])
            scns      = raw.get('scns', {})

            # Pre-parse bitmask strings ("0x01" etc.) to int at load time.
            for scn_data in scns.values():
                for plan in scn_data.get('plans', {}).values():
                    for offset in plan.get('offsets', []):
                        for k in ('fn', 'dn', 'sfn'):
                            if k in offset and isinstance(offset[k], str):
                                offset[k] = int(offset[k], 0)

            with self._state_lock:
                self._settings     = settings
                self._timetable    = timetable
                self._plan_config  = scns
                self._active_modes = set(settings.get('active_in_modes', [1, 2]))
                self._file_mtime   = mtime

            log.info("plan file loaded: %s  SCNs=%s  tt_entries=%d  modes=%s  time_mode=%s",
                     self._plan_file, list(scns.keys()), len(timetable),
                     sorted(set(settings.get('active_in_modes', [1, 2]))),
                     settings.get('base_time_mode', 'none'))
        except FileNotFoundError:
            log.warning("plan file not found: %s — service inactive", self._plan_file)
        except Exception as e:
            log.error("plan file error (%s): %s — keeping last config", self._plan_file, e)

    def reload(self):
        with self._state_lock:
            self._active_plan.clear()
            self._activation_time.clear()
            self._last_offset_idx.clear()
        self._load_plan_file()
        log.info("plan file force-reloaded")

    # ── Main tick (called every second) ──────────────────────────────────────

    def tick(self, op_mode):
        # Hot-reload: check mtime each tick.
        try:
            mtime = os.path.getmtime(self._plan_file)
            with self._state_lock:
                old_mtime = self._file_mtime
            if mtime != old_mtime:
                log.info("plan file changed — reloading")
                with self._state_lock:
                    self._active_plan.clear()
                    self._activation_time.clear()
                    self._last_offset_idx.clear()
                self._load_plan_file()
        except Exception:
            pass

        now = datetime.now()

        with self._state_lock:
            active_modes  = self._active_modes
            plan_config   = self._plan_config
            settings      = self._settings
            timetable     = self._timetable

        active = op_mode < 3 and op_mode in active_modes

        # Resolve timetable once — applies to all SCNs.
        plan_name = None
        if active:
            entry = self._resolve_timetable(timetable, now)
            if entry and entry.get('plan') is not None:
                plan_name = entry['plan']

        for scn in self._scns:
            if scn not in plan_config:
                continue

            scn_plans = plan_config[scn].get('plans', {})

            if active and plan_name and plan_name in scn_plans:
                with self._state_lock:
                    if self._active_plan.get(scn) != plan_name:
                        prev = self._active_plan.get(scn, 'none')
                        log.info("SCN %s: activating plan %s (was %s)", scn, plan_name, prev)
                        self._active_plan[scn]     = plan_name
                        self._activation_time[scn] = time.monotonic()
                        self._last_offset_idx[scn] = -1
                cycle_pos = self._calc_cycle_pos(scn, plan_name, now, settings, plan_config)
                self._apply_offsets(scn, plan_name, cycle_pos, plan_config)
            else:
                with self._state_lock:
                    was_active = self._active_plan.get(scn) is not None
                if was_active:
                    reason = f'opMode={op_mode}' if not active else 'no matching plan'
                    log.info("SCN %s: standing down (%s) — zeroing outputs", scn, reason)
                    self._zero_scn(scn)
                with self._state_lock:
                    self._active_plan[scn] = None
                    self._last_offset_idx.pop(scn, None)

    # ── Timetable resolution ──────────────────────────────────────────────────

    def _resolve_timetable(self, timetable, now):
        """
        Latest 'start' <= now for today's day. Carries forward previous day's
        last entry if nothing matches yet today (midnight carryover).
        """
        day  = now.strftime('%a')
        hhmm = now.strftime('%H%M')

        best = None
        for entry in timetable:
            if day not in entry.get('days', []):
                continue
            start = entry.get('start', '0000')
            if start <= hhmm:
                if best is None or start > best.get('start', '0000'):
                    best = entry

        if best is not None:
            return best

        # Midnight carryover — carry previous day's last entry.
        prev_day  = (now - timedelta(days=1)).strftime('%a')
        prev_best = None
        for entry in timetable:
            if prev_day not in entry.get('days', []):
                continue
            start = entry.get('start', '0000')
            if prev_best is None or start > prev_best.get('start', '0000'):
                prev_best = entry

        return prev_best

    # ── Cycle position ────────────────────────────────────────────────────────

    def _calc_cycle_pos(self, scn, plan_name, now, settings, plan_config):
        cycle_secs = plan_config[scn]['plans'][plan_name]['cycle_secs']
        mode       = settings.get('base_time_mode', 'none')
        base_time  = settings.get('base_time', '')

        if mode == 'datetime':
            try:
                dp, tp  = base_time.strip().split()
                dd, mm_ = int(dp[0:2]), int(dp[2:4])
                yyyy    = int(dp[4:8])
                hh, mi  = int(tp[0:2]), int(tp[2:4])
                base_dt = datetime(yyyy, mm_, dd, hh, mi, 0)
                elapsed = (now - base_dt).total_seconds()
                return elapsed % cycle_secs
            except Exception as e:
                log.warning("datetime parse error (%s): %s", base_time, e)
                return 0.0

        if mode == 'time':
            try:
                hh, mi   = int(base_time[0:2]), int(base_time[2:4])
                base_sec = hh * 3600 + mi * 60
                midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
                now_sec  = (now - midnight).total_seconds()
                elapsed  = now_sec - base_sec if now_sec >= base_sec \
                           else (86400 - base_sec) + now_sec
                return elapsed % cycle_secs
            except Exception as e:
                log.warning("time parse error (%s): %s", base_time, e)
                return 0.0

        # mode == 'none': time since plan activation on this machine.
        with self._state_lock:
            act = self._activation_time.get(scn, time.monotonic())
        return (time.monotonic() - act) % cycle_secs

    # ── Offset application ────────────────────────────────────────────────────

    def _apply_offsets(self, scn, plan_name, cycle_pos, plan_config):
        offsets = plan_config[scn]['plans'][plan_name]['offsets']
        control = self._control.get(scn, {})

        active_idx = -1
        for i, off in enumerate(offsets):
            if off['at'] <= cycle_pos:
                active_idx = i

        with self._state_lock:
            if active_idx == self._last_offset_idx.get(scn, -2):
                return
            self._last_offset_idx[scn] = active_idx

        if active_idx < 0:
            return

        off = offsets[active_idx]
        log.info("SCN %s  %s  @%ds (pos=%.1fs)  fn=%s dn=%s",
                 scn, plan_name, off['at'], cycle_pos,
                 hex(off['fn']) if 'fn' in off else '-',
                 hex(off['dn']) if 'dn' in off else '-')

        # Bitmask fields — anti-drop: set new=1 bits first, clear new=0 bits second.
        for field in BITMASK_CONTROL:
            key = field.lower()
            if key not in off or field not in control:
                continue
            mask = int(off[key])
            bits = control[field]
            for bit, sig in bits.items():
                if (mask >> (bit - 1)) & 1:
                    self._iobus.write(sig, 1)
            for bit, sig in bits.items():
                if not ((mask >> (bit - 1)) & 1):
                    self._iobus.write(sig, 0)

        # Single-bit fields.
        for json_key, field in _SINGLE_FIELDS:
            if json_key not in off or field not in control:
                continue
            sig = list(control[field].values())[0]
            self._iobus.write(sig, int(off[json_key]))

    def _zero_scn(self, scn):
        for field_bits in self._control.get(scn, {}).values():
            for sig in field_bits.values():
                self._iobus.write(sig, 0)

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self):
        now = datetime.now()
        with self._state_lock:
            plan_config  = self._plan_config
            settings     = self._settings
            active_modes = sorted(self._active_modes)
            timetable    = self._timetable

        scns_out  = {}
        plans_out = {}

        for scn in self._scns:
            if scn not in plan_config:
                continue

            scn_data = plan_config[scn]
            control  = self._control.get(scn, {})

            with self._state_lock:
                active_plan = self._active_plan.get(scn)
                offset_idx  = self._last_offset_idx.get(scn, -1)

            cycle_pos  = 0.0
            cycle_secs = 0
            if active_plan and active_plan in scn_data.get('plans', {}):
                cycle_secs = scn_data['plans'][active_plan]['cycle_secs']
                try:
                    cycle_pos = self._calc_cycle_pos(scn, active_plan, now, settings, plan_config)
                except Exception:
                    pass

            scns_out[scn] = {
                'active_plan': active_plan,
                'cycle_pos':   round(cycle_pos, 1),
                'cycle_secs':  cycle_secs,
                'offset_idx':  offset_idx,
            }

            plans_out[scn] = {}
            for plan_name, plan in scn_data.get('plans', {}).items():
                decoded = []
                for off in plan['offsets']:
                    bits = {}
                    fn_mask  = int(off.get('fn',  0))
                    dn_mask  = int(off.get('dn',  0))
                    sfn_mask = int(off.get('sfn', 0))
                    for bit in sorted(control.get('Fn',  {}).keys()):
                        bits[f'Fn[{bit}]']  = (fn_mask  >> (bit - 1)) & 1
                    for bit in sorted(control.get('Dn',  {}).keys()):
                        bits[f'Dn[{bit}]']  = (dn_mask  >> (bit - 1)) & 1
                    for bit in sorted(control.get('SFn', {}).keys()):
                        bits[f'SFn[{bit}]'] = (sfn_mask >> (bit - 1)) & 1
                    for json_key, field in _SINGLE_FIELDS:
                        if field in control:
                            bits[field] = int(off.get(json_key, 0))
                    decoded.append({'at': off['at'], 'bits': bits})
                plans_out[scn][plan_name] = {
                    'cycle_secs': plan['cycle_secs'],
                    'offsets':    decoded,
                }

        return {
            'settings': {
                'base_time_mode': settings.get('base_time_mode', 'none'),
                'base_time':      settings.get('base_time', ''),
                'active_in_modes': active_modes,
            },
            'timetable': timetable,
            'scns':      scns_out,
            'plans':     plans_out,
        }


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    import configparser

    logging.basicConfig(
        level   = logging.INFO,
        format  = '%(asctime)s pci.offline %(name)s %(levelname)s %(message)s',
        datefmt = '%H:%M:%S',
    )
    log.info("starting  pid=%d", os.getpid())

    cfg_path = os.getenv('PCI_OFFLINE_CFG', _DEFAULT_CFG)
    cfg = configparser.ConfigParser()
    cfg.read(cfg_path)

    plan_file  = cfg.get('offline', 'plan_file',
                         fallback=os.path.join(os.path.dirname(cfg_path), 'offline_plan.json'))
    ug405_path = cfg.get('offline', 'ug405_cfg',
                         fallback=os.path.join(os.path.dirname(cfg_path), 'ug405.cfg'))

    if not os.path.isabs(plan_file):
        plan_file = os.path.join(os.path.dirname(cfg_path), plan_file)
    if not os.path.isabs(ug405_path):
        ug405_path = os.path.join(os.path.dirname(cfg_path), ug405_path)

    # Allow PCI_UG405_CFG env override (same variable as pci.ug405 uses)
    ug405_path = os.getenv('PCI_UG405_CFG', ug405_path)

    log.info("plan_file=%s  ug405_cfg=%s", plan_file, ug405_path)

    ug405_cfg = load_ug405(ug405_path)
    scns      = ug405_cfg['scns']
    control   = ug405_cfg['control']
    log.info("SCNs: %s", scns)

    iobus     = OfflineIOBus()
    op_tracker= UG405OpModeTracker()
    svc       = OfflinePlanService(iobus, scns, control, plan_file)

    ipc = IPCServer()
    ipc.set_snapshot_callback(svc.snapshot)
    ipc.set_reload_callback(svc.reload)
    ipc.start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT,  lambda *_: stop.set())

    log.info("ready")

    while not stop.is_set():
        try:
            svc.tick(op_tracker.value)
        except Exception as e:
            log.error("tick error: %s", e)
        stop.wait(_TICK_INTERVAL)

    log.info("stopping")


if __name__ == '__main__':
    main()
