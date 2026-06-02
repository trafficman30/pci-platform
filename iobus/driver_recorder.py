# IOBus signal recorder — captures signal writes to a JSONL file.
# Activated by: driver = recorder  in platform.cfg [iobus].
# Config in signals.cfg [recorder] section:
#
#   file    = /opt/pci/recordings/2026-06-02.jsonl
#   signals = det.0, det.1   (optional; omit = record all signals)
#
# Output format (one event per line):
#   {"ts": 1748880010.123, "n": "det.0", "v": 1}
#
# Only records state changes — the signal table does not fire subscribers
# for writes that do not change the current value.
#
# Note: records conditioned values as seen by services.  For most detector
# signals (0/1, inversion only) recording+replay is bit-perfect because
# double inversion is identity.  Avoid recording scaled signals.
#
# Files produced here are replayed by driver_sim.py [replay] mode.
# Interface: start(table, config_path) — called by server.py main().

import configparser
import json
import logging
import os
import threading
import time

log = logging.getLogger('pci.iobus.recorder')


def start(table, config_path):
    cfg = configparser.ConfigParser()
    cfg.optionxform = str
    cfg.read(config_path)

    if not cfg.has_section('recorder'):
        log.warning('no [recorder] section in %s — recorder not started', config_path)
        return

    file_path = cfg.get('recorder', 'file', fallback='').strip()
    if not file_path:
        log.warning('[recorder] file= not set — recorder not started')
        return

    raw_filter = cfg.get('recorder', 'signals', fallback='').strip()
    sig_filter = (
        {s.strip() for s in raw_filter.split(',') if s.strip()}
        if raw_filter else set()
    )

    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    # buffering=1 = line-buffered: each newline flushes to the OS page cache.
    # Avoids event loss on crash without the overhead of an explicit flush() call.
    out  = open(file_path, 'a', buffering=1, encoding='utf-8')  # noqa: SIM115
    lock = threading.Lock()

    log.info('recorder started → %s  filter=%s',
             file_path,
             ', '.join(sorted(sig_filter)) if sig_filter else 'all signals')

    def _on_write(name, value, _source):
        if sig_filter and name not in sig_filter:
            return
        line = json.dumps({'ts': time.time(), 'n': name, 'v': value}) + '\n'
        with lock:
            out.write(line)

    table.subscribe(_on_write)
