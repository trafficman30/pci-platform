# MOVA kernel IO layer — reads IOBus inputs into kernel buffers, writes all outputs.
#
# One BATCH call per tick reads all detectors + confirms + CRB together.
# 150ms rising-edge latch applied to detector values only, before buffers.set_detector().
# Only changed output values are written (W) to minimise socket traffic.
#
# Signal names come from streams.json — must match signals.cfg exactly.
# KernelBuffers is duck-typed; no import at module level so this file is
# testable without the full MOVA stack present.

# dout[] indices — matches pci_mova.core.model.buffers defines.h layout
_DOUT_TO         = 16
_DOUT_HI         = 17
_DOUT_SYNC       = 18
_DOUT_DET_FAULT  = 19
_DOUT_MOVA_FAULT = 20

import json
import logging
import time

log = logging.getLogger('pci.mova.kernel_io')

_LATCH_S = 0.150   # 150ms — MOVA algorithm requirement, not our choice


class _DetLatch:
    """
    Per-signal rising-edge latch.
    On 0→1 transition, holds the reported value at 1 for _LATCH_S seconds
    regardless of what the raw value does during that window.
    """

    def __init__(self):
        self._prev  = {}   # sig → last raw value
        self._until = {}   # sig → monotonic deadline

    def apply(self, sig, raw):
        now  = time.monotonic()
        prev = self._prev.get(sig, 0)
        if raw and not prev:                         # rising edge
            self._until[sig] = now + _LATCH_S
        self._prev[sig] = raw
        return 1 if (raw or now < self._until.get(sig, 0)) else 0


class KernelIO:
    """
    IOBus ↔ MOVA kernel bridge for one stream.

    Parameters
    ----------
    client     : IOBusClient — connected (or will reconnect) to pci.iobus
    stream_map : dict with keys detectors, confirms, crb, forces
                 signal name lists/strings matching signals.cfg
    stream_id  : int — for logging only
    """

    def __init__(self, client, stream_map, stream_id=0):
        self._client     = client
        self._dets       = stream_map.get('detectors',  [])
        self._confs      = stream_map.get('confirms',   [])
        self._crb        = stream_map.get('crb',        '')
        self._forces     = stream_map.get('forces',     [])
        self._to         = stream_map.get('to',         '')
        self._hi         = stream_map.get('hi',         '')
        self._sync       = stream_map.get('sync',       '')
        self._det_fault  = stream_map.get('det_fault',  '')
        self._mova_fault = stream_map.get('mova_fault', '')
        self._specials   = stream_map.get('specials',   [])
        self._stream_id  = stream_id
        self._latch      = _DetLatch()
        self._prev_out   = {}   # sig → last written value (all outputs)

        log.info(
            "stream %d: %d detectors, %d confirms, crb=%s, %d forces, %d specials",
            stream_id, len(self._dets), len(self._confs),
            self._crb or 'none', len(self._forces), len(self._specials),
        )

    def read_inputs(self, buffers):
        """
        One BATCH call → populate buffers.
        Latch is applied to detector values only.
        buffers.crb defaults True if no crb signal is configured.
        """
        all_sigs = self._dets + self._confs + ([self._crb] if self._crb else [])
        vals = self._client.batch(all_sigs)
        it   = iter(vals)

        for idx, sig in enumerate(self._dets):
            raw = next(it, 0)
            buffers.set_detector(idx, self._latch.apply(sig, raw))

        for idx, sig in enumerate(self._confs):
            buffers.set_confirm(idx, 1 if next(it, 0) else 0)

        if self._crb:
            buffers.crb = bool(next(it, 0))
        else:
            buffers.crb = True

    def write_outputs(self, buffers):
        """
        Write all output signals that have changed since last tick.
        Only changed values are sent to minimise socket traffic.
        """
        def _w(sig, val):
            if sig and val != self._prev_out.get(sig):
                self._client.write(sig, val)
                self._prev_out[sig] = val

        # Stage forces (1-based stage index)
        for idx, sig in enumerate(self._forces):
            _w(sig, buffers.get_force(idx + 1))

        # dout[] outputs
        _w(self._to,         buffers.dout[_DOUT_TO])
        _w(self._hi,         buffers.dout[_DOUT_HI])
        _w(self._sync,       buffers.dout[_DOUT_SYNC])
        _w(self._det_fault,  buffers.dout[_DOUT_DET_FAULT])
        _w(self._mova_fault, buffers.dout[_DOUT_MOVA_FAULT])

        # Special (dataset-configured channel) outputs
        for idx, sig in enumerate(self._specials):
            if idx < len(buffers.special_outputs):
                _w(sig, buffers.special_outputs[idx])


    def snapshot(self):
        """Return IO state for MovaStream.snapshot() — iobus-backed values."""
        return {
            "type":     "iobus",
            "connected": self._client.connected,
        }

    def reset_confirms(self, stagon):
        pass

    def set_intergreen_matrix(self, matrix):
        pass


def load_stream_map(path, stream_id):
    """Load signal map for one stream from streams.json."""
    with open(path) as f:
        data = json.load(f)
    key = str(stream_id)
    if key not in data:
        raise KeyError(f"stream {stream_id} not found in {path}")
    return data[key]
