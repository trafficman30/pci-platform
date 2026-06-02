"""
pci.agd — AGD650 radar detector adapter.

Subscribes to ZeroMQ PUB frames from one or more AGD650 units.
Writes zone detection signals to IOBus on change only.
Entry point: python -m pci.agd.service
"""

import json
import time
import logging
import threading
import configparser
import os
import sys
from collections import deque

from pci.agd.iobus_client import AGDIOBus
from pci.agd.ipc.server import IPCServer

log = logging.getLogger('pci.agd')

_CFG_DEFAULT = '/opt/pci/config/agd.cfg'
_SIG_CFG     = '/opt/pci/config/signals.cfg'


def _load_cfg(cfg_path):
    cp = configparser.ConfigParser(inline_comment_prefixes=('#',))
    cp.read(cfg_path)

    units = []
    for section in sorted(cp.sections()):
        if not section.upper().startswith('AGD.'):
            continue
        ip    = cp.get(section, 'ip',   fallback='127.0.0.1')
        port  = cp.getint(section, 'port', fallback=9003)
        zones = [int(x.strip()) for x in
                 cp.get(section, 'zones', fallback='1').split(',') if x.strip()]
        units.append({'id': section, 'ip': ip, 'port': port, 'zones': zones})

    classes = {}
    if cp.has_section('CLASSES'):
        for cls_name, suffix in cp.items('CLASSES'):
            classes[cls_name.strip().lower()] = suffix.strip()

    frame_timeout = cp.getfloat('SETTINGS', 'frame_timeout', fallback=5.0)
    mapping = dict(cp.items('VIRT_MAPPING')) if cp.has_section('VIRT_MAPPING') else {}

    return {
        'units':         units,
        'classes':       classes,
        'frame_timeout': frame_timeout,
        'mapping':       mapping,
    }


def _discover_owned_signals(sig_cfg_path, owner='pci.agd'):
    """Scan signals.cfg for signals owned by pci.agd."""
    cp = configparser.ConfigParser(inline_comment_prefixes=('#',))
    cp.read(sig_cfg_path)
    owned = set()
    if cp.has_section('signals'):
        for name, owners_str in cp.items('signals'):
            declared = {o.strip() for o in owners_str.split(',')}
            if owner in declared:
                owned.add(name)
    return owned


class AGDService:
    def __init__(self, cfg, io):
        self._cfg           = cfg
        self._io            = io
        self._running       = False
        self._mapping       = cfg['mapping']
        self._classes       = cfg['classes']
        self._frame_timeout = cfg['frame_timeout']
        self._events        = deque(maxlen=50)
        self._fault_units   = set()
        self._fault_lock    = threading.Lock()
        self._ipc           = IPCServer(self)

    def _sig(self, unit_id, zone, signal_type):
        unit_key   = f'{unit_id.lower()}.zone.{zone}.{signal_type}'
        global_key = f'zone.{zone}.{signal_type}'
        return self._mapping.get(unit_key) or self._mapping.get(global_key)

    def _global_sig(self, name):
        return self._mapping.get(name)

    def start(self):
        self._running = True
        self._ipc.start()
        for unit in self._cfg['units']:
            t = threading.Thread(
                target=self._unit_loop, args=(unit,),
                daemon=True, name=f"agd-{unit['id']}")
            t.start()
            log.info("AGD unit %s -> tcp://%s:%d  zones=%s",
                     unit['id'], unit['ip'], unit['port'], unit['zones'])

    def stop(self):
        self._running = False

    def _unit_loop(self, unit):
        import zmq
        unit_id    = unit['id']
        unit_zones = set(unit['zones'])
        url        = f"tcp://{unit['ip']}:{unit['port']}"
        prev_state = {}
        faulted    = False
        last_frame = time.time()

        ctx  = zmq.Context.instance()
        sock = ctx.socket(zmq.SUB)
        sock.setsockopt(zmq.SUBSCRIBE, b'')
        sock.setsockopt(zmq.RCVTIMEO, 500)
        sock.connect(url)
        log.info("AGD %s connected to %s", unit_id, url)

        while self._running:
            try:
                raw = sock.recv()
                last_frame = time.time()

                if faulted:
                    faulted = False
                    with self._fault_lock:
                        self._fault_units.discard(unit_id)
                    log.info("AGD %s frames resumed", unit_id)
                    self._ipc.push_event({'t': 'reconnect', 'ts': time.time(),
                                          'unit': unit_id})

                frame = json.loads(raw)
                self._process_frame(frame, unit_id, unit_zones, prev_state)

            except zmq.Again:
                gap = time.time() - last_frame
                if not faulted and gap > self._frame_timeout:
                    faulted = True
                    with self._fault_lock:
                        self._fault_units.add(unit_id)
                    log.warning("AGD %s no frames for %.1fs — fault", unit_id, gap)
                    self._zero_unit(unit, prev_state)
                    self._ipc.push_event({'t': 'fault', 'ts': time.time(),
                                          'unit': unit_id,
                                          'msg': f"no frames for {gap:.1f}s"})
                continue

            except Exception as e:
                log.error("AGD %s error: %s", unit_id, e)
                time.sleep(1)

        sock.close()

    def _process_frame(self, frame, unit_id, unit_zones, prev_state):
        changed_zones = []
        for zone_data in frame.get('zones', []):
            zone  = zone_data.get('index')
            state = zone_data.get('state', 2)

            if zone not in unit_zones:
                continue

            detected   = 1 if state == 1 else 0
            road_users = zone_data.get('roadusers', []) if detected else []

            classes_present = {}
            for ru in road_users:
                cls    = ru.get('className', '').lower()
                suffix = self._classes.get(cls)
                if suffix:
                    classes_present[suffix] = 1

            new_state = {'detected': detected}
            for suffix in self._classes.values():
                new_state[suffix] = classes_present.get(suffix, 0)

            old_state = prev_state.get(zone, {})
            changed   = False

            for key, val in new_state.items():
                if old_state.get(key) != val:
                    sig = self._sig(unit_id, zone, key)
                    if sig:
                        self._io.write(sig, val)
                    changed = True

            if changed:
                prev_state[zone] = new_state
                changed_zones.append(zone)
                cls_str = ', '.join(k.replace('has_', '')
                                    for k in classes_present) or '—'
                log.info("AGD %s zone %d → detected=%d  classes=%s",
                         unit_id, zone, detected, cls_str)
                self._events.append({
                    'ts':       time.strftime('%H:%M:%S'),
                    'unit':     unit_id,
                    'zone':     zone,
                    'detected': detected,
                    'classes':  ', '.join(k.replace('has_', '')
                                          for k in classes_present) or '—',
                })

        if changed_zones:
            self._update_globals()
            self._ipc.push_event({'t': 'zone_change', 'ts': time.time(),
                                   'unit': unit_id, 'zones': changed_zones})

    def _zero_unit(self, unit, prev_state):
        for zone in unit['zones']:
            sig = self._sig(unit['id'], zone, 'detected')
            if sig:
                self._io.write(sig, 0)
            for suffix in self._classes.values():
                sig = self._sig(unit['id'], zone, suffix)
                if sig:
                    self._io.write(sig, 0)
            prev_state.pop(zone, None)
        self._update_globals()

    def _read(self, unit_id, zone, sig_type):
        sig = self._sig(unit_id, zone, sig_type)
        return self._io.read(sig) if sig else 0

    def _update_globals(self):
        sig = self._global_sig('any_detected')
        if sig:
            val = 1 if any(
                self._read(u['id'], z, 'detected')
                for u in self._cfg['units'] for z in u['zones']
            ) else 0
            self._io.write(sig, val)

        for cls_suffix in self._classes.values():
            global_name = f'any_{cls_suffix.replace("has_", "")}'
            sig = self._global_sig(global_name)
            if sig:
                val = 1 if any(
                    self._read(u['id'], z, cls_suffix)
                    for u in self._cfg['units'] for z in u['zones']
                ) else 0
                self._io.write(sig, val)

    def snapshot(self):
        units_out = []
        for unit in self._cfg['units']:
            zones = []
            for z in unit['zones']:
                zd = {'id': z, 'detected': self._read(unit['id'], z, 'detected')}
                for suffix in self._classes.values():
                    zd[suffix] = self._read(unit['id'], z, suffix)
                zones.append(zd)
            with self._fault_lock:
                faulted = unit['id'] in self._fault_units
            units_out.append({
                'id':     unit['id'],
                'ip':     unit['ip'],
                'port':   unit['port'],
                'faulted': faulted,
                'zones':  zones,
            })
        events = list(self._events)[-20:]
        return {
            'ts':     time.time(),
            'units':  units_out,
            'events': events,
        }


def main():
    from pci.shared.log import setup
    setup('pci.agd')

    cfg_path = os.environ.get('PCI_AGD_CFG', _CFG_DEFAULT)
    if not os.path.exists(cfg_path):
        log.error("AGD config not found: %s", cfg_path)
        sys.exit(1)

    cfg = _load_cfg(cfg_path)
    if not cfg['units']:
        log.error("No AGD units configured in %s", cfg_path)
        sys.exit(1)

    io = AGDIOBus()
    io.connect()

    svc = AGDService(cfg, io)
    svc.start()

    log.info("pci.agd running — %d unit(s)", len(cfg['units']))

    import signal
    signal.signal(signal.SIGTERM, lambda *_: svc.stop())

    try:
        while svc._running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    svc.stop()
    log.info("pci.agd stopped")


if __name__ == '__main__':
    main()
