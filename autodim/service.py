# autodim/service.py
# pci.autodim — automatic signal dimming controller.
#
# Uses astral to calculate daily sunrise/sunset from lat/lon.
# Applies configurable minute offsets to derive dim_utc and bright_utc.
# Writes 1 (dim) or 0 (bright) to one IOBus output signal on a 30-second loop.
# Pushes state to pci.web via IPC push socket.
#
# Algorithm:
#   is_dim = (now >= dim_utc) OR (now < bright_utc)
#
# Run:
#   PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.autodim.service

import configparser
import json
import logging
import os
import signal
import threading
import time
from datetime import date, datetime, timedelta, timezone

from astral import LocationInfo
from astral.sun import sun

from pci.autodim.iobus_client import AutodimIOBus
from pci.autodim.ipc.server   import IPCServer

log = logging.getLogger('pci.autodim')

_DEFAULT_CFG   = os.path.join(os.path.dirname(__file__), '..', 'config', 'autodim.cfg')
_STATE_FILE    = '/tmp/pci.autodim.state.json'
_TICK_INTERVAL = 30.0


class AutodimService:
    def __init__(self, iobus, cfg, cfg_path=None):
        self._iobus         = iobus
        self._cfg_path      = cfg_path
        self._lat           = cfg.getfloat('autodim', 'lat')
        self._lon           = cfg.getfloat('autodim', 'lon')
        self._dim_offset    = cfg.getint('autodim', 'dim_offset_minutes',    fallback=0)
        self._bright_offset = cfg.getint('autodim', 'bright_offset_minutes', fallback=0)
        self._signal        = cfg.get('autodim', 'signal')
        self._enabled       = cfg.getboolean('autodim', 'enabled', fallback=True)

        self._is_dim        = None      # current output state (None = not yet written)
        self._dim_utc       = None      # today's computed dim time
        self._bright_utc    = None      # today's computed bright time
        self._calc_date     = None      # date for which dim/bright were last calculated
        self._last_write_ts = None
        self._lock          = threading.Lock()

        self._load_state()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_enabled(self, enabled):
        with self._lock:
            self._enabled = bool(enabled)
        log.info("enabled = %s", enabled)

    def reload_config(self):
        if not self._cfg_path:
            raise RuntimeError("cfg_path not set")
        cfg = configparser.ConfigParser()
        cfg.read(self._cfg_path)
        with self._lock:
            self._lat           = cfg.getfloat('autodim', 'lat')
            self._lon           = cfg.getfloat('autodim', 'lon')
            self._dim_offset    = cfg.getint('autodim', 'dim_offset_minutes',    fallback=0)
            self._bright_offset = cfg.getint('autodim', 'bright_offset_minutes', fallback=0)
            self._signal        = cfg.get('autodim', 'signal')
            self._enabled       = cfg.getboolean('autodim', 'enabled', fallback=True)
            self._calc_date     = None  # force recalc on next tick
        log.info("config reloaded: lat=%.4f lon=%.4f dim_offset=%d bright_offset=%d",
                 self._lat, self._lon, self._dim_offset, self._bright_offset)

    def snapshot(self):
        with self._lock:
            return {
                'enabled':       self._enabled,
                'is_dim':        self._is_dim,
                'signal':        self._signal,
                'lat':           self._lat,
                'lon':           self._lon,
                'dim_offset':    self._dim_offset,
                'bright_offset': self._bright_offset,
                'dim_utc':       self._dim_utc.isoformat()    if self._dim_utc    else None,
                'bright_utc':    self._bright_utc.isoformat() if self._bright_utc else None,
                'last_write_ts': self._last_write_ts,
            }

    def tick(self):
        """
        Called every 30 seconds from main loop.
        Recalculates astral times when the date changes.
        Writes current dim value to IOBus.
        Returns transition event dict if state changed, else None.
        """
        now   = datetime.now(tz=timezone.utc)
        today = now.date()
        ev    = None

        with self._lock:
            if self._calc_date != today:
                self._recalc(today)

            if not self._enabled:
                return None

            is_dim = (now >= self._dim_utc) or (now < self._bright_utc)
            signal = self._signal

            if is_dim != self._is_dim:
                prev          = self._is_dim
                self._is_dim  = is_dim
                self._save_state()
                log.info("transition: %s → %s",
                         ('dim' if prev else 'bright') if prev is not None else 'startup',
                         'dim' if is_dim else 'bright')
                ev = {
                    'v':      1,
                    't':      'transition',
                    'ts':     now.timestamp(),
                    'is_dim': is_dim,
                    'signal': signal,
                    'value':  1 if is_dim else 0,
                }

        self._iobus.write(signal, 1 if is_dim else 0)
        with self._lock:
            self._last_write_ts = now.timestamp()

        return ev

    # ── Internal ──────────────────────────────────────────────────────────────

    def _recalc(self, today):
        loc = LocationInfo(
            name='site', region='', timezone='UTC',
            latitude=self._lat, longitude=self._lon,
        )
        s = sun(loc.observer, date=today, tzinfo=timezone.utc)
        self._dim_utc    = s['sunset']  + timedelta(minutes=self._dim_offset)
        self._bright_utc = s['sunrise'] + timedelta(minutes=self._bright_offset)
        self._calc_date  = today
        log.info("astral %s: sunset=%s (+%dm) → dim @ %s  sunrise=%s (+%dm) → bright @ %s",
                 today,
                 s['sunset'].strftime('%H:%M'),  self._dim_offset,
                 self._dim_utc.strftime('%H:%M'),
                 s['sunrise'].strftime('%H:%M'), self._bright_offset,
                 self._bright_utc.strftime('%H:%M'))

    def _save_state(self):
        state = {'is_dim': self._is_dim, 'signal': self._signal}
        try:
            with open(_STATE_FILE, 'w') as f:
                json.dump(state, f)
        except Exception as e:
            log.warning("state save failed: %s", e)

    def _load_state(self):
        try:
            with open(_STATE_FILE) as f:
                state = json.load(f)
            self._is_dim = state.get('is_dim')
            log.info("restored state: is_dim=%s", self._is_dim)
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("state load failed: %s", e)


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    from pci.shared.log import setup
    setup('pci.autodim')
    log.info("starting  pid=%d", os.getpid())

    cfg_path = os.getenv('PCI_AUTODIM_CFG', _DEFAULT_CFG)
    cfg = configparser.ConfigParser()
    cfg.read(cfg_path)

    iobus = AutodimIOBus()
    svc   = AutodimService(iobus, cfg, cfg_path=cfg_path)

    ipc = IPCServer()
    ipc.set_snapshot_callback(svc.snapshot)
    ipc.set_enabled_callback(svc.set_enabled)
    ipc.set_reload_callback(svc.reload_config)
    ipc.start()

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT,  lambda *_: stop.set())

    log.info("ready — signal=%s lat=%.4f lon=%.4f dim_offset=%d bright_offset=%d",
             cfg.get('autodim', 'signal'),
             cfg.getfloat('autodim', 'lat'),
             cfg.getfloat('autodim', 'lon'),
             cfg.getint('autodim', 'dim_offset_minutes',    fallback=0),
             cfg.getint('autodim', 'bright_offset_minutes', fallback=0))

    while not stop.is_set():
        try:
            ev = svc.tick()
            if ev:
                ipc.push_event(ev)
        except Exception as e:
            log.error("tick error: %s", e)
        stop.wait(_TICK_INTERVAL)

    log.info("stopping")


if __name__ == '__main__':
    main()
