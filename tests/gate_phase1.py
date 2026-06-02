"""
Phase 1.6 gate test — must pass before Phase 2.

Tests:
  1. IOBus BATCH round-trip latency (target: p99 < 0.5ms)
  2. MOVA tick interval consistency (target: p99 < 110ms at 100ms rate)
  3. IOBus time per tick under load (target: p99 < 0.5ms)
  4. 150ms detector latch timing (target: hold 140-165ms)

Run with IOBus server already started:
  PYTHONPATH=/opt /opt/pci/venv/bin/python /opt/pci/tests/gate_phase1.py
"""

import statistics
import sys
import time

sys.path.insert(0, '/opt')

from pci.shared.iobus_client import IOBusClient
from pci.mova.kernel_io import KernelIO, load_stream_map

STREAMS_JSON = '/opt/pci/config/streams.json'
TICK_SEC     = 0.100
TICK_COUNT   = 150   # 15 seconds — enough to catch scheduler jitter

_G = '\033[92m'
_R = '\033[91m'
_W = '\033[93m'
_E = '\033[0m'

def _ok(cond):  return f'{_G}PASS{_E}' if cond else f'{_R}FAIL{_E}'
def hr():       print('─' * 62)


# ── Stub buffers (duck-typed — no MOVA stack required) ────────────────────────

class _Buffers:
    def __init__(self):
        self.dets   = [0] * 16
        self.confs  = [0] * 16
        self.crb    = False
        self._f     = {}
    def set_detector(self, i, v): self.dets[i]  = v
    def set_confirm(self,  i, v): self.confs[i] = v
    def get_force(self, stage):   return self._f.get(stage, 0)


# ── Mock IOBus client (controlled signal injection for latch test) ─────────────

class _MockClient:
    def __init__(self, all_signals):
        self._v = {s: 0 for s in all_signals}
    def set(self, sig, val): self._v[sig] = val
    def batch(self, sigs):   return [self._v.get(s, 0) for s in sigs]
    def write(self, sig, val): return True
    connected = True


# ── Test 1: BATCH round-trip latency ─────────────────────────────────────────

def test_1_batch_latency(client, signals):
    print('\nTest 1 — IOBus BATCH round-trip latency')
    hr()

    N  = 1000
    ts = []
    for _ in range(N):
        t0 = time.perf_counter()
        client.batch(signals)
        ts.append((time.perf_counter() - t0) * 1000)

    ts.sort()
    mn   = ts[0]
    mean = statistics.mean(ts)
    p50  = ts[N // 2]
    p95  = ts[int(N * 0.95)]
    p99  = ts[int(N * 0.99)]
    mx   = ts[-1]

    print(f'  n={N}  signals={len(signals)}')
    print(f'  min={mn:.3f}ms  mean={mean:.3f}ms  p50={p50:.3f}ms')
    print(f'  p95={p95:.3f}ms  p99={p99:.3f}ms  max={mx:.3f}ms')
    ok = p99 < 0.5
    print(f'  p99 < 0.500ms: {_ok(ok)}  ({p99:.3f}ms)')
    return ok


# ── Tests 2 + 3: Tick timing under IOBus load ─────────────────────────────────

def test_2_3_tick_timing(io, buffers):
    print(f'\nTest 2+3 — Tick timing under IOBus load  ({TICK_COUNT} ticks @ 100ms)')
    hr()

    intervals_ms = []
    iobus_ms     = []
    overruns     = 0

    next_t = time.perf_counter()
    prev_t = None

    for _ in range(TICK_COUNT):
        next_t += TICK_SEC

        t0 = time.perf_counter()
        io.read_inputs(buffers)
        io.write_outputs(buffers)
        iobus_ms.append((time.perf_counter() - t0) * 1000)

        now = time.perf_counter()
        if prev_t is not None:
            intervals_ms.append((now - prev_t) * 1000)
        prev_t = now

        slack = next_t - time.perf_counter()
        if slack > 0:
            time.sleep(slack)
        else:
            overruns += 1

    intervals_ms.sort()
    iobus_ms.sort()
    n = len(intervals_ms)

    i_mean = statistics.mean(intervals_ms)
    i_p95  = intervals_ms[int(n * 0.95)]
    i_p99  = intervals_ms[int(n * 0.99)]
    i_max  = intervals_ms[-1]
    i_std  = statistics.stdev(intervals_ms)

    o_mean = statistics.mean(iobus_ms)
    o_p99  = iobus_ms[int(len(iobus_ms) * 0.99)]
    o_max  = iobus_ms[-1]

    print(f'  Tick intervals ({n} samples, target 100.000ms):')
    print(f'    mean={i_mean:.3f}ms  stdev={i_std:.3f}ms')
    print(f'    p95={i_p95:.3f}ms   p99={i_p99:.3f}ms   max={i_max:.3f}ms')
    print(f'    overruns: {overruns}/{TICK_COUNT}')
    print(f'  IOBus time per tick (read_inputs + write_outputs):')
    print(f'    mean={o_mean:.3f}ms  p99={o_p99:.3f}ms  max={o_max:.3f}ms')

    ok_interval = i_p99  < 110.0
    ok_iobus    = o_p99  < 0.500
    ok_overruns = overruns == 0

    print(f'  Tick p99 < 110ms:          {_ok(ok_interval)}  ({i_p99:.3f}ms)')
    print(f'  IOBus/tick p99 < 0.500ms:  {_ok(ok_iobus)}  ({o_p99:.3f}ms)')
    print(f'  Zero tick overruns:        {_ok(ok_overruns)}  ({overruns})')
    return ok_interval and ok_iobus and ok_overruns


# ── Test 4: 150ms detector latch timing ───────────────────────────────────────

def test_4_latch(stream_map):
    print('\nTest 4 — 150ms detector latch timing')
    hr()

    all_sigs = stream_map['detectors'] + stream_map['confirms'] + [stream_map['crb']]
    results  = []

    for det_idx, det_sig in enumerate(stream_map['detectors']):
        mock = _MockClient(all_sigs)
        io   = KernelIO(mock, stream_map, stream_id=99)
        buf  = _Buffers()

        # Prime: ensure detector starts at 0
        mock.set(det_sig, 0)
        io.read_inputs(buf)
        assert buf.dets[det_idx] == 0

        # Rising edge — latch arms inside this call
        mock.set(det_sig, 1)
        t_arm = time.perf_counter()
        io.read_inputs(buf)
        assert buf.dets[det_idx] == 1, f'{det_sig}: expected 1 on rising edge'

        # Raw drops to 0 immediately
        mock.set(det_sig, 0)

        # Poll every 1ms until latch releases
        hold_end = None
        while (time.perf_counter() - t_arm) < 0.300:
            io.read_inputs(buf)
            if buf.dets[det_idx] == 0 and hold_end is None:
                hold_end = time.perf_counter()
                break
            time.sleep(0.001)

        if hold_end is None:
            hold_ms = 300.0   # never released — fail
        else:
            hold_ms = (hold_end - t_arm) * 1000

        results.append((det_sig, hold_ms))
        # Allow latch to expire before next iteration
        time.sleep(0.01)

    print(f'  Signal         hold (ms)   target 150ms ± 15ms  result')
    all_ok = True
    for sig, hold_ms in results:
        ok = 140 <= hold_ms <= 165
        all_ok = all_ok and ok
        print(f'  {sig:<14s} {hold_ms:7.1f}ms   [140 – 165]          {_ok(ok)}')

    # Verify latch does NOT affect confirms or crb
    mock2 = _MockClient(all_sigs)
    io2   = KernelIO(mock2, stream_map, stream_id=99)
    buf2  = _Buffers()
    mock2.set(stream_map['confirms'][0], 1)
    io2.read_inputs(buf2)
    mock2.set(stream_map['confirms'][0], 0)
    io2.read_inputs(buf2)
    conf_unlatched = buf2.confs[0] == 0
    print(f'  Confirms have no latch:    {_ok(conf_unlatched)}')
    all_ok = all_ok and conf_unlatched

    return all_ok


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print('=' * 62)
    print('  Phase 1.6 gate test')
    print('=' * 62)

    stream_map = load_stream_map(STREAMS_JSON, 0)
    all_signals = stream_map['detectors'] + stream_map['confirms'] + [stream_map['crb']]

    client = IOBusClient('pci.mova.kernel@0')
    print('\nWaiting for IOBus connection...')
    deadline = time.time() + 5
    while not client.connected and time.time() < deadline:
        time.sleep(0.1)

    if not client.connected:
        print(f'{_R}ABORT{_E}: could not connect to IOBus within 5 seconds')
        sys.exit(1)
    print('Connected.')

    buffers = _Buffers()
    io      = KernelIO(client, stream_map, stream_id=0)

    r1 = test_1_batch_latency(client, all_signals)
    r2 = test_2_3_tick_timing(io, buffers)
    r4 = test_4_latch(stream_map)

    print('\n' + '=' * 62)
    print('  Gate summary')
    hr()
    print(f'  Test 1  BATCH latency p99 < 0.5ms:       {_ok(r1)}')
    print(f'  Test 2  Tick interval p99 < 110ms:        {_ok(r2)}')
    print(f'  Test 3  IOBus/tick p99 < 0.5ms:           {_ok(r2)}')
    print(f'  Test 4  Latch hold 140-165ms per det:     {_ok(r4)}')
    print('=' * 62)

    all_pass = r1 and r2 and r4
    if all_pass:
        print(f'  {_G}ALL TESTS PASSED — gate cleared for Phase 2{_E}')
    else:
        print(f'  {_R}GATE FAILED — do not proceed to Phase 2{_E}')
    print('=' * 62)

    sys.exit(0 if all_pass else 1)


if __name__ == '__main__':
    main()
