# rtig/service.py
# pci.rtig — RTIG TLP message adapter.
#
# Receives TLP XML via IPC command socket (forwarded by pci.web RTIG HTTP listener).
# Matches rules, pulses IOBus output signals for a configurable duration.
# Pushes state to pci.web via IPC push socket.
#
# Run:
#   PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.rtig.service

import collections
import configparser
import json
import logging
import os
import signal
import threading
import time
import xml.etree.ElementTree as ET

from pci.rtig.iobus_client import RTIGIOBus
from pci.rtig.ipc.server   import IPCServer

log = logging.getLogger('pci.rtig')

_DEFAULT_CFG  = os.path.join(os.path.dirname(__file__), '..', 'config', 'rtig.cfg')
TLP_LOG_SIZE  = 50

# ── Comparator matching (ported verbatim from CM5) ────────────────────────────

_OPS = {
    '=':  lambda a, b: a == b,
    '!=': lambda a, b: a != b,
    '<':  lambda a, b: a <  b,
    '<=': lambda a, b: a <= b,
    '>':  lambda a, b: a >  b,
    '>=': lambda a, b: a >= b,
}

MATCH_FIELDS = ('traffic_signal', 'movement', 'trigger_point',
                'priority', 'schedule_deviation')


def _match_field(rule_val, msg_val):
    if isinstance(rule_val, dict):
        op  = rule_val.get('op', '=')
        ref = int(rule_val.get('val', 0))
        fn  = _OPS.get(op)
        if fn is None:
            log.warning("unknown comparator op '%s' — no-match", op)
            return False
        return fn(msg_val, ref)
    return int(rule_val) == msg_val


# ── Rule loader ───────────────────────────────────────────────────────────────

def load_rules(path):
    if not os.path.exists(path):
        log.warning("rules file not found: %s — no rules loaded", path)
        return []
    with open(path) as f:
        rules = json.load(f)
    valid = [r for i, r in enumerate(rules)
             if 'signal' in r or log.warning("rule #%d has no 'signal' — skipped", i)]
    log.info("loaded %d rule(s) from %s", len(valid), path)
    return valid


def match_rules(rules, msg):
    matched = []
    for rule in rules:
        ok = True
        for field in MATCH_FIELDS:
            if field not in rule:
                continue
            msg_val = msg.get(field)
            if msg_val is None:
                ok = False
                break
            if not _match_field(rule[field], msg_val):
                ok = False
                break
        if ok:
            matched.append(rule)
    return matched


# ── XML parser ────────────────────────────────────────────────────────────────

def parse_rtig_xml(body):
    try:
        root = ET.fromstring(body.strip())
    except ET.ParseError as e:
        log.warning("XML parse error: %s", e)
        return None
    if root.tag != 'rtig_tlp':
        log.warning("unexpected root tag '%s' (expected rtig_tlp)", root.tag)
        return None
    def _int(attr):
        v = root.get(attr)
        return int(v) if v is not None else None
    return {
        'traffic_signal':     _int('traffic_signal'),
        'movement':           _int('movement'),
        'trigger_point':      _int('trigger_point'),
        'priority':           _int('priority'),
        'schedule_deviation': _int('schedule_deviation'),
    }


# ── Pulse engine ──────────────────────────────────────────────────────────────

class PulseEngine:
    """
    Timed output pulses on the IOBus.
    pulse(signal) writes 1, resets to 0 after duration.
    A second pulse on an active signal restarts the timer.
    """

    def __init__(self, iobus, duration):
        self._iobus    = iobus
        self.duration  = duration
        self._timers   = {}
        self._lock     = threading.Lock()

    def pulse(self, signal):
        with self._lock:
            existing = self._timers.get(signal)
            if existing:
                existing.cancel()
            self._iobus.write(signal, 1)
            log.info("PULSE ON  → %s  (%.1fs)", signal, self.duration)
            t = threading.Timer(self.duration, self._expire, args=(signal,))
            t.daemon = True
            t.start()
            self._timers[signal] = t

    def _expire(self, signal):
        with self._lock:
            self._timers.pop(signal, None)
        self._iobus.write(signal, 0)
        log.info("PULSE OFF ← %s", signal)

    def active(self):
        with self._lock:
            return set(self._timers.keys())


# ── RTIG service ──────────────────────────────────────────────────────────────

class RTIGService:
    def __init__(self, iobus, rules_file, pulse_secs, signal_map):
        self._iobus      = iobus
        self._rules_file = rules_file
        self._pulse_secs = pulse_secs
        self._signal_map = signal_map
        self.rules       = load_rules(rules_file)
        self._pulser     = PulseEngine(iobus, pulse_secs)
        self._state_lock = threading.Lock()
        self.total_rx    = 0
        self.last_tlp    = None
        self.tlp_log     = collections.deque(maxlen=TLP_LOG_SIZE)

    def receive(self, xml_body):
        """Process a TLP XML body. Returns list of pulsed signal names."""
        msg = parse_rtig_xml(xml_body)
        if msg is None:
            return []

        log.info("TLP  sig=%-5s mov=%-3s trg=%-3s pri=%-4s dev=%s",
                  msg['traffic_signal'], msg['movement'], msg['trigger_point'],
                  msg['priority'], msg['schedule_deviation'])

        matched = match_rules(self.rules, msg)
        if matched:
            log.info("TLP matched %d rule(s) → %s",
                     len(matched), [r.get('signal') for r in matched])
        pulsed  = []

        for rule in matched:
            name   = rule['signal']
            signal = self._signal_map.get(name, name)
            if signal == name and not name.startswith(
                    ('xkop.', 'gp.', 'virt.', 'rpdb.', 'modb.')):
                log.warning("rule hit but '%s' not in signal_map — skipped", name)
                continue
            self._pulser.pulse(signal)
            pulsed.append(signal)

        self._record_tlp(msg, pulsed)
        return pulsed

    def reload_rules(self):
        self.rules = load_rules(self._rules_file)
        log.info("rules reloaded (%d rules)", len(self.rules))

    def snapshot(self):
        with self._state_lock:
            return {
                'pulse_secs':  self._pulse_secs,
                'rules_count': len(self.rules),
                'total_rx':    self.total_rx,
                'last_tlp':    dict(self.last_tlp) if self.last_tlp else None,
                'tlp_log':     list(self.tlp_log),
                'pulsing':     list(self._pulser.active()),
            }

    def _record_tlp(self, msg, matched_signals):
        entry = {
            'ts':                time.strftime('%H:%M:%S'),
            'traffic_signal':    msg.get('traffic_signal'),
            'movement':          msg.get('movement'),
            'trigger_point':     msg.get('trigger_point'),
            'priority':          msg.get('priority'),
            'schedule_deviation': msg.get('schedule_deviation'),
            'matched':           matched_signals,
        }
        with self._state_lock:
            self.total_rx += 1
            self.last_tlp  = entry
            self.tlp_log.appendleft(entry)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    from pci.shared.log import setup
    setup('pci.rtig')
    log.info("starting  pid=%d", os.getpid())

    cfg_path = os.getenv('PCI_RTIG_CFG', _DEFAULT_CFG)
    cfg = configparser.ConfigParser()
    cfg.read(cfg_path)

    pulse_secs = cfg.getfloat('rtig', 'pulse_seconds', fallback=2.0)
    rules_file = cfg.get('rtig', 'rules_file',
                         fallback=os.path.join(os.path.dirname(cfg_path), 'rtig_rules.json'))
    if not os.path.isabs(rules_file):
        rules_file = os.path.join(os.path.dirname(cfg_path), rules_file)

    signal_map = {}
    if cfg.has_section('signal_map'):
        signal_map = dict(cfg.items('signal_map'))

    log.info("pulse_seconds=%.1f  rules_file=%s  signal_map=%d entries",
             pulse_secs, rules_file, len(signal_map))

    iobus = RTIGIOBus()
    svc   = RTIGService(iobus, rules_file, pulse_secs, signal_map)

    ipc = IPCServer()
    ipc.set_snapshot_callback(svc.snapshot)
    ipc.set_receive_callback(svc.receive)
    ipc.set_reload_callback(svc.reload_rules)
    ipc.start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT,  lambda *_: stop.set())

    log.info("ready")
    stop.wait()
    log.info("stopping")


if __name__ == '__main__':
    main()
