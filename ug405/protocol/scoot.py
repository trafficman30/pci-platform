# ug405/protocol/scoot.py
# SCOOT occupancy sampler for pci.ug405.
# Ported from /opt/CM5/ug405/svc_ug405_scoot.py — logic is identical.
#
# Samples VSn signals at 100ms intervals via UG405IOBus.read().
# Accumulates occupancy over ScootSampleReportInterval seconds.
# Packs results into nibble bitmask for SNMP utcType2ScootVSn reply.
#
# Nibble encoding (per detector per sample interval):
#   0x0 = never occupied
#   0xF = always occupied
#   value = int(occupied_ticks / total_ticks * 15)
#
# Byte packing:
#   Byte 0: D2[high nibble] D1[low nibble]
#   Byte 1: D4[high nibble] D3[low nibble]

import logging
import threading
import time

log = logging.getLogger('pci.ug405.scoot')

SAMPLE_INTERVAL_MS = 100


class ScootSampler:
    """
    SCOOT occupancy sampler.

    io      : UG405IOBus
    mapping : ug405 config dict (from load_ug405)
    live    : live config dict (ScootSampleReportInterval)
    on_sample : optional callback(scn, packed_bytes) at each interval
    """

    def __init__(self, io, mapping, live, on_sample=None):
        self.io        = io
        self.mapping   = mapping
        self.live      = live
        self.on_sample = on_sample

        self._vsn_signals = {}
        self._total_count = 0

        for scn in mapping['scns']:
            reply = mapping['reply'][scn]
            if 'VSn' not in reply:
                continue
            bits    = reply['VSn']
            ordered = [sig for _, sig in sorted(bits.items())]
            self._vsn_signals[scn] = ordered
            self._total_count += len(ordered)
            log.info("SCN %s: %d VSn detectors", scn, len(ordered))

        self._sample_counts = {
            scn: [0] * len(sigs)
            for scn, sigs in self._vsn_signals.items()
        }
        self._tick_count = 0

        self._packed = {
            scn: bytes((len(sigs) + 1) // 2)
            for scn, sigs in self._vsn_signals.items()
        }
        self._lock = threading.Lock()

    @property
    def detector_count(self):
        return self._total_count

    def get_packed(self, scn):
        with self._lock:
            return self._packed.get(scn, b'\x00')

    def start(self):
        if not self._vsn_signals:
            log.info("no VSn signals configured — SCOOT sampler idle")
            return
        threading.Thread(target=self._sample_loop, daemon=True,
                         name='ug405-scoot').start()
        log.info("SCOOT sampler started — %d detectors total", self._total_count)

    def _sample_loop(self):
        while True:
            time.sleep(SAMPLE_INTERVAL_MS / 1000.0)

            with self._lock:
                for scn, signals in self._vsn_signals.items():
                    for i, sig in enumerate(signals):
                        if self.io.read(sig):
                            self._sample_counts[scn][i] += 1
                self._tick_count += 1

            report_ticks = int(
                self.live.get('ScootSampleReportInterval', 4) * 1000
                / SAMPLE_INTERVAL_MS
            )

            if self._tick_count >= report_ticks:
                self._pack_and_reset()

    def _pack_and_reset(self):
        with self._lock:
            total_ticks = self._tick_count
            for scn, counts in self._sample_counts.items():
                packed = self._pack_nibbles(counts, total_ticks)
                self._packed[scn] = packed
                log.debug("SCOOT %s packed=%s", scn, packed.hex())
                if self.on_sample:
                    self.on_sample(scn, packed)
            for scn in self._sample_counts:
                self._sample_counts[scn] = [0] * len(self._sample_counts[scn])
            self._tick_count = 0

    def _pack_nibbles(self, counts, total_ticks):
        n_bytes = (len(counts) + 1) // 2
        if total_ticks == 0 or not counts:
            return bytes(n_bytes)

        nibbles = [min(15, int(c / total_ticks * 15)) for c in counts]
        while len(nibbles) % 2:
            nibbles.append(0)

        result = bytearray()
        for i in range(0, len(nibbles), 2):
            result.append((nibbles[i + 1] << 4) | nibbles[i])
        while len(result) < n_bytes:
            result.append(0)
        return bytes(result[:n_bytes])
