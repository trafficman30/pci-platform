# driver_gpio.py — CM5 GPIO driver, runs inside pci.iobus.
# Maps gp.i.* signals from /sys/class/gpio inputs → signal table.
# Maps gp.o.* signals from signal table → /sys/class/gpio outputs.
#
# Fails silently on dev hardware where GPIO export is not available —
# pins that cannot be exported are skipped; if none export successfully
# the driver starts with empty pin sets and does nothing.
#
# GPIO is for slow signals only: photocells, panel switches, status inputs,
# enable/inhibit lines. 20ms polling is adequate for these.
# Do NOT route vehicle detection through GPIO — detection pulses are
# typically 100–250ms and require latching (RPDB/XKOP handle detection).
#
# Config: [gpio] section in signals.cfg
#   signal_name = gpio_bcm_number
#   Direction is inferred from signal name: gp.i.* = in, gp.o.* = out
#
# Entry point: start(table, config_path) — called by server.py main().

import configparser
import logging
import os
import threading
import time

log = logging.getLogger('pci.iobus.gpio')

SYSFS_GPIO    = '/sys/class/gpio'
POLL_INTERVAL = 0.02    # 20ms — suitable for slow signals only (see file header)
DRIVER_SOURCE = 'pci.iobus'


# ── Sysfs helpers ─────────────────────────────────────────────────────────────

def _export(gpio_num):
    """Export a GPIO pin. Returns True on success, False if unavailable."""
    value_path = os.path.join(SYSFS_GPIO, f'gpio{gpio_num}', 'value')
    if os.path.exists(value_path):
        return True   # already exported
    try:
        with open(os.path.join(SYSFS_GPIO, 'export'), 'w') as f:
            f.write(str(gpio_num))
        return True
    except OSError as e:
        log.warning('GPIO%d: export failed (%s) — pin skipped', gpio_num, e)
        return False


def _set_direction(gpio_num, direction):
    """Set pin direction ('in' or 'out'). Returns True on success."""
    path = os.path.join(SYSFS_GPIO, f'gpio{gpio_num}', 'direction')
    try:
        with open(path, 'w') as f:
            f.write(direction)
        return True
    except OSError as e:
        log.warning('GPIO%d: set direction %s failed (%s) — pin skipped',
                    gpio_num, direction, e)
        return False


def _read_value(gpio_num):
    """Read pin value. Returns int (0 or 1) or None on error."""
    path = os.path.join(SYSFS_GPIO, f'gpio{gpio_num}', 'value')
    try:
        with open(path, 'r') as f:
            return int(f.read().strip())
    except OSError:
        return None


def _write_value(gpio_num, value):
    """Write pin value. Returns True on success."""
    path = os.path.join(SYSFS_GPIO, f'gpio{gpio_num}', 'value')
    try:
        with open(path, 'w') as f:
            f.write('1' if value else '0')
        return True
    except OSError as e:
        log.warning('GPIO%d: write failed (%s)', gpio_num, e)
        return False


# ── Driver ────────────────────────────────────────────────────────────────────

class GPIODriver:
    """
    GPIO driver — runs inside pci.iobus.

    Parameters
    ----------
    table   : SignalTable
    inputs  : dict — {signal_name: gpio_num}  (gp.i.*)
    outputs : dict — {signal_name: gpio_num}  (gp.o.*)
    """

    def __init__(self, table, inputs, outputs):
        self._table   = table
        self._inputs  = inputs    # {signal_name: gpio_num}  active inputs only
        self._outputs = outputs   # {signal_name: gpio_num}  active outputs only

    def start(self):
        if self._inputs:
            threading.Thread(
                target=self._poll_loop, daemon=True, name='gpio-poll'
            ).start()
            log.info('GPIO polling %d input(s) at %dms', len(self._inputs),
                     int(POLL_INTERVAL * 1000))

        if self._outputs:
            self._table.subscribe(self._on_table_change)
            # Push current table values to outputs immediately
            for name, gpio_num in self._outputs.items():
                val = self._table.read(name)
                _write_value(gpio_num, val)
            log.info('GPIO watching %d output(s)', len(self._outputs))

        if not self._inputs and not self._outputs:
            log.info('GPIO driver started with no active pins')

    # ── Input polling ─────────────────────────────────────────────────────────

    def _poll_loop(self):
        state = {name: None for name in self._inputs}
        dead  = set()

        while True:
            for name, gpio_num in self._inputs.items():
                if name in dead:
                    continue
                val = _read_value(gpio_num)
                if val is None:
                    log.warning('GPIO%d (%s): read failed — removing from poll',
                                gpio_num, name)
                    dead.add(name)
                    continue
                if val != state[name]:
                    state[name] = val
                    self._table.write(name, val, DRIVER_SOURCE)
                    log.debug('GPIO%d (%s) = %d', gpio_num, name, val)

            time.sleep(POLL_INTERVAL)

    # ── Output writes ─────────────────────────────────────────────────────────

    def _on_table_change(self, name, value, source):
        if name not in self._outputs:
            return
        gpio_num = self._outputs[name]
        ok = _write_value(gpio_num, value)
        if ok:
            log.debug('GPIO%d (%s) ← %d', gpio_num, name, value)


# ── Entry point ───────────────────────────────────────────────────────────────

def start(table, config_path):
    """Called by server.py main() when driver = gpio in platform.cfg."""
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read(config_path)

    if not cfg.has_section('gpio'):
        log.warning('no [gpio] section in %s — driver not started', config_path)
        return

    inputs  = {}
    outputs = {}

    for sig_name, gpio_str in cfg.items('gpio'):
        try:
            gpio_num = int(gpio_str.split('#')[0].strip())
        except ValueError:
            log.warning('GPIO config: bad gpio number for %s = %r — skipped',
                        sig_name, gpio_str)
            continue

        if sig_name.startswith('gp.i.'):
            direction = 'in'
            target    = inputs
        elif sig_name.startswith('gp.o.'):
            direction = 'out'
            target    = outputs
        else:
            log.warning('GPIO config: unrecognised signal name %r — skipped', sig_name)
            continue

        if not _export(gpio_num):
            continue
        if not _set_direction(gpio_num, direction):
            continue

        target[sig_name] = gpio_num
        log.info('GPIO%d → %s (%s)', gpio_num, sig_name, direction)

    log.info('GPIO ready: %d input(s), %d output(s)',
             len(inputs), len(outputs))

    driver = GPIODriver(table, inputs, outputs)
    driver.start()
