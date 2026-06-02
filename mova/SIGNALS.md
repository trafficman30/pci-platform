# MOVA Kernel IO — Confirmed Output Signal List

Confirmed by design session before Phase 2.
Derived from: /opt/MOVA/pci_mova/core/kernel/wrapper.py (tick),
              /opt/MOVA/pci_mova/core/model/buffers.py,
              /opt/MOVA/pci_mova/core/io/cm5_io.py

## Outputs written by kernel_io.py write_outputs()

| Group          | Signal name   | buffers source              | Count |
|----------------|---------------|-----------------------------|-------|
| Stage forces   | force.0       | buffers.get_force(1)        |       |
|                | force.1       | buffers.get_force(2)        |       |
|                | ...           | ...                         |       |
|                | force.9       | buffers.get_force(10)       | 10    |
| Turn-On        | to            | buffers.dout[DOUT_TO=16]    | 1     |
| Hold Inhibit   | hi            | buffers.dout[DOUT_HI=17]    | 1     |
| Sync pulse     | sync          | buffers.dout[DOUT_SYNC=18]  | 1     |
| Det fault      | det_fault     | buffers.dout[DOUT_DET_FAULT=19] | 1 |
| MOVA fault     | mova_fault    | buffers.dout[DOUT_MOVA_FAULT=20] | 1 |
| Special outputs| special.0     | buffers.special_outputs[0]  |       |
|                | special.1     | buffers.special_outputs[1]  |       |
|                | ...           | ...                         |       |
|                | special.7     | buffers.special_outputs[7]  | 8     |

**Total: 23 output signals per stream.**

## Excluded

- **Hold (control[13]) / Release (control[14])** — M8_IMPROVED_LINKING IS compiled
  into libmova.so (confirmed via nm exports: is_ep_hold_or_ext_present etc.) but
  wrapper.py tick() does not copy control[13]/[14] to buffers.dout[]. Not accessible
  without modifying /opt/MOVA. Excluded until wrapper surfaces them.

## streams.json must be updated before kernel_main.py

The existing streams.json only has forces. Before writing kernel_main.py,
streams.json must be extended to include all 23 outputs above, plus the
existing inputs (detectors, confirms, crb).

signals.cfg must also register all 23 output signals as owned by
pci.mova.kernel@N (where N = stream index).
