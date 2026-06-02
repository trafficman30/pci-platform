"""
pci.flir — FLIR camera adapter.

Subscribes to WebSocket event streams from one or more FLIR cameras.
Writes zone detection signals to IOBus on event receipt.
Entry point: python -m pci.flir.service
"""

import json
import time
import logging
import threading
import configparser
import os
import sys
from collections import deque

from pci.flir.iobus_client import FLIRIOBus
from pci.flir.ipc.server import IPCServer

log = logging.getLogger('pci.flir')

_CFG_DEFAULT = '/opt/pci/config/flir.cfg'

ZONE_TYPES = ['occupied', 'dilemma', 'has_pedestrian', 'has_bicycle', 'has_vehicle']


def _load_cfg(cfg_path):
    cp = configparser.ConfigParser(inline_comment_prefixes=('#',))
    cp.read(cfg_path)

    cameras = []
    for section in sorted(cp.sections()):
        if not section.upper().startswith('CAMERA.'):
            continue
        ip    = cp.get(section, 'ip',   fallback='127.0.0.1')
        port  = cp.getint(section, 'port', fallback=8765)
        zones = [int(x.strip()) for x in
                 cp.get(section, 'zones', fallback='1').split(',') if x.strip()]
        cameras.append({'id': section, 'ip': ip, 'port': port, 'zones': zones})

    mapping = dict(cp.items('VIRT_MAPPING')) if cp.has_section('VIRT_MAPPING') else {}

    return {'cameras': cameras, 'mapping': mapping}


class FLIRService:
    def __init__(self, cfg, io):
        self._cfg     = cfg
        self._io      = io
        self._running = False
        self._mapping = cfg['mapping']
        self._events  = deque(maxlen=50)
        self._ipc     = IPCServer(self)

    def _sig(self, camera_id, zone, signal_type):
        cam_key    = f'{camera_id.lower()}.zone.{zone}.{signal_type}'
        global_key = f'zone.{zone}.{signal_type}'
        return self._mapping.get(cam_key) or self._mapping.get(global_key)

    def start(self):
        self._running = True
        self._ipc.start()
        for cam in self._cfg['cameras']:
            t = threading.Thread(
                target=self._camera_loop, args=(cam,),
                daemon=True, name=f"flir-{cam['id']}")
            t.start()
            log.info("FLIR camera %s -> ws://%s:%d  zones=%s",
                     cam['id'], cam['ip'], cam['port'], cam['zones'])

    def stop(self):
        self._running = False

    def _camera_loop(self, cam):
        import websocket
        ws_url    = f"ws://{cam['ip']}:{cam['port']}"
        cam_zones = set(cam['zones'])
        svc       = self

        def on_open(ws):
            ws.send(json.dumps({"messageType": "Subscription",
                                "subscription": {"type": "Event", "action": "Subscribe"}}))
            log.info("FLIR %s connected", cam['id'])

        def on_message(ws, message):
            try:
                data = json.loads(message)
                if data.get("messageType") == "Event":
                    zone = int(data.get("zoneId") or data.get("zone") or 0)
                    if zone in cam_zones:
                        svc._publish_event(zone, data.get("type"),
                                           data.get("state"), data.get("class"),
                                           cam['id'])
            except Exception as e:
                log.debug("FLIR %s parse error: %s", cam['id'], e)

        def on_error(ws, error):
            if "Connection refused" not in str(error):
                log.warning("FLIR %s error: %s", cam['id'], error)

        def on_close(ws, *args):
            log.warning("FLIR %s closed — reconnecting in 5s", cam['id'])

        while self._running:
            try:
                ws = websocket.WebSocketApp(ws_url,
                    on_open=on_open, on_message=on_message,
                    on_error=on_error, on_close=on_close)
                ws.run_forever(ping_interval=30, ping_timeout=10)
            except Exception as e:
                log.error("FLIR %s connection failed: %s", cam['id'], e)
            time.sleep(5)

    def _publish_event(self, zone, event_type, state, cls=None, camera=''):
        value = 1 if state == "Begin" else 0

        self._events.append({
            'ts':     time.strftime('%H:%M:%S'),
            'zone':   zone,
            'type':   event_type,
            'state':  state,
            'cls':    cls or '',
            'camera': camera,
        })

        if event_type in ("Presence", "Pedestrian"):
            sig = self._sig(camera, zone, 'occupied')
            if sig:
                self._io.write(sig, value)

        if event_type == "DilemmaZone":
            sig = self._sig(camera, zone, 'dilemma')
            if sig:
                self._io.write(sig, value)
                log.info("FLIR DilemmaZone zone %d [%s] -> %s = %d",
                         zone, camera, sig, value)

        if cls:
            cls_upper = cls.upper()
            if cls_upper == "PEDESTRIAN":
                sig = self._sig(camera, zone, 'has_pedestrian')
                if sig:
                    self._io.write(sig, value)
            elif cls_upper == "BICYCLE":
                sig = self._sig(camera, zone, 'has_bicycle')
                if sig:
                    self._io.write(sig, value)
            elif cls_upper in ("CAR", "TRUCK", "BUS", "VAN"):
                sig = self._sig(camera, zone, 'has_vehicle')
                if sig:
                    self._io.write(sig, value)

        self._update_globals()
        self._ipc.push_event({'t': 'zone_event', 'ts': time.time(),
                               'camera': camera, 'zone': zone,
                               'event_type': event_type, 'state': state,
                               'cls': cls or ''})

    def _update_globals(self):
        checks = {
            'any_occupied':   'occupied',
            'any_dilemma':    'dilemma',
            'any_pedestrian': 'has_pedestrian',
            'any_bicycle':    'has_bicycle',
            'any_vehicle':    'has_vehicle',
        }
        for global_key, zone_type in checks.items():
            sig = self._mapping.get(global_key)
            if not sig:
                continue
            val = 1 if any(
                self._io.read(s)
                for cam in self._cfg['cameras']
                for z in cam['zones']
                for s in [self._sig(cam['id'], z, zone_type)]
                if s
            ) else 0
            self._io.write(sig, val)

    def _read(self, cam_id, zone, sig_type):
        sig = self._sig(cam_id, zone, sig_type)
        return self._io.read(sig) if sig else 0

    def snapshot(self):
        cameras_out = []
        for cam in self._cfg['cameras']:
            zones = []
            for z in cam['zones']:
                zones.append({
                    'id':             z,
                    'occupied':       self._read(cam['id'], z, 'occupied'),
                    'dilemma':        self._read(cam['id'], z, 'dilemma'),
                    'has_pedestrian': self._read(cam['id'], z, 'has_pedestrian'),
                    'has_bicycle':    self._read(cam['id'], z, 'has_bicycle'),
                    'has_vehicle':    self._read(cam['id'], z, 'has_vehicle'),
                })
            cameras_out.append({
                'id':    cam['id'],
                'ip':    cam['ip'],
                'port':  cam['port'],
                'zones': zones,
            })
        events = list(self._events)[-20:]
        return {
            'ts':      time.time(),
            'cameras': cameras_out,
            'events':  events,
        }


def main():
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )

    cfg_path = os.environ.get('PCI_FLIR_CFG', _CFG_DEFAULT)
    if not os.path.exists(cfg_path):
        log.error("FLIR config not found: %s", cfg_path)
        sys.exit(1)

    cfg = _load_cfg(cfg_path)
    if not cfg['cameras']:
        log.error("No cameras configured in %s", cfg_path)
        sys.exit(1)

    io = FLIRIOBus()
    io.connect()

    svc = FLIRService(cfg, io)
    svc.start()

    log.info("pci.flir running — %d camera(s)", len(cfg['cameras']))

    import signal
    signal.signal(signal.SIGTERM, lambda *_: svc.stop())

    try:
        while svc._running:
            time.sleep(1)
    except KeyboardInterrupt:
        pass

    svc.stop()
    log.info("pci.flir stopped")


if __name__ == '__main__':
    main()
