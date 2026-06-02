# PCI Platform — Master Architecture Document

---

## CRITICAL — Read this before writing a single line of code

This document is the source of truth for the PCI platform architecture.
It was produced through careful design sessions and must be followed exactly.

### Rules for Claude Code sessions

1. **Read before writing.** Before any code is produced, read and summarise
   the relevant existing files. State what exists. Wait for confirmation.

2. **One task per session.** The task will be stated explicitly at the start.
   Do not extend scope, do not refactor things that were not asked about,
   do not anticipate future phases.

3. **Do not guess.** If something is unclear, ask. Do not infer and proceed.

4. **Do not rewrite working code.** If it works, it stays. The job is to
   extend and connect, not to rewrite.

5. **Do not blame the architecture.** If something is not working, find the
   actual cause. Do not suggest reverting architectural decisions.

6. **Do not please.** Accuracy matters more than agreement. If the approach
   is wrong, say so clearly and explain why.

---

## Existing codebases — reference only

### `/opt/MOVA` — DO NOT EDIT

The working MOVA implementation. Tested and proven. This is the reference
for all MOVA kernel, AML protocol, dataset handling, and web UI behaviour.

When building `/opt/pci/mova/`, read `/opt/MOVA/` to understand what works.
Do not copy blindly — the new architecture has structural differences (IOBus,
IPC sockets, kernel/web split). But the core logic, protocol handling, and
algorithm behaviour are correct and should be preserved exactly.

Key files to read before any MOVA work:
- `pci_mova/core/stream.py` — the kernel tick loop, the heart of the system
- `pci_mova/core/kernel_io.py` (currently `cm5_io.py`) — IO layer
- `pci_mova/protocol/aml_server.py` — MOVA Tools AML protocol
- `pci_mova/api/app.py` — existing web layer (being split out)

### `/opt/CM5` — DO NOT EDIT

The working CM5 monolith — UG405, RTIG, autodim, offline plans, IOBus server.
Tested and proven in field deployments.

When building `/opt/pci/` services, read `/opt/CM5/` to understand the
existing protocol implementations and IOBus server design.

Key files to read before relevant work:
- `core/io_bus_server.py` — IOBus socket server, signal table, BATCH/W protocol
- `ug405/` — UG405/SCOOT protocol implementation
- `rtig/` — RTIG message handling
- `autodim/` — dimming logic

**Neither codebase should be modified under any circumstances.**
They are the proven reference. `/opt/pci/` is the new platform being built
from them.

---

## Platform overview

The PCI platform is a collection of independent services communicating via
Unix domain sockets. Every service is isolated — it can crash and restart
without affecting any other service.

The IOBus is the hardware backbone. It is the only process that touches
physical hardware. All other services read from and write to the IOBus
signal table via a unix socket.

The web process is the only process that serves HTTP. It aggregates live
data from all service IPC sockets and forwards to browsers. No kernel or
control process contains any HTTP server code (except MOVA Tools AML which
is a direct TCP engineering connection, not HTTP).

---

## Service naming convention

All services follow `pci.<domain>` pattern.

| Systemd unit                  | Process name          | Description                        |
|-------------------------------|-----------------------|------------------------------------|
| `pci-iobus.service`           | `pci.iobus`           | IOBus server — hardware backbone   |
| `pci-mova-kernel@0.service`   | `pci.mova.kernel@0`   | MOVA kernel instance 0             |
| `pci-mova-kernel@1.service`   | `pci.mova.kernel@1`   | MOVA kernel instance 1             |
| `pci-ug405.service`           | `pci.ug405`           | UG405 / SCOOT UTC comms            |
| `pci-rtig.service`            | `pci.rtig`            | RTIG message adapter               |
| `pci-autodim.service`         | `pci.autodim`         | Automatic signal dimming           |
| `pci-offline.service`         | `pci.offline`         | Offline plan player                |
| `pci-agd.service`             | `pci.agd`             | AGD detector adapter               |
| `pci-flir.service`            | `pci.flir`            | FLIR camera adapter                |
| `pci-web.service`             | `pci.web`             | Web UI — aggregates all services   |

---

## Directory structure

```
/opt/pci/
│
├── iobus/
│   ├── server.py               ← IOBus server + signal table + conditioning
│   ├── driver_xkop.py          ← XKOP TCP client → TLC XKOP server
│   ├── driver_rpdb.py          ← RPDB TCP client → TLC RPDB server
│   ├── driver_gpio.py          ← GPIO → CM5 pins
│   ├── driver_agd.py           ← AGD TCP client → AGD server
│   ├── driver_flir.py          ← FLIR TCP client → FLIR server
│   └── driver_sim.py           ← Simulation driver (replaces hardware)
│
├── mova/
│   ├── kernel_main.py          ← entry: pci.mova.kernel N
│   │                              main thread = tick loop
│   │                              IPC server on background thread
│   ├── core/                   ← kernel, buffers, stream — from /opt/MOVA
│   ├── kernel_io.py            ← IOBus client for MOVA
│   │                              BATCH reads detectors/confirms/CRB
│   │                              W writes forces/specials
│   │                              150ms rising-edge latch on detector reads
│   │                              (MOVA algorithm requirement, not our choice)
│   ├── ipc/
│   │   ├── server.py           ← push socket + command socket (runs in kernel)
│   │   └── client.py           ← KernelClient + KernelRegistry (runs in web)
│   ├── protocol/               ← MOVA Tools AML TCP server (ports 6000+N)
│   │                              direct connection, bypasses web entirely
│   ├── datasets/               ← .mxds files + stream state JSON
│   └── logs/                   ← JSONL per stream per day (see log rotation)
│
├── ug405/
│   ├── service.py              ← entry: pci.ug405
│   │                              SNMP agent UDP 161 — instation polls us
│   │                              unix socket client → IOBus
│   ├── iobus_client.py         ← IOBus read/write (raw signals, no latch)
│   ├── ipc/
│   │   └── server.py           ← push + command sockets → web
│   └── protocol/               ← UG405/SCOOT — from /opt/CM5
│
├── rtig/
│   ├── service.py              ← entry: pci.rtig
│   │                              TCP server ← TLC/external connects in
│   │                              validates messages, W writes to IOBus
│   │                              does NOT read IOBus
│   ├── iobus_client.py         ← IOBus write only
│   └── protocol/               ← RTIG — from /opt/CM5
│
├── autodim/
│   ├── service.py              ← entry: pci.autodim
│   │                              reads photocell from IOBus
│   │                              writes dim level to IOBus
│   └── iobus_client.py
│
├── offline/
│   ├── service.py              ← entry: pci.offline
│   │                              UTC fallback plan player
│   └── iobus_client.py
│
├── agd/
│   ├── service.py              ← entry: pci.agd
│   │                              TCP client → AGD server
│   │                              message adapter → IOBus
│   └── iobus_client.py
│
├── flir/
│   ├── service.py              ← entry: pci.flir
│   │                              TCP client → FLIR server
│   │                              message adapter → IOBus
│   └── iobus_client.py
│
├── web/
│   ├── web_main.py             ← entry: pci.web
│   │                              FastAPI + uvicorn on :8080
│   │                              connects to all service IPC sockets
│   │                              reads JSONL logs directly (no kernel involved)
│   ├── api/
│   │   ├── app.py
│   │   ├── routes/             ← mova.py, ug405.py, iobus.py, rtig.py ...
│   │   └── ws/                 ← live.py — SSE/WebSocket push to browser
│   └── static/
│       ├── design.html     ← component library — READ BEFORE ANY UI WORK
│       │                      built by extracting CM5 + MOVA styles
│       │                      all CSS variables defined here
│       │                      no styles invented outside this file
│       ├── css/pci.css     ← single stylesheet, all services
│       ├── js/
│       │   ├── sse.js      ← SSE connection handler (shared)
│       │   ├── mova.js     ← MOVA stream UI
│       │   ├── ug405.js    ← UG405 UI
│       │   └── iobus.js    ← IOBus signal viewer
│       └── templates/
│           ├── index.html       ← main dashboard
│           ├── mova_stream.html ← per-stream detail
│           └── ug405.html       ← UG405 detail
│
├── shared/
│   ├── iobus_client.py         ← base IOBus client (all services inherit)
│   └── log_rotate.py           ← shared log rotation utility
│
├── config/
│   ├── platform.cfg            ← which services are enabled
│   ├── signals.cfg             ← signal ownership map
│   └── streams.json            ← per MOVA stream IO signal mappings
│
└── logs/
    ├── pci.iobus.log
    ├── pci.ug405.log
    ├── pci.web.log
    └── mova/
        ├── pci.mova.0_2026-06-01.jsonl       ← today, live, uncompressed
        ├── pci.mova.0_2026-05-31.jsonl.gz    ← previous days, compressed
        └── pci.mova.1_2026-06-01.jsonl
```

---

## Client/server relationships

This is fixed by protocol and hardware — not our choice.

### Hardware servers — we connect as client

| Hardware              | Our driver/service     | Direction              |
|-----------------------|------------------------|------------------------|
| TLC XKOP server       | `driver_xkop.py`       | TCP client → TLC       |
| TLC RPDB server       | `driver_rpdb.py`       | TCP client → TLC       |
| AGD server            | `driver_agd.py`        | TCP client → AGD       |
| FLIR server           | `driver_flir.py`       | TCP client → FLIR      |
| UTC instation         | `pci.ug405`            | SNMP agent UDP 161     |

### We are the server — hardware/external connects to us

| Our service           | Who connects in        | Protocol               |
|-----------------------|------------------------|------------------------|
| `pci.rtig`            | TLC / external client  | RTIG TCP               |
| `pci.mova.kernel@N`   | MOVA Tools (Windows)   | AML TCP port 6000+N    |
| `pci.iobus`           | all services           | Unix socket BATCH/W    |

### IOBus is a pure server

`pci.iobus` has no client code. Everything else connects to it.
Drivers run inside the IOBus process — they are not separate services.

---

## Socket reference

### IOBus

| Socket                        | Direction              | Protocol              |
|-------------------------------|------------------------|-----------------------|
| `/tmp/pci.iobus.sock`         | services ↔ IOBus       | BATCH/W newline-JSON  |
| `/tmp/pci.iobus.live.sock`    | IOBus → web            | JSON push             |

### MOVA kernel (per instance N = 0,1,2...)

| Socket                        | Direction              | Protocol              |
|-------------------------------|------------------------|-----------------------|
| `/tmp/pci.mova.N.live.sock`   | kernel → web           | JSON push             |
| `/tmp/pci.mova.N.cmd.sock`    | web → kernel           | command/ack           |
| TCP port `6000+N`             | MOVA Tools → kernel    | AML (direct)          |

### Service sockets

| Socket                        | Direction              |
|-------------------------------|------------------------|
| `/tmp/pci.ug405.live.sock`    | ug405 → web            |
| `/tmp/pci.ug405.cmd.sock`     | web → ug405            |
| `/tmp/pci.rtig.live.sock`     | rtig → web             |
| `/tmp/pci.autodim.live.sock`  | autodim → web          |
| `/tmp/pci.offline.live.sock`  | offline → web          |

---

## IPC protocol (kernel ↔ web)

All IPC messages are newline-terminated JSON. Every message carries `"v":1`.

### Push socket (kernel → web, one way)

```json
{"v":1,"t":"snap","ts":1234.5, ...full snapshot fields... }
{"v":1,"t":"phase_change","ts":1234.5,"from":2,"to":4}
{"v":1,"t":"fault","ts":1234.5,"error_id":5,"data":0}
{"v":1,"t":"stage_forced","ts":1234.5,"stage":3}
{"v":1,"t":"msg","ts":1234.5,"type":1,"sub":1,"desc":"...","seq":42}
```

Snapshot rate: 1-2Hz.
Events (phase change, fault, stage force): immediate on occurrence.
Do NOT push a full snapshot every 100ms tick — this was a previous design
error that caused GIL contention and memory growth.

### Command socket (web → kernel, request/ack)

Web opens socket, sends one line, reads one ack, closes socket.

```
PING\n                          → {"v":1,"t":"pong"}
LOAD /path/file.mxds id\n       → {"v":1,"t":"ack","cmd":"LOAD","ok":true}
UNLOAD\n                        → {"v":1,"t":"ack","cmd":"UNLOAD","ok":true}
FORCE_STAGE 3\n                 → {"v":1,"t":"ack","cmd":"FORCE_STAGE","ok":true}
SWITCH_PLAN 2\n                 → {"v":1,"t":"ack","cmd":"SWITCH_PLAN","ok":true}
SET_IO index value\n            → {"v":1,"t":"ack","cmd":"SET_IO","ok":true}
SET_DET index value\n           → {"v":1,"t":"ack","cmd":"SET_DET","ok":true}
SET_CONFIRM index value\n       → {"v":1,"t":"ack","cmd":"SET_CONFIRM","ok":true}
SET_CRB value\n                 → {"v":1,"t":"ack","cmd":"SET_CRB","ok":true}
CONNECT_XKOP host port\n        → {"v":1,"t":"ack","cmd":"CONNECT_XKOP","ok":true}
DISCONNECT_XKOP\n               → {"v":1,"t":"ack","cmd":"DISCONNECT_XKOP","ok":true}
SET_SPEED mult\n                → {"v":1,"t":"ack","cmd":"SET_SPEED","ok":true}
SET_TOD_OFFSET hours\n          → {"v":1,"t":"ack","cmd":"SET_TOD_OFFSET","ok":true}
RESET\n                         → {"v":1,"t":"ack","cmd":"RESET","ok":true}
```

---

## IOBus protocol (services ↔ IOBus)

```
BATCH signal1 signal2 signal3\n    → 1 0 1\n
W signal1 1\n                      → OK\n
```

Round trip on unix socket: <0.5ms. Well within 100ms MOVA tick budget.

Signal conditioning (debounce, inversion, scaling) is applied inside the
IOBus server before any service reads a value. Services always see clean signals.

---

## Signal ownership — rules

### Reads — unrestricted

Any service can read any signal from the IOBus signal table at any time.
No ownership, no restriction, no configuration required.

### Writes — owner only

Every output signal has exactly one registered owner in `config/signals.cfg`.
Only the registered owner can write that signal.
Any write attempt from a non-owner is rejected by IOBus.

### Shared outputs

Some output signals may be shared between services where the deployment
requires it — for example, stage force outputs may be controlled by MOVA,
UG405, or offline plans depending on which service is in control at the time.
Shared ownership is defined in `config/signals.cfg` using a comma-separated
list of service names:

```ini
[signals]
xkop.o.101 = pci.ug405, pci.offline    # both services may write
xkop.o.102 = pci.ug405, pci.offline
```

The IOBus server stores shared owners as a frozenset. Any service in the set
may write; non-listed services are rejected as before. Services take turns
writing based on opMode — there is no arbitration in the IOBus itself.

### Signal allocations — deployment config, not defined here

Which specific signals belong to which service, which MOVA stream, which
UG405 instance, which offline plan — this is all deployment-specific.
It is mapped out per junction per installation and configured via:

- `config/signals.cfg` — signal ownership and sharing rules
- `config/streams.json` — per MOVA stream signal mappings
- Web UI — signal allocation per stream and per service
  (same UI pattern for MOVA streams, UG405, offline plans etc.)

The platform enforces the rules. It does not define the mappings.

### 150ms detector latch

Applied in `kernel_io.py` only, after reading raw detector values from IOBus.
The IOBus signal table always holds raw unlatched values.
All other services read raw values and handle timing themselves.

---

## MOVA kernel threading model

```
main thread      ← tick loop (MovaStream._loop(), 100ms)
                   SIGTERM lands here — clean controlled shutdown
IPC thread       ← push socket + command socket server
MOVA Tools thread← AML TCP server (port 6000+N)
```

Main thread owns the tick. This is deliberate — Python signal handlers only
fire on the main thread. Tick on main guarantees SIGTERM → clean stop.

---

## MOVA kernel — one codebase, N instances

Same Python files and libmova.so launched N times with a stream index argument.
No per-instance file copying.

```bash
python -m pci.mova.kernel_main 0    # stream 0
python -m pci.mova.kernel_main 1    # stream 1
```

Per-instance state derived from stream index N:
- `/tmp/pci.mova.N.live.sock`
- `/tmp/pci.mova.N.cmd.sock`
- TCP port `6000+N` (MOVA Tools)
- `stream_state_N.json`
- `logs/mova/pci.mova.N_YYYY-MM-DD.jsonl`
- One `MovaStream` object in memory

Licence validation: if stream N is not licensed, process exits cleanly (code 0).
`Restart=on-failure` does not loop on a clean exit.

Realistic deployments: average 2 streams, maximum 4, 8 is theoretical maximum.

---

## 150ms detector latch — MOVA algorithm requirement

The latch is a MOVA specification requirement, not a platform choice.

**Why:** MOVA kernel scans detectors every 100ms. A short vehicle pulse could
arrive between scans and be missed. 150ms hold guarantees overlap into the
next scan regardless of pulse phase. Real M8 hardware applies the same latch.

**Where:** `kernel_io.py` only, on rising edge (0→1), before feeding `buffers.din[]`.
The IOBus signal table holds raw unlatched values. UG405 and other services
read raw values — they have their own timing requirements and do not need the latch.

**Latency implication:** detector → force worst case is ~253ms. This matches M8
hardware behaviour exactly. It is not a platform limitation — it is the algorithm.

---

## Log storage and rotation

```
Live (today):      .jsonl      uncompressed, appended every tick
Previous days:     .jsonl.gz   gzip compressed at 00:05 via systemd timer

Compression ratio: ~15:1 for repetitive JSON
One stream, one day raw: ~432MB
One stream, one day compressed: ~25-30MB
30 days, two streams: ~2GB total

Retention: 30 days default, configurable
```

The kernel process never performs compression or rotation.
A separate systemd oneshot timer handles this entirely.
Web process decompresses on demand for history/playback — kernel unaffected.

---

## Full data flow

```
Physical hardware
XKOP(TLC) / RPDB(TLC) / GPIO / AGD / FLIR
       │
       │ TCP (client connects out to hardware servers)
       ▼
┌──────────────────────────────────────────────┐
│                 pci.iobus                    │
│                                              │
│  driver_xkop@N  driver_rpdb  driver_gpio    │
│  driver_agd     driver_flir  driver_sim      │
│                                              │
│  signal table (in memory)                   │
│  conditioning rules                         │
│  ownership enforcement                      │
└──────┬───────────────────────────────────────┘
       │ /tmp/pci.iobus.sock (BATCH/W)
       │
  ┌────┴────┬──────────┬──────────┬──────────┐
  ▼         ▼          ▼          ▼          ▼
mova@0   mova@1    pci.ug405  pci.rtig  pci.autodim
  │                   │                  pci.offline
  │ kernel_io.py      │ iobus_client.py
  │ 150ms latch       │ raw signals
  │
  ├── /tmp/pci.mova.N.live.sock  (push → web)
  ├── /tmp/pci.mova.N.cmd.sock   (commands ← web)
  └── TCP 6000+N                 (MOVA Tools AML, direct)
                                  ↑
                             Windows engineer
                             validation only

  pci.ug405 → /tmp/pci.ug405.live.sock
  pci.iobus → /tmp/pci.iobus.live.sock
  (all service live sockets → web)
               │
               ▼
           pci.web
           FastAPI + uvicorn :8080
           KernelRegistry
           reads JSONL logs directly
               │
               │ SSE / WebSocket
               ▼
            Browser
```

---

## Service connection summary

```
External protocol           Service                    IOBus
─────────────────           ───────                    ──────
UTC instation (polls)  ←→   pci.ug405            ←→   signal table
TLC XKOP (server)      ←    driver_xkop@N         →   signal table
TLC RPDB (server)      ←    driver_rpdb            →   signal table
AGD (server)           ←    driver_agd             →   signal table
FLIR (server)          ←    driver_flir            →   signal table
GPIO CM5 pins         ←→    driver_gpio           ←→   signal table
simulation              →   driver_sim             →   signal table
TLC (client)           →    pci.rtig               →   signal table (W only)
photocell/dusk         ←    pci.autodim           ←→   signal table
MOVA Tools (client)    →    pci.mova.kernel@N     ←→   signal table
                            kernel_io.py (150ms latch on det reads)
```

---

## Timing criticality

| Service          | Cycle             | Criticality   |
|------------------|-------------------|---------------|
| `pci.mova`       | 100ms tick        | Hard          |
| `pci.ug405`      | SCOOT cycle       | Hard          |
| Detection        | Per pulse         | Hard          |
| `pci.rtig`       | Event driven      | Soft          |
| `pci.autodim`    | Seconds           | Soft          |
| `pci.offline`    | Plan steps        | Soft          |
| `pci.agd`        | Event driven      | Soft          |
| `pci.flir`       | Event driven      | Soft          |
| `pci.web`        | 1-2Hz push        | Irrelevant    |

---

## Deployment

### Development

- SER5 Proxmox, Debian LXC (x86_64)
- venv at `/opt/pci/venv`
- Run services directly, systemd optional

### Field (CM5 in controller cabinet)

- Compute Module 5, Debian ARM64
- Same venv approach — no PyInstaller for field deployment
- Shared venv at `/opt/pci/venv`, all services use it
- `libmova.so` compiled natively on ARM64 Debian LXC
- Systemd units manage all services

### Web service port

- Dev host: 8081 (8080 occupied by CM5 monolith)
- Field CM5: 8080 (no CM5 monolith present)
- Override: PCI_WEB_PORT environment variable

### ARM64 build environment

- Debian ARM64 LXC on SER5 (QEMU) mirrors CM5 exactly
- `libmova.so` compiled there: `gcc -shared -fPIC -O2 -o libmova.so mova_kernel.c`
- Validated before shipping to field unit

---

## Build order

### Phase 1 — IOBus foundation  ✓ COMPLETE

```
1.1  Signal table — in-memory dict, ownership map, read/write, conditioning
1.2  Unix socket server — BATCH read, W write, ownership enforcement
1.3  driver_sim.py — injects test signals on a timer, no hardware needed
1.4  Shared iobus_client.py base class
1.5  MOVA kernel_io.py — BATCH reads detectors/confirms/CRB, W writes forces
     Apply 150ms rising-edge latch on detector reads
1.6  Prove MOVA tick at 100ms against IOBus — confirm <0.5ms round trip
     Gate passed.
```

### Phase 2 — MOVA kernel split  ✓ COMPLETE

```
2.1  IPC server (push + command sockets) inside kernel process
2.2  kernel_main.py — main thread = tick, IPC on background thread
2.3  KernelClient + KernelRegistry in web process
2.4  Web SSE endpoint — push snapshots and events to browser
2.5  Proved: IOBus → kernel → IPC → web → browser
     Snapshot at 1Hz confirmed. kernel_version M8.0.0.435 visible end-to-end.
```

#### Phase 2 implementation notes

**Port:** pci.web runs on :8081. Port 8080 is occupied by the CM5 monolith
on this development host. The ARCHITECTURE port table shows :8080 as the
intended field port — that remains correct for field deployment.
On this dev host, override with: PCI_WEB_PORT=8081

**KernelIO.snapshot() — required method:**
MovaStream.snapshot() calls self.io.snapshot(). KernelIO must implement
snapshot(), reset_confirms(), and set_intergreen_matrix() to satisfy the
AbstractIO protocol used by the MOVA runtime. Without snapshot(), the push
loop silently catches AttributeError at DEBUG level and no snaps are sent.
These three methods are now present in kernel_io.py.

**_PciMovaStream subclass:**
kernel_main.py subclasses MovaStream as _PciMovaStream to override start().
This prevents load_dataset() from spawning a background tick thread —
the tick runs on the main thread via _loop() directly. The override sets
_running and transitions status to WARMUP, matching normal start() behaviour.

**Before writing anything in Phase 2, read:**
/opt/pci/mova/SIGNALS.md — confirmed complete output signal list.
Do not infer signal names from memory.

### Phase 3 — Real hardware drivers  ✓ COMPLETE

```
3.1  driver_xkop.py — TCP client to TLC XKOP server    ✓ COMPLETE
3.2  driver_rpdb.py — TCP client to TLC RPDB server     ✓ COMPLETE
3.3  driver_gpio.py — CM5 GPIO pins                     ✓ COMPLETE
```

#### Phase 3.1 implementation notes

**Activation:** set `driver = xkop` in `config/platform.cfg` `[iobus]` section.
Connection params (ip, port, mode) are in the `[xkop]` section of `platform.cfg`.

**Signal config:** `xkop.i.*` and `xkop.o.*` entries must be added to
`config/signals.cfg` per deployment. The driver scans signals.cfg at startup
to discover which signals to monitor and zero. No signals → driver connects
but does nothing. Signal names are deployment-specific; they are not defined here.

**PCI adaptations from CM5:**
- `source='pci.iobus'` (all IOBus drivers own signals as pci.iobus)
- `_zero_inputs()` writes 0 explicitly to each xkop.i.* on reconnect
  (SignalTable has no zero_owned_by())
- Signal names discovered by scanning signals.cfg (SignalTable has no
  registered_signals())
- CRC16 inlined in driver_xkop.py (no shared crc_utils in pci/iobus/)
- table.subscribe() confirmed present in server.py — used for event-driven send

#### Phase 3.2 implementation notes

**Activation:** set `driver = rpdb` in `config/platform.cfg` `[iobus]` section.
Connection params in `config/rpdb.cfg` `[RPDB]` section.

**Signal registration:** `rpdb.i.*` input signals are registered by the driver
at runtime after querying element counts from the controller — NOT pre-registered
in signals.cfg. Element count is only known once the controller responds.
`rpdb.o.*` output signals must be declared in signals.cfg; the driver discovers
them by scanning signals.cfg (SignalTable has no registered_signals()).

**Re-registration on reconnect:** If the controller is down at startup,
`_input_signals` will be empty. `_connect_loop()` re-runs `_fetch_names()`,
`_register_inputs()`, `_register_outputs()` on every pass until the controller
responds. Once populated, this block is skipped. This ensures the driver
recovers fully when a controller comes back up after a cold-start dropout.

**Reconnect fetch:** `_initial_fetch()` called on every (re)connect — pulls
current controller state immediately rather than waiting up to 60s for the
first subscription heartbeat. No zeroing of inputs (unlike XKOP).

**Two-socket model:** separate read socket (subscriptions) and write socket
(SET_VALUE). Write socket uses write_password. SET_VALUE requests are pipelined
— all changes for one URI sent back-to-back before reading ACKs, preventing
TLC UTC mode drops when multiple XIN bits change together.

**PCI adaptations from CM5:**
- `source='pci.iobus'` throughout
- `table.register()` called directly by driver for dynamically discovered inputs
- `io.registered_signals()` → scan signals.cfg for rpdb.o.* signals
- Config from `config/rpdb.cfg` (separate file — subscriptions/names/outputs
  sections are too complex for platform.cfg)

#### Phase 3.3 implementation notes

**Activation:** set `driver = gpio` in `config/platform.cfg` `[iobus]` section.

**Config:** `[gpio]` section in `signals.cfg`. Direction inferred from signal
name (`gp.i.*` = in, `gp.o.*` = out). Value is the BCM GPIO number:
```
[gpio]
gp.i.0 = 17
gp.o.0 = 22
```

**Fail-silent on dev:** any pin that fails to export is skipped with a warning.
If no pins export successfully the driver starts with empty sets and does nothing.
On SER5 LXC dev machine, the sysfs export file is read-only — all pins are
skipped automatically. On CM5 field hardware, exports succeed normally.

**No library dependencies.** Uses `/sys/class/gpio` sysfs via plain Python
file I/O. No gpiod, no gpioget/gpioset.

**Signal use:** GPIO is for slow signals only — photocells, panel switches,
status inputs, enable/inhibit lines. 20ms polling is adequate.
Do NOT use GPIO for vehicle detection — detection pulses require RPDB or XKOP.

#### XKOP driver reference — derived from /opt/CM5/io/io_xkop.py

Read /opt/CM5/io/io_xkop.py and /opt/CM5/core/io_bus.py in full
before writing driver_xkop.py. Do not infer behaviour from this
summary alone — the source is authoritative.

**What the driver does**

Bidirectional bridge between the IOBus signal table and a TLC via
the XKOP TCP protocol.

- Receives signal values from TLC → writes as `xkop.i.N` inputs to IOBus
- Watches IOBus for `xkop.o.N` changes → sends to TLC
- Keepalive packet every 6 seconds
- Dead-connection detection: nothing received for 3× keepalive (18s)
  → treat as dead, reconnect
- On reconnect: zero all owned `xkop.i.*` signals so stale values do
  not persist, then force a full resend of all current `xkop.o.*` values

**Connection mode**

TCP, configured per deployment:
- Client mode (normal field use): connect out to TLC IP:port
  Exponential backoff on failure: 2s initial, doubles to 300s max
- Server mode: bind and listen; TLC connects in

Socket set non-blocking after connect. Send and receive run on
separate threads.

**17-byte packet format**

```
Byte  0-1 : header  0xCA 0x35
Byte  2   : type    0x00 = data    0x02 = keepalive
Bytes 3-14: four signal slots × 3 bytes each
            [xkop_index, value_high, value_low]
            Unused slots: xkop_index = 0xFF
Bytes 15-16: CRC16 over bytes 2-14 (skip_bytes=2, length=15)
             implemented in /opt/CM5/core/crc_utils.py write_crc16()
```

Value is 16-bit: `(high_byte << 8) | low_byte`.
Up to 4 signal updates per packet. Larger batches are split into
multiple packets in groups of 4.

XKOP physical index is extracted from the signal name:
`xkop.o.101` → index 101.

**Event-driven send loop**

The CM5 driver wakes immediately on IOBus change via a bus
subscription callback → `threading.Event.set()` → send loop wakes.

In PCI, drivers run inside `pci.iobus` and the IOBus signal table
notifies subscribers synchronously on every write. Use the same
pattern: subscribe to the signal table, wake a send event when any
`xkop.o.*` signal changes. Do not poll — match the CM5 pattern.

**Signal registration**

`xkop.i.*` signals are owned by the XKOP driver (it writes them).
`xkop.o.*` signals are owned by their respective services (MOVA,
UG405 etc.) — the driver only reads them.

Ownership is declared in `signals.cfg` exactly as for any other
signal. The driver must call `zero_owned_by` (write 0 to all its
owned input signals) on every reconnect.

**CM5 vs PCI mapping**

| CM5                                        | PCI equivalent                              |
|--------------------------------------------|---------------------------------------------|
| `io.subscribe(cb)` — push on any change    | `table.subscribe(cb)` in server.py          |
| `io.write(name, val, source='xkop_driver')`| `table.write(name, val, source)`            |
| `io.read(name)`                            | `table.read(name)`                          |
| `io.zero_owned_by('xkop_driver')`          | write 0 to each owned signal on reconnect   |
| Runs inside CM5 monolith process           | Runs inside `pci.iobus` process             |
| Signal names: `xkop.i.N` / `xkop.o.N`     | Same naming convention — carry it over      |

### Phase 4 — UG405  ✓ COMPLETE (4.1 + 4.2)

```
4.1  pci.ug405 — SNMP agent UDP 161, instation polls us, reads/writes IOBus  ✓ COMPLETE
4.2  IPC live socket to web                                                    ✓ COMPLETE
4.3  Web aggregates UG405 alongside MOVA                                       (Phase 5 web work)
```

#### Phase 4 prerequisites — completed

**pysnmp pinned to 4.4.12** — matches CM5 reference implementation exactly.
CM5 uses asyncore-based pysnmp v4 API. pyasn1 pinned to 0.4.8 (v4.4.12
incompatible with newer pyasn1). Both pinned in requirements.txt.

**pysnmp v7 migration deferred to Phase 7** (ARM64 field validation).
v7 breaks: `pysnmp.carrier.asyncore` removed (asyncore gone in Python 3.12),
entire API renamed camelCase → snake_case, `runDispatcher()` replaced by
asyncio event loop. Field target is Python 3.11 (asyncore still present) —
safe to defer. Revisit at Phase 7 when CM5 ARM64 Debian Python version confirmed.

**SNMP agent model confirmed** — pci.ug405 is the SNMP agent (UTC Type 2
outstation). Instation (UTC central) polls us via SNMP GET/SET on UDP 161.
For RBE, we push INFORM packets to InstationAddress:InstationPort (UDP, optional).
No outbound TCP connection. ARCHITECTURE.md client/server table corrected.

#### Phase 4 implementation notes

**subscribe() is poll-based** — CM5's IOBus is in-process, so `io.subscribe(cb)` fires
synchronously on every write. PCI's IOBus is a socket server; external services have no
push channel. `UG405IOBus.set_monitored(signal_names)` starts a 100ms poll thread that
reads all reply signals via BATCH and fires callbacks on change. Same contract as CM5 —
`cb(name, value, source)`. Call `set_monitored()` once after `load_ug405()`.

**io.zero_owned_by('ug405')** → `io.zero_owned(control_signals)` — CM5 zeroes by owner
string (in-process IOBus knows ownership). PCI: ownership is in signals.cfg. The service
derives `_control_signals` from `cfg['signals'].keys()` at startup.

**Control signal ownership** — control signals must be listed in `config/signals.cfg` as
owned by `pci.ug405`. Example for a deployment with XKOP:
```
xkop.o.101 = pci.ug405
xkop.o.102 = pci.ug405
```
Reply signals are owned by `pci.iobus` (written by drivers) — no signals.cfg entry needed.

**Config path** — defaults to `/opt/pci/config/ug405.cfg`. Override with
`PCI_UG405_CFG=/path/to/ug405.cfg` environment variable.

**Entry point:**
```bash
PYTHONPATH=/opt /opt/pci/venv/bin/python -m pci.ug405.service
```

**Phase 4.3 deferred** — web routes for UG405 (`web/api/routes/ug405.py`) are built
in Phase 5 web work alongside rtig, autodim etc. The IPC push socket is ready; web-side
wiring is the remaining piece.

### Phase 5 — Remaining services  ✓ COMPLETE

Before writing any Phase 5 service:
Read the equivalent service in /opt/CM5 first.
Summarise what it does and what PCI adaptations
are needed. Wait for confirmation before writing.

```
5.1  pci.rtig  — HTTP receiver on :9010 inside pci.web, W writes to IOBus  ✓ COMPLETE
5.2  pci.autodim                                                             ✓ COMPLETE
5.3  pci.offline                                                             ✓ COMPLETE
5.4  pci.agd / pci.flir                                                      ✓ COMPLETE
```

#### Phase 5.4 agd/flir — implementation notes

**pci.agd — AGD650 radar detector adapter**

Transport: ZeroMQ SUB socket. AGD650 is the publisher; we subscribe to
`tcp://<ip>:<port>`. One thread per unit. ZMQ handles reconnect implicitly.

Change detection: full snapshot frame arrives every ~150ms. Only writes IOBus
when zone state or class presence changes vs previous frame — not on every frame.

Signal ownership: `pci.agd` owns all agd.* signals. Declared in signals.cfg.
Signal names discovered via [VIRT_MAPPING] in agd.cfg (unit-qualified key first,
global fallback). Same scan approach as XKOP/RPDB.

Fault detection: 500ms recv timeout. If no frame received for `frame_timeout`
seconds (default 5.0), zeros all unit signals and pushes fault event over IPC.

Write-only IOBus adapter — does not read IOBus except to compute global OR bits.

**pci.flir — FLIR camera adapter**

Transport: WebSocket client (`websocket-client` library). FLIR camera is the
WS server at `ws://<ip>:<port>`. One thread per camera running `run_forever()`.
Sends subscription message on connect. Reconnects with 5s sleep on close.

Event-driven: no polling. Processes `messageType=Event` messages immediately.
Writes IOBus signal on each event: Presence/Pedestrian → `occupied`,
DilemmaZone → `dilemma`, class field → `has_pedestrian`/`has_bicycle`/`has_vehicle`.

Signal ownership: `pci.flir` owns all flir.* signals. Declared in signals.cfg.
Signal names via [VIRT_MAPPING] in flir.cfg (camera-qualified first, global fallback).

Write-only IOBus adapter — reads back zone signals only to compute global OR bits.

**Dependencies added (Phase 5.4):**
- `pyzmq` — ZeroMQ bindings for AGD ZMQ SUB socket
- `websocket-client` — WebSocket client library for FLIR camera connection
Both added to requirements.txt.

#### Phase 5.2 autodim — pre-write notes (read /opt/CM5/autodim/ before writing)

**What it does:** Astronomical dim/bright controller. Uses `astral` (lat/lon) to
calculate daily sunrise/sunset. Applies minute offsets to get dim_utc (sunset +
offset) and bright_utc (sunrise + offset). Writes 1 (dim) or 0 (bright) to one
IOBus output signal on a 30-second loop.

**Algorithm:**
```
is_dim = (now >= dim_utc) OR (now < bright_utc)
```

**PCI adaptations:**
- Drop `io.register()` / `io.unregister()` — not in PCI IOBus; ownership is
  static in signals.cfg. Declare autodim output signal as owned by `pci.autodim`.
- IOBus client: `IOBusClient('pci.autodim')` — write(sig, val) + batch([sig]).
- IPC push socket: autodim/ipc/server.py + client.py (same pattern as ug405/rtig).
  Push 1Hz snapshots + immediate event on dim/bright transition.
- Config: /opt/pci/config/autodim.cfg — lat, lon, dim_offset, bright_offset,
  signal, enabled. Override via PCI_AUTODIM_CFG.
- save_autodim() ports unchanged — same file I/O, different path.

**Dependency:** `astral` — added to requirements.txt.
**TODO before writing:** confirm `astral` installs cleanly in the venv.

### Phase 6 — Log management

```
6.1  Systemd timer for gzip rotation at 00:05
6.2  30-day retention policy
6.3  driver_sim.py JSONL replay mode — replay real junction recordings
```

### Phase 7 — ARM64 field deployment

```
7.1  Debian ARM64 LXC on SER5
7.2  libmova.so ARM64 compile and test
7.3  Full service stack on ARM64 — prove identical to x86 dev
7.4  CM5 bench unit validation
```

---

## Claude Code session template

Copy this at the start of every session:

```
Working directory: /opt/pci/
Reference (read only, do not edit): /opt/MOVA/ and /opt/CM5/

Today's task: [ONE specific thing]

Before writing anything:
1. Read [specific files listed here]
2. Summarise what exists in those files
3. Wait for my confirmation before writing any code

Do not extend scope beyond the stated task.
Do not rewrite anything that is not directly part of the task.
Do not guess — ask if something is unclear.
```

---

## UI design system

### Problem
CM5 and MOVA UIs are inline CSS inside Python files.
No shared stylesheet. Styles drift between services.
Claude Code copies bits and misses others.

### Solution
/opt/pci/web/static/design.html — single self-contained
design reference file. Built by extracting and unifying
CSS from both existing UIs.

### First task before any UI work
Read /opt/CM5/web/web.py and /opt/MOVA equivalent.
Extract all inline CSS, colours, fonts, spacing.
Resolve conflicts in favour of CM5 where it looks better.
Build design.html with every component defined.

### Rule for all future UI work
All PCI service UIs must copy styles from design.html.
Do not invent colours, spacing, or component styles.
Do not add inline CSS to Python files.
All styles live in design.html and linked stylesheets only.

design.html must exist before any template work starts.
Claude Code must read design.html before writing
any HTML or CSS. No styles invented outside this file.

Before building any MOVA UI page, read the equivalent 
popup from /opt/MOVA/pci_mova/static/ and port it 
directly. Do not rebuild from scratch.

Pages to port:
- Dataset popup    → /opt/MOVA/pci_mova/static/dataset.html
- Derived popup    → /opt/MOVA/pci_mova/static/derived.html  
- Analysis popup   → /opt/MOVA/pci_mova/static/analysis.html
- Messages popup   → /opt/MOVA/pci_mova/static/messages.html
- Errors popup     → /opt/MOVA/pci_mova/static/errors.html
- History popup    → /opt/MOVA/pci_mova/static/history.html

Read the file, understand it, adapt to new CSS/JS 
structure. Never rewrite from memory.

## End of every Claude Code session

Before stopping, Claude Code must:
1. Mark completed phases in build order
2. Record any decisions not already in this document
3. Note outstanding issues for next session
4. git commit all changes
5. git push to remote
6. Append a summary to /opt/pci/PHASE_LOG.md covering: what was built,
   decisions made, test results with actual numbers, any issues found.
   Do not overwrite — always append.

## Python environment

Shared venv at /opt/pci/venv — all services use this.
Do not create per-service venvs.

Create if not exists:
  python3 -m venv /opt/pci/venv

Install packages:
  /opt/pci/venv/bin/pip install -r /opt/pci/requirements.txt

Systemd units use:
  /opt/pci/venv/bin/python

requirements.txt lives at /opt/pci/requirements.txt
Add packages there as new services require them.
All services share the same requirements.txt.

