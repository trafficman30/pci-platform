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
    signals, sim_cfg = _load(config_path)
    if not signals:
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

    if pulse:
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

    return signals, sim_cfg
