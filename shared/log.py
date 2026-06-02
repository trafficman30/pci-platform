# Shared logging setup — matches CM5 format exactly.
# Call setup(service_name) once at each service entry point.
#
# Produces:  2026-06-02 15:16:50 [pci.web ] INFO  message
# Same format as /opt/CM5/main.py setup_logging().

import logging
import logging.handlers
import os
import time

_LOG_DIR = os.path.join(os.path.dirname(__file__), '..', 'logs')
_FMT     = '%(asctime)s [%(name)-8s] %(levelname)-5s %(message)s'
_DFMT    = '%Y-%m-%d %H:%M:%S'


def setup(service_name, level='INFO'):
    """
    Configure root logging for a PCI service process.

    Handlers:
      console              — at `level` (default INFO)
      <service_name>.log   — DEBUG, rotating 10 MB × 5
      pci.log              — DEBUG, plain append (multi-process safe)

    Local time is used (not UTC), matching CM5.
    Call once per process before any logging is done.
    """
    log_dir = os.path.abspath(_LOG_DIR)
    os.makedirs(log_dir, exist_ok=True)

    logging.Formatter.converter = time.localtime
    lvl  = getattr(logging, level.upper(), logging.INFO)
    fmt  = logging.Formatter(_FMT, datefmt=_DFMT)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # Console — at configured level
    console = logging.StreamHandler()
    console.setLevel(lvl)
    console.setFormatter(fmt)
    root.addHandler(console)

    # Per-service rotating file — always DEBUG so nothing is dropped
    svc_path = os.path.join(log_dir, f'{service_name}.log')
    try:
        fh = logging.handlers.RotatingFileHandler(
            svc_path, maxBytes=10 * 1024 * 1024, backupCount=5, encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        logging.getLogger(service_name).warning('cannot open log file %s: %s', svc_path, e)

    # Unified platform log — plain append, multi-process safe across all services
    pci_path = os.path.join(log_dir, 'pci.log')
    try:
        pfh = logging.FileHandler(pci_path, mode='a', encoding='utf-8')
        pfh.setLevel(logging.DEBUG)
        pfh.setFormatter(fmt)
        root.addHandler(pfh)
    except OSError as e:
        logging.getLogger(service_name).warning('cannot open platform log %s: %s', pci_path, e)

    # Suppress noisy library loggers — same suppressions as CM5
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    logging.getLogger('urllib3').setLevel(logging.WARNING)
