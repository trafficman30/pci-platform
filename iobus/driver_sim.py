# Simulation driver — replaces hardware, runs inside pci.iobus.
# Reads signals.cfg for every signal owned by pci.iobus, registers and drives them.
#
# Default behaviour: random on/off pulses simulating vehicle detectors.
# Override per signal in signals.cfg [sim] section:
#   signal_name = steady    — holds at 1 (use for crb, health bits, etc.)
#   signal_name = pulse     — explicit pulse (same as default, for clarity)
#
# Interface: start(table, config_path) — called by server.py main().

import configparser
import json
import logging
import os
import random
import threading
import time

log = logging.getLogger('pci.iobus.sim')

OWNER = 'pci.iobus'

_PULSE_ON_MIN  = 0.10   # 100ms — shortest realistic loop detector pulse
_PULSE_ON_MAX  = 0.25   # 250ms
_PULSE_OFF_MIN = 1.0    # 1s gap between vehicles
_PULSE_OFF_MAX = 8.0    # 8s


def start(table, config_path):
    signals, sim_cfg, replay_cfg = _load(config_path)
    if not signals and not replay_cfg:
        log.warning("no signals owned by '%s' found in %s", OWNER, config_path)
        return

    for name in signals:
        table.register(name, OWNER)
    log.info("registered %d signals", len(signals))

    steady = [n for n in signals if sim_cfg.get(n) == 'steady']
    pulse  = [n for n in signals if sim_cfg.get(n) != 'steady']

    for name in steady:
        table.write(name, 1, OWNER)

    log.info("steady=%d  pulse=%d", len(steady), len(pulse))

    if replay_cfg:
        threading.Thread(
            target=_replay_loop, args=(table, replay_cfg),
            daemon=True, name='driver-replay',
        ).start()
    elif pulse:
        threading.Thread(
            target=_pulse_loop, args=(table, pulse),
            daemon=True, name='driver-sim',
        ).start()


def _pulse_loop(table, signals):
    now = time.time()
    state = {
        name: {'value': 0, 'next': now + random.uniform(0.5, 3.0)}
        for name in signals
    }
    while True:
        now = time.time()
        for name, s in state.items():
            if now >= s['next']:
                new_val = 1 - s['value']
                table.write(name, new_val, OWNER)
                s['value'] = new_val
                if new_val == 1:
                    s['next'] = now + random.uniform(_PULSE_ON_MIN, _PULSE_ON_MAX)
                else:
                    s['next'] = now + random.uniform(_PULSE_OFF_MIN, _PULSE_OFF_MAX)
        time.sleep(0.02)   # 20ms poll is fine for 100ms minimum pulse


def _replay_loop(table, replay_cfg):
    path  = replay_cfg.get('file', '')
    speed = float(replay_cfg.get('speed', '1.0'))
    loop  = replay_cfg.get('loop', 'true').strip().lower() != 'false'

    events = []
    try:
        with open(path) as f:
            for lineno, raw in enumerate(f, 1):
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    ev = json.loads(raw)
                    events.append((float(ev['ts']), str(ev['n']), int(ev['v'])))
                except (KeyError, ValueError) as e:
                    log.warning("replay: line %d skipped (%s): %s", lineno, e, raw[:80])
    except OSError as e:
        log.error("replay: cannot open %s: %s — replay not started", path, e)
        return

    if not events:
        log.warning("replay: no valid events in %s — replay not started", path)
        return

    events.sort(key=lambda e: e[0])

    # Register any signal names from recording not already owned by OWNER
    for _, name, _ in events:
        try:
            table.register(name, OWNER)
        except ValueError:
            pass   # already owned by another service — writes will be rejected

    log.info("replay: %d events from %s  speed=%.1fx  loop=%s",
             len(events), path, speed, loop)

    while True:
        t_wall_start = time.time()
        ts_start     = events[0][0]
        for ts, name, val in events:
            target = t_wall_start + (ts - ts_start) / speed
            wait   = target - time.time()
            if wait > 0:
                time.sleep(wait)
            table.write(name, val, OWNER)
        if not loop:
            log.info("replay: complete, thread exiting")
            break
        log.debug("replay: loop complete — restarting")


def _load(config_path):
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read(config_path)

    signals = []
    if cfg.has_section('signals'):
        for name, owner in cfg.items('signals'):
            if owner.strip() == OWNER:
                signals.append(name)

    sim_cfg = {}
    if cfg.has_section('sim'):
        for name, mode in cfg.items('sim'):
            sim_cfg[name] = mode.strip()

    replay_cfg = {}
    if cfg.has_section('replay'):
        for key, val in cfg.items('replay'):
            replay_cfg[key] = val.strip()

    return signals, sim_cfg, replay_cfg
