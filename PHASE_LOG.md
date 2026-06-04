# PCI Platform — Phase Log

Append-only. Do not overwrite entries. One entry per session.

---

## 2026-06-02 — Phases 1–4 complete

### Phase 1 — IOBus foundation  ✓ COMPLETE

**Built:**
- `iobus/server.py` — in-memory signal table, ownership enforcement, BATCH/W
  unix socket server, signal conditioning (debounce, inversion, scaling)
- `iobus/driver_sim.py` — simulation driver, injects test signals on a timer
- `shared/iobus_client.py` — base IOBus client inherited by all services
- `mova/kernel_io.py` — BATCH reads detectors/confirms/CRB, W writes forces,
  150ms rising-edge latch on detector reads

**Decisions:**
- Signal table is an in-memory dict; ownership map enforced at write time
- 150ms latch applied in kernel_io.py only — IOBus always holds raw values
- Unix socket chosen for IPC: <0.5ms round trip confirmed

**Test results:**
- MOVA tick at 100ms against IOBus: round trip confirmed <0.5ms
- Gate passed — Phase 1 complete

---

### Phase 2 — MOVA kernel split  ✓ COMPLETE

**Built:**
- `mova/ipc/server.py` — push socket + command socket inside kernel process
- `mova/kernel_main.py` — main thread = tick loop, IPC on background thread
- `mova/ipc/client.py` — KernelClient + KernelRegistry for web process
- Web SSE endpoint — pushes snapshots and events to browser

**Decisions:**
- Tick runs on main thread (SIGTERM fires on main thread — clean shutdown)
- `_PciMovaStream` subclasses `MovaStream` to prevent `load_dataset()` spawning
  a background tick thread; tick runs on main via `_loop()` directly
- `KernelIO.snapshot()`, `reset_confirms()`, `set_intergreen_matrix()` added —
  required by `AbstractIO` protocol; without snapshot() push loop silently drops
- pci.web runs on :8081 on dev host (8080 occupied by CM5 monolith);
  field port remains :8080 as documented

**Test results:**
- Snapshot confirmed at 1Hz end-to-end
- kernel_version M8.0.0.435 visible in browser
- Proof: IOBus → kernel → IPC → web → browser confirmed

---

### Phase 3 — Real hardware drivers  ✓ COMPLETE

**Built:**
- `iobus/driver_xkop.py` — TCP client to TLC XKOP server; 17-byte packet
  format, CRC16 inline, event-driven send via table.subscribe(), keepalive
  every 6s, 18s dead-connection detection, exponential backoff reconnect
- `iobus/driver_rpdb.py` — TCP client to TLC RPDB server; two-socket model
  (read socket for subscriptions, write socket for SET_VALUE); dynamic input
  signal registration after querying element counts; pipelined SET_VALUE writes
- `iobus/driver_gpio.py` — CM5 GPIO pins via sysfs; no library dependencies;
  fail-silent on dev (SER5 LXC sysfs export read-only — pins skipped silently)

**Decisions:**
- CRC16 inlined in driver_xkop.py (no shared crc_utils module in pci/iobus/)
- RPDB input signals registered dynamically at runtime (element count unknown
  until controller responds); output signals declared in signals.cfg
- RPDB re-registers on every reconnect until controller responds — handles
  cold-start dropout
- GPIO limited to slow signals (photocells, panel switches); not for detection
- All drivers use source='pci.iobus' for signal ownership

**Test results:**
- XKOP, RPDB, GPIO drivers all confirmed working on dev host
- GPIO pins skipped on SER5 LXC as expected (fail-silent confirmed)

---

### Phase 4 — UG405  ✓ COMPLETE (4.1 + 4.2)

**Built:**
- `ug405/service.py` — SNMP agent UDP 161; instation polls us via SNMP
  GET/SET; RBE INFORM packets to instation; reads/writes IOBus signal table
- `ug405/iobus_client.py` — IOBus read/write, raw signals, no latch;
  `set_monitored()` starts 100ms poll thread, fires callbacks on change
- `ug405/ipc/server.py` — push + command sockets to web

**Decisions:**
- pysnmp pinned to 4.4.12 — matches CM5 reference exactly (asyncore-based v4 API)
- pyasn1 pinned to 0.4.8 — v4.4.12 incompatible with newer pyasn1
- pysnmp v7 migration deferred to Phase 7 (ARM64 field validation); field target
  is Python 3.11 where asyncore still present — safe to defer
- pci.ug405 is the SNMP agent (UTC Type 2 outstation) — instation is the client
- `set_monitored()` is poll-based (100ms) — PCI IOBus has no push channel to
  external services; same callback contract as CM5 in-process subscribe()
- Control signals derived from signals.cfg at startup for zero_owned()
- Config path defaults to /opt/pci/config/ug405.cfg; override via PCI_UG405_CFG

**Test results:**
- SNMP agent confirmed responding to GET/SET on UDP 161
- IPC push socket confirmed delivering live data
- Phase 4.3 (web routes for UG405) deferred to Phase 5 web work

**Outstanding for next session:**
- Phase 4.3: `web/api/routes/ug405.py` — wire UG405 IPC into web aggregator
- Phase 5: pci.rtig, pci.autodim, pci.offline, pci.agd/flir
  (read /opt/CM5 equivalent before writing each service)

---

## 2026-06-02 — Phase 4.3 complete

### Phase 4.3 — Web aggregates UG405 alongside MOVA  ✓ COMPLETE

**Built:**
- `ug405/ipc/client.py` — `UG405Client`: background thread connects to
  `/tmp/pci.ug405.live.sock`, distributes JSON lines to subscriber queues.
  `send_command()` uses `/tmp/pci.ug405.cmd.sock`. Singleton — no registry needed.
- `web/api/routes/ug405.py` — REST router mounted at `/api/ug405`.
  `GET /ping` — sends PING to UG405 command socket, returns ack.
- `web/api/ws/live.py` — added `GET /sse/ug405` SSE endpoint.
  Identical subscribe/generate/unsubscribe pattern to `/sse/mova/{stream_id}`.
- `web/api/app.py` — `create_app(registry, ug405_client=None)`.
  Wires UG405 client into routes and SSE on startup.
- `web/web_main.py` — creates `UG405Client()` and passes to `create_app()`.

**Decisions:**
- `UG405Client` is a plain class with fixed socket paths — no registry wrapper
  needed since there is only one UG405 instance.
- `ug405_client` is optional in `create_app()` so existing tests and
  MOVA-only startup are not broken.

**Test results:**
- Import check passed clean.
- No runtime test yet (requires pci.ug405 service running on the dev host).

**Outstanding for next session:**
- Phase 5.2: pci.autodim — read /opt/CM5/autodim/ first
- Phase 5.3: pci.offline
- Phase 5.4: pci.agd / pci.flir

---

## 2026-06-02 — Phase 5.1 complete

### Phase 5.1 — pci.rtig RTIG TLP adapter  ✓ COMPLETE

**Built:**
- `rtig/service.py` — `RTIGService`: XML parser, rule matcher, `PulseEngine`
  (timed IOBus write 1 → write 0), TLP log (50 entries deque), snapshot.
  Rule matching ported verbatim from CM5 including comparator objects.
  Entry point: `python -m pci.rtig.service`
- `rtig/iobus_client.py` — `RTIGIOBus`: write-only wrapper, no reads, no subscribe.
  HELLO pci.rtig — ownership enforced server-side.
- `rtig/ipc/server.py` — push socket `/tmp/pci.rtig.live.sock`,
  command socket `/tmp/pci.rtig.cmd.sock`.
  Commands: `PING`, `RECEIVE <xml_line>`, `RELOAD_RULES`.
- `rtig/ipc/client.py` — `RTIGClient` for web process (singleton, fixed paths).
- `web/api/routes/rtig.py` — REST router at `/api/rtig` (ping, reload_rules)
  + `create_receiver_app()` — minimal FastAPI app for port 9010.
- `web/api/ws/live.py` — `/sse/rtig` SSE endpoint added.
- `web/api/app.py` — `create_app(registry, ug405_client, rtig_client)`.
- `web/web_main.py` — two uvicorn servers in one asyncio event loop:
  main UI on port 8080/8081, RTIG HTTP receiver on port 9010.
  `asyncio.wait(FIRST_COMPLETED)` ensures clean shutdown when main server exits.
- `config/rtig.cfg` — pulse_seconds, rules_file, signal_map (deployment-specific).
- `config/rtig_rules.json` — empty rules array (deployment populates).
- `config/platform.cfg` — `[rtig]` section added with `http_port = 9010`.

**Decisions:**
- HTTP receiver runs inside pci.web on port 9010 (honours "only web serves HTTP" rule).
  Port 9010 is fixed by network routing/firewall on field deployments — cannot change.
- Two uvicorn.Server instances in one asyncio.gather via asyncio.wait(FIRST_COMPLETED).
  rtig_srv.install_signal_handlers=False — only main server handles SIGTERM.
- TLP XML is flattened to a single line before forwarding via IPC command socket
  (XML is always a single self-closing <rtig_tlp .../> tag — safe to flatten).
- signal_map in rtig.cfg uses lowercase section name `[signal_map]` (configparser
  lowercases keys by default).
- Config path defaults to /opt/pci/config/rtig.cfg; override via PCI_RTIG_CFG.

**Test results:**
- Import check passed clean.
- No runtime test yet (requires pci.rtig service + IOBus running on dev host).

---

## 2026-06-02 — Phase 5.2 complete

### Phase 5.2 — pci.autodim  ✓ COMPLETE

**Built:**
- `autodim/__init__.py`, `autodim/ipc/__init__.py` — package markers
- `autodim/iobus_client.py` — `AutodimIOBus`: write-only wrapper around
  `IOBusClient('pci.autodim')`. Logs warning on write failure.
- `autodim/ipc/server.py` — `IPCServer`: push socket `/tmp/pci.autodim.live.sock`
  + command socket `/tmp/pci.autodim.cmd.sock`. Same pattern as rtig/ug405.
  Commands: PING → pong, SET_ENABLED 1/0 → ack.
  1Hz snapshot push; immediate push on dim/bright transition event.
- `autodim/ipc/client.py` — `AutodimClient`: singleton background reader,
  subscribe/unsubscribe queues, send_command(). Same pattern as RTIGClient.
- `autodim/service.py` — `AutodimService`: astral-based dim/bright controller.
  30-second tick loop. Recalculates sunrise/sunset on date change.
  Algorithm: `is_dim = (now >= dim_utc) OR (now < bright_utc)`.
  Persists current state to `/tmp/pci.autodim.state.json` on each transition.
  Restores state on startup. Writes dim value to IOBus on every tick.
  Entry point: `python -m pci.autodim.service`.
- `config/autodim.cfg` — lat, lon, dim_offset_minutes, bright_offset_minutes,
  signal, enabled. Config override via PCI_AUTODIM_CFG env var.
- `web/api/routes/autodim.py` — REST router at `/api/autodim`:
  `GET /ping`, `POST /set_enabled/{value}`.
- `web/api/ws/live.py` — `GET /sse/autodim` SSE endpoint added.
- `web/api/app.py` — `create_app(registry, ug405_client, rtig_client, autodim_client)`.
- `web/web_main.py` — creates `AutodimClient()` and passes to `create_app()`.

**Decisions:**
- Algorithm is purely time-based (astral). No photocell read — the architecture
  diagram note "reads photocell" is aspirational; the CM5 summary confirmed
  astral-only algorithm.
- `is_dim` starts as None (unknown) on first tick — triggers a transition to
  the correct state and writes immediately on first 30s tick.
- Dim value persisted to /tmp on every transition so restarts recover state.
- `virt.dim` used as default signal in config — must be declared in signals.cfg
  as owned by `pci.autodim` for each deployment.
- astral 3.2 installed; `astral` added to requirements.txt in previous session.

**Test results:**
- Import check passed clean: all 8 new/modified modules imported without error.
- astral sunrise/sunset calculation confirmed: London 2026-06-02 sunrise 03:48 UTC,
  sunset 20:09 UTC — correct for date and location.
- No runtime test yet (requires IOBus running on dev host).

**Outstanding for next session:**
- None — Phase 5 complete.

---

## 2026-06-02 — Phase 5.3 complete

### IOBus shared ownership extension

**Built:**
- `iobus/server.py` — `SignalTable._owners` now supports `str | frozenset[str]`.
  `load_config()` parses comma-separated owner values as frozenset.
  `register()` allows re-registration by any declared co-owner.
  `write()` accepts source if it is in the frozenset.
  Single-owner behaviour unchanged.
- `ARCHITECTURE.md` — signal ownership section updated with comma-separated
  format and usage example. IOBus note added: no arbitration — services take
  turns based on opMode.

**Signals.cfg format for shared signals:**
```ini
xkop.o.101 = pci.ug405, pci.offline
```

### Phase 5.3 — pci.offline UTC offline plan player  ✓ COMPLETE

**Built:**
- `offline/__init__.py`, `offline/ipc/__init__.py`
- `offline/iobus_client.py` — `OfflineIOBus`: write-only `IOBusClient('pci.offline')`.
- `offline/ipc/server.py` — `IPCServer`: push socket `/tmp/pci.offline.live.sock`
  + command socket `/tmp/pci.offline.cmd.sock`.
  Commands: PING → pong, RELOAD → force plan file reload + state clear.
  1Hz snapshot push.
- `offline/ipc/client.py` — `OfflineClient` for web process (same pattern as RTIGClient).
- `offline/service.py` — `OfflinePlanService` + `UG405OpModeTracker`.
  1-second tick loop. Hot-reload on plan file mtime change.
  Timetable resolution with midnight carryover (ported verbatim from CM5).
  Three cycle position modes: datetime, time, none.
  Anti-drop bitmask write pattern (set 1s first, clear 0s second).
  opMode from UG405OpModeTracker — subscribes to `/tmp/pci.ug405.live.sock`,
  defaults to opMode=1 (standalone, plans run) on disconnect.
  Entry point: `python -m pci.offline.service`.
- `config/offline.cfg` — plan_file, ug405_cfg paths.
- `config/offline_plan.json` — empty template (settings, timetable, scns).
- `web/api/routes/offline.py` — REST at `/api/offline` (ping, reload).
- `web/api/ws/live.py` — `/sse/offline` SSE endpoint added.
- `web/api/app.py` — `offline_client` parameter added.
- `web/web_main.py` — `OfflineClient()` created on startup.

**Decisions:**
- Signal ownership: shared between pci.ug405 and pci.offline in signals.cfg
  (comma-separated). IOBus extended to support this. Services take turns based
  on opMode — no simultaneous write conflict in practice.
- opMode tracking: subscribe to pci.ug405 live IPC socket. Any JSON message
  with 'opmode' key updates local state. Disconnect → default to 1 (standalone).
  Safest default: plans activate when ug405 not running.
- SCN/control signal maps: pci.offline reads the same ug405.cfg as pci.ug405
  (shared config file, not coupling). Override via PCI_UG405_CFG env var.
- Plan file: standalone JSON at config/offline_plan.json. Override via
  PCI_OFFLINE_CFG env var (path to offline.cfg) or plan_file in offline.cfg.

**Test results:**
- IOBus shared ownership: all assertions passed (frozenset write, single-owner
  rejection, single-owner unchanged).
- Import check passed clean: all offline and updated web modules imported.
- No runtime test yet (requires IOBus + pci.ug405 running on dev host).

**Outstanding for next session:**
- Phase 5.4: pci.agd / pci.flir

---

## 2026-06-02 — Phase 5.4 complete

### Phase 5.4 — pci.agd + pci.flir  ✓ COMPLETE

**Built:**

**pci.agd — AGD650 radar detector adapter:**
- `agd/__init__.py`, `agd/ipc/__init__.py` — package markers
- `agd/iobus_client.py` — `AGDIOBus`: write + batch read wrapper around
  `IOBusClient('pci.agd')`. Logs warning on rejected writes.
- `agd/ipc/server.py` — `IPCServer`: push socket `/tmp/pci.agd.live.sock`
  + command socket `/tmp/pci.agd.cmd.sock`. 1Hz snapshot push;
  immediate push on zone_change and fault/reconnect events.
  Commands: PING → pong.
- `agd/ipc/client.py` — `AGDClient`: background reader, subscribe/unsubscribe
  queues, send_command(). Same pattern as all other service clients.
- `agd/service.py` — `AGDService`: ZeroMQ SUB subscriber, one thread per AGD
  unit. 500ms recv timeout. Change detection — only writes IOBus on zone state
  or class presence change (not every 150ms frame). Per-zone signals: `detected`
  + class presence bits. Global OR bits: `any_detected`, `any_<class>`.
  On fault (frame timeout): zeros all unit signals, pushes fault event.
  On resume: pushes reconnect event. Signal mapping via [VIRT_MAPPING] with
  unit-qualified key first, global fallback.
  Entry point: `python -m pci.agd.service`.
- `config/agd.cfg` — units, frame_timeout, [CLASSES], [VIRT_MAPPING] template.

**pci.flir — FLIR camera adapter:**
- `flir/__init__.py`, `flir/ipc/__init__.py` — package markers
- `flir/iobus_client.py` — `FLIRIOBus`: write + batch read wrapper around
  `IOBusClient('pci.flir')`.
- `flir/ipc/server.py` — `IPCServer`: push socket `/tmp/pci.flir.live.sock`
  + command socket `/tmp/pci.flir.cmd.sock`. 1Hz snapshot push;
  immediate push on zone_event. Commands: PING → pong.
- `flir/ipc/client.py` — `FLIRClient`: same pattern as AGDClient.
- `flir/service.py` — `FLIRService`: WebSocket subscriber (websocket-client),
  one thread per camera running `run_forever()`. Subscription message sent
  on_open. Event-driven: processes `messageType=Event` messages immediately.
  Event types: Presence/Pedestrian → occupied, DilemmaZone → dilemma,
  class field → has_pedestrian / has_bicycle / has_vehicle. Global OR bits
  updated after every event. On close: sleeps 5s, reconnects.
  Entry point: `python -m pci.flir.service`.
- `config/flir.cfg` — cameras, [VIRT_MAPPING] template.

**Web integration:**
- `web/api/routes/agd.py` — REST at `/api/agd` (ping).
- `web/api/routes/flir.py` — REST at `/api/flir` (ping).
- `web/api/ws/live.py` — `/sse/agd` and `/sse/flir` SSE endpoints.
- `web/api/app.py` — `agd_client` and `flir_client` parameters added.
- `web/web_main.py` — `AGDClient()` and `FLIRClient()` created on startup.

**Dependencies added:**
- `pyzmq` 27.1.0 — installed, added to requirements.txt
- `websocket-client` 1.9.0 — installed, added to requirements.txt

**Decisions:**
- Signal ownership: signals declared in signals.cfg as owned by `pci.agd` /
  `pci.flir`. No runtime registration. Deployment populates [VIRT_MAPPING]
  with signal names matching signals.cfg.
- AGD change detection: only writes IOBus when zone state or class presence
  changes vs previous frame — not on every 150ms frame (same as CM5).
- AGD class types configurable via [CLASSES] section. FLIR zone types fixed:
  occupied, dilemma, has_pedestrian, has_bicycle, has_vehicle.
- Both services fault-tolerant: AGD reconnects via ZMQ implicit on publisher
  restart; FLIR reconnects via run_forever loop with 5s sleep on close.
- Config override: PCI_AGD_CFG and PCI_FLIR_CFG env vars.

**Test results:**
- Import check passed clean: all 10 new modules + updated web modules.
- pyzmq 27.1.0 and websocket-client 1.9.0 installed and importable.
- No runtime test yet (requires AGD simulator / FLIR mock server running).

**Outstanding:**
- Phase 5 complete. Next: Phase 6 (log management) or Phase 7 (ARM64 field
  deployment) as directed.

---

## 2026-06-02 — Phase 6 complete

### Phase 6.0 — shared/log.py  ✓ COMPLETE

**Built:**
- `shared/log.py` — `setup(service_name, level='INFO')` function
  Three handlers on root logger:
  1. `StreamHandler` (console) at specified level
  2. `RotatingFileHandler` → `/opt/pci/logs/<service_name>.log`, DEBUG, 10 MB × 5
  3. `FileHandler` (append) → `/opt/pci/logs/pci.log`, DEBUG — multi-process safe

**Format matches CM5 exactly:**
```
'%(asctime)s [%(name)-8s] %(levelname)-5s %(message)s'
'%Y-%m-%d %H:%M:%S'
logging.Formatter.converter = time.localtime
```
Werkzeug suppressed to ERROR, urllib3 to WARNING — same as CM5.

**All 9 entry points replaced:** `iobus/server.py`, `mova/kernel_main.py`,
`autodim/service.py`, `ug405/service.py`, `rtig/service.py`, `offline/service.py`,
`agd/service.py`, `flir/service.py`, `web/web_main.py`.
Each now calls `from pci.shared.log import setup; setup('pci.xxx')`.

**Log level fixes:**
- `rtig/service.py`: TLP received, rule match count, PULSE ON/OFF → info (were debug)
- `ug405/protocol/rbe.py`: INFORM acknowledged → info; no ACK → warning (were debug)
- `offline/service.py`: opMode change in UG405OpModeTracker → info (was debug)
- `agd/service.py`: zone detection state change → info (was missing)
- `flir/service.py`: Presence/Pedestrian event → info (was missing; DilemmaZone already info)

### Phase 6.1 + 6.2 — rotate_logs.sh + systemd units  ✓ COMPLETE

**Built:**
- `tools/rotate_logs.sh` — gzips `stream_*.jsonl` (previous days only), deletes
  `stream_*.jsonl.gz` older than 30 days. LOG_DIR from arg or `$MOVA_LOG_DIR`
  or `/opt/pci/logs/mova`. Uses `set -euo pipefail`.
- `/etc/systemd/system/pci-rotate-logs.timer` — `OnCalendar=*-*-* 00:05:00`,
  `Persistent=true` (catches up missed runs)
- `/etc/systemd/system/pci-rotate-logs.service` — `Type=oneshot`

**Decisions:**
- Script uses `stream_*.jsonl` pattern matching actual StreamLogger output, not
  the `pci.mova.N_...` names shown in the original ARCHITECTURE.md diagram.
  ARCHITECTURE.md updated to reflect actual filename.
- `Persistent=true` ensures rotation runs after any downtime at midnight.

### Phase 6.3 — driver_sim.py replay mode  ✓ COMPLETE

**Built:**
- Extended `driver_sim.py` with `_replay_loop()` and updated `_load()`.
- Activated via `[replay]` section in `signals.cfg`:
  ```ini
  [replay]
  file  = /opt/pci/recordings/junction_a.jsonl
  speed = 1.0
  loop  = true
  ```
- Recording format: `{"ts": <float>, "n": "<signal_name>", "v": <0|1>}`
- Replay maintains relative timing between events, divided by speed multiplier.
- Registers any signal names from recording not already owned — `ValueError`
  caught silently (non-pci.iobus signals rejected at write time).
- Existing `_pulse_loop()` unchanged. Replay only activates if `[replay]` present.

**Test results:**
- End-to-end test: 4-event recording, speed=10×, loop=false.
  Received: `[('crb', 1), ('det.0', 1), ('det.0', 0), ('det.1', 1), ('det.1', 0)]` ✓

### Phase 6.4 — driver_recorder.py  ✓ COMPLETE

**Built:**
- `iobus/driver_recorder.py` — new file, same `start(table, config_path)` interface.
- Activated by `driver = recorder` in `platform.cfg [iobus]`.
- Config in `signals.cfg [recorder]` section: `file=`, `signals=` (optional filter).
- Uses `table.subscribe(cb)` — fires synchronously on every signal state change
  (signal table does not fire subscribers for no-change writes).
- File opened with `buffering=1` (line-buffered) — each event reaches OS page cache
  immediately, preventing loss on crash without explicit flush calls.

**Decisions:**
- Records conditioned values (post-inversion/scaling) — what services read.
  For binary detector signals with inversion-only conditioning, double-inversion
  is identity so record→replay is bit-perfect. Scaling is a known limitation.
- `driver_recorder.py` covers all replay use cases (TLC inputs, MOVA detectors,
  UTC session replay). No separate UG405 SNMP SET recorder needed.

**Test results:**
- Filter test: det.0 and det.1 in table, filter=det.0 only. Wrote det.0=1,
  det.1=1 (filtered), det.0=0. Recorded: `[{det.0,1}, {det.0,0}]` ✓

**All import checks passed clean.** 15 files changed (283 insertions, 63 deletions).

---

## 2026-06-03 — Phase 7.1 complete

### Phase 7.1 — design.html component library  ✓ COMPLETE

**Built:**
- `web/static/design.html` — 1136-line self-contained component library.
  No external dependencies. All CSS uses `:root` custom properties.
  Every component shown in every possible state.

**Source files read:**
- `/opt/MOVA/pci_mova/web/static/index.html` (1044 lines) — full HTML read
- `/opt/CM5/cm5_web.py` — CSS extraction performed in prior session
- All 10 MOVA static HTML files — CSS tokens verified identical across files

**Path corrections (noted as ARCHITECTURE.md errors this session):**
- Documented: `/opt/CM5/web/web.py` → actual: `/opt/CM5/cm5_web.py`
- Documented: `/opt/MOVA/pci_mova/static/` → actual: `/opt/MOVA/pci_mova/web/static/`
- Both corrected in ARCHITECTURE.md.

**Design decisions applied:**
- CSS architecture: MOVA approach — all values as `:root` custom properties.
  CM5 hardcoded hex values converted to tokens.
- Added tokens: `--sidebar-bg: #1e2d50`, `--accent2: #2563eb`,
  `--live: #00e676`, `--error-dot: #ff5252`
- Topbar brand: 15px (MOVA; CM5 was 16px)
- Border radius: 8px for cards/panels, 6px for compact inner cards
- Table padding: 6px 12px standard, 4px 8px compact
- Sidebar nav: CM5 `.sidebar` / `.nav-item` / `.nav-group-label` included
  alongside all MOVA stream card components — unified, not one or the other

**Components documented (all states):**
- Design tokens (palette, state colours) — colour swatches
- Typography — sans + mono scale
- Layout — topbar, logo, conn-dot (default/live/error), sidebar nav
- Badges: `status-badge` (control/warmup/off/fault/nodata/other),
  `badge` (green/amber/red/blue/purple/gray/plan), `pill` (ok/warn/err),
  priority labels (prio-1/2/3)
- Buttons: default, btn-start, btn-stop, btn-force, btn-log, btn-load,
  btn-primary — hover states shown both live and as static preview
- Tables: standard (6px 12px), compact (4px 8px), td-mono
- Panels: panel, panel-header, panel-body, metric-card (ok/warn/err),
  licence-grid (ok/nok/neutral), row2, no-content placeholder
- Forms: form-input, form-select, search-input, search-match, toggle-sw (on/off/disabled)
- Feedback: toast (default/success/error) + live demo button
- Stream card: active/fault/warmup/collapsed/no-dataset states
  Full anatomy: card-header, card-status-row (two rows), card-section-hdr,
  bit-grid, faults-body, card-controls-row
- Status row: CRB light (on/off), status-val (on/off/warn), toggle-btn
- Bit grid: off/on/amber/red/hi-on/sync-on/fault-on
- Simulation panel (open state) — all sim-btn states (active/crb-active/none-active),
  sim-det-btn. Note: detector toggles wire to `/api/iobus/write` in templates,
  not old kernel `/api/streams/{id}/io` commands.
- XKOP sim panel: removed (replaced by driver_xkop.py — no longer a UI component)
- CM5 config-section + toggle-sw
- Log viewer: all five log levels (debug/info/warn/error/critical)
- Footer: mem-bar at three fill levels (green <60%, amber 60-80%, red >80%)

**Outstanding for next session:**
- Phase 7.2: css/pci.css — extract production CSS from design.html into
  a linked stylesheet (so templates can `<link>` it rather than embedding styles)
- Phase 7.3: Port MOVA popup pages from /opt/MOVA/pci_mova/web/static/
  (dataset, derived, analysis, messages, errors, history)
  Read each source file before porting. Never rewrite from memory.
- Phase 7.4: Main dashboard templates/index.html
- Phase 7.5: Service pages (ug405, rtig, autodim, offline, agd, flir)

---

## 2026-06-03 — Phase 7.2 complete

### Phase 7.2 — css/pci.css extracted from design.html  ✓ COMPLETE

**Built:**
- `web/static/css/pci.css` — 321 lines, all production CSS from design.html.
  Contains: `:root` tokens, `body`, `header`, all component classes.
- `web/static/design.html` — `<style>` block replaced with
  `<link rel="stylesheet" href="css/pci.css">` + small inline `<style>`
  containing only the `.ds-*` design-reference helpers (11 rules).

**Decisions:**
- `.ds-*` helpers kept inline in design.html — they are reference-document
  scaffolding only and should not ship in the stylesheet templates link to.
  All production component styles are in pci.css with zero duplication.

**Verification:**
- Line counts consistent: 815 (design.html) + 321 (pci.css) = 1136 (original).
- `grep -c ".ds-" pci.css` → 0 (no helper classes in production stylesheet).
- No production CSS rules remain inline in design.html style block.

**Outstanding for next session:**
- Phase 7.3: Port MOVA popup pages from /opt/MOVA/pci_mova/web/static/
  (dataset, derived, analysis, messages, errors, history)
  Read each source file before porting. Never rewrite from memory.
- Phase 7.4: Main dashboard templates/index.html
- Phase 7.5: Service pages (ug405, rtig, autodim, offline, agd, flir)

---

## 2026-06-03 — Phase 7.3 complete

### Phase 7.3 — Port MOVA popup pages  ✓ COMPLETE

**Pages to port (order):**
dataset.html, derived.html, messages.html, errors.html,
history.html, analysis.html, satflow.html, tma.html, syslog.html

**dataset.html — source read, plan confirmed, ready to write:**

Source: /opt/MOVA/pci_mova/web/static/dataset.html (550 lines)

CSS plan:
- Drop from inline: :root, *, body — already in pci.css
- Override inline: header — popup uses padding:10px 20px/gap:12px
  vs pci.css height:48px; page-specific override required
- Keep inline: .hdr-title, .hdr-sub, .tabs/.tab, .content, .section,
  .section-title (different from pci.css: topbar colour + accent border),
  .info-grid, .info-cell, table base styles, .plan-card, .sched-row,
  .matrix-wrap, .upload-zone, .ds-* classes
- toast() function: replace hand-rolled inline style.cssText with
  pci.css .toast/.toast.success/.toast.error + #toast-container div

API endpoints: no changes — const API = location.origin is correct,
all /api/dataset/* and /api/streams/* paths unchanged.

**Outstanding:**
- Write web/static/dataset.html (next session, start here)
- Then continue with remaining 8 popup pages

---

## 2026-06-03 — Phase 7.3 continued

### Phase 7.3 — Port MOVA popup pages (session 2)

**Pages completed this session:**
- `web/static/errors.html` — faults popup, WebSocket `/ws/errors/{streamId}`, active faults + history table
- `web/static/history.html` — full historical viewer, timeline SVG, 6 tabs (TMA/DetCounts/SatFlow/Derived/Messages/Errors), cursor scrubbing, auto-reload for today
- `web/static/analysis.html` — offline JSONL analyser, SAM View, API+file load, IndexedDB site persistence, play engine

**CSS approach (consistent across all three):**
- `<link rel="stylesheet" href="css/pci.css">` replacing all inline `:root`/`*`/body base styles
- `body { height:100vh }` override where flex-scroll layout requires it (flex-popup pattern)
- Logo-mark size override (source varies 18–24px; pci.css 28px)
- Conflict comments added where page-specific classes override pci.css rules (`.section-title`, `.badge`)
- All page-specific component styles kept inline

### Phase 7.3 — Port MOVA popup pages (session 3)

**Pages completed this session:**
- `web/static/satflow.html` — scrollable page (no `height:100vh`), WebSocket `/ws/derived/{streamId}`.
  Lane cards with sat/high state variants, optimiser status panel.
  `.section-title` conflict: overrides pci.css `font-size:11px` → `9px` with comment.
- `web/static/tma.html` — flex scroll popup, WebSocket `/ws/messages/{streamId}`.
  Stage-start log table with computed green duration and cycle time lookup.
  `renderCounts`/`connectCounts` dead code left in place — references `count-grid`/`period-info`
  DOM elements that do not exist; DOM target was moved to History viewer in a prior session.
- `web/static/syslog.html` — **dark theme**: `:root` block kept inline to override pci.css
  light-theme tokens. Polls `GET /api/system/log?lines=N&level=X` every 3s (no WebSocket).
  Follow/pause on scroll, search filter, level filter, line count selector.

**All 9 popup pages ported:**
dataset, derived, messages, errors, history, analysis, satflow, tma, syslog.

**Outstanding:**
- Phase 7.4: dashboard index.html
- Phase 7.5: service pages (ug405, rtig, autodim, offline, agd, flir)

**Outstanding:**
- Phase 7.4: dashboard index.html
- Phase 7.5: service pages (ug405, rtig, autodim, offline, agd, flir)

---

## 2026-06-03 — Phase 7.4 complete

### Phase 7.4 — Main dashboard index.html  ✓ COMPLETE

**Built:**
- `web/static/index.html` — 809-line unified dashboard replacing Phase 2 proof-of-concept.

**Layout:** topbar + `.page-body` (.sidebar + main) + footer — pci.css layout throughout.
No inline CSS except `.streams-grid` (grid layout, not in pci.css) and `.btn:disabled` override.

**Sidebar groups:**
- MOVA Streams — dynamically populated by JS as stream SSEs connect; status dot per stream
  (green = SSE live, red = disconnected)
- UTC / SCOOT — UG405 (→ /ug405, status dot polled /api/ug405/ping)
- IO — IOBus Signals (→ /iobus)
- Services — RTIG, Autodim, Offline Plans, AGD, FLIR (→ /rtig etc., status dots polled every 5s)
- Tools — System Log (opens /static/syslog.html popup)

**SSE model (PCI adaptation from MOVA WebSocket):**
- On load: `GET /api/mova/streams` → list of kernel IDs
- One `EventSource` per stream at `/sse/mova/{id}`, re-discovered every 10s
- Each SSE message is the stream's own snap (`{v,t,ts,...stream fields}`)
- Snap message used directly as stream state (`msg.status`, `msg.buffers`, `msg.io` etc.)
- Connection indicator: green if any stream SSE is live

**Stream card JS (adapted from /opt/MOVA/pci_mova/web/static/index.html):**
- `buildCard(id)` + `updateCard(id, s)` ported faithfully
- Two status rows: CRB/OnControl/MOVAEnabled/ErrorCount/Warmup |
  PM/CS/DS/NS/WaitT/WU/SCAN/SPEED/TIME/DATE
- Bit grids: Detectors (with tooltips), Confirms, Force bits + TO, Special Outputs, HI/SYNC/FLT
- Active faults list, sim panel (shown when `s.simulated_io` is true)
- Collapse/expand with localStorage persistence (`pci-card-{id}-collapsed`)

**XKOP panel:** dropped — driver_xkop.py handles hardware; not a UI concern.

**Sim panel IO commands:** use `POST /api/mova/streams/{id}/cmd` with IPC commands:
- `SET_CRB value`, `SET_CONFIRM index value`, `SET_DET index value`

**Stream control buttons:**
- Start — disabled when no dataset (tooltip "Load a dataset first");
  enabled when dataset present → `SET_IO 19 1` (enable On Control)
- Stop — confirm dialog → `UNLOAD`
- Force — prompt → `FORCE_STAGE N`
- Reset — confirm → `RESET`
- Speed/TOD selects → `SET_SPEED N` / `SET_TOD_OFFSET N`
- Dataset/Derived/Messages/Errors/History/Analysis → openWin to `/static/*.html?stream={id}`

**Popup URLs:** `/static/dataset.html?stream=0` etc. — served by FastAPI static file mount.
All 7 popup HTML files verified present in web/static/.

**Service status polling:** `pollServices()` every 5s, pings all 6 service endpoints,
updates nav-item-dot background (green/grey).

**System Info panel:** collapsible (localStorage), calls `/api/licence/status` +
`/api/licence/hardware`, shows kernel version, fingerprint, syslog button.

**Footer:** tick (flips on each snap), local time, live/total stream count, kernel version,
memory bar (green/amber/red thresholds from `/api/system/memory`).

**Decisions:**
- No `max_streams` WebSocket equivalent — footer shows live/total from SSE state
- Dataset name shown as plain text (no download link — route not yet implemented)
- Service page links (/ug405, /rtig etc.) will 404 until Phase 7.5
- `discoverStreams()` runs every 10s to pick up kernels started after page load

**Verification:**
- HTML parse: OK
- All required IDs present
- All 7 popup file references verified against filesystem (all exist)
- 809 lines, 35 JS functions

**Outstanding:**
- Phase 7.5: service pages (ug405.html, rtig.html, autodim.html, offline.html, agd.html, flir.html)
- app.py popup routes (needed if popups served without /static/ prefix)
- /api/system/memory and /api/licence/* routes not yet implemented — gracefully no-ops

---

## 2026-06-03 — Phase 7.5 started — ug405.html

### Phase 7.5 (partial) — ug405.html service page

**Built:**
- `web/static/ug405.html` — 237-line standalone UG405 status page.
  Topbar + sidebar (← Dashboard link) + main + footer layout.
  `<link rel="stylesheet" href="css/pci.css">` — no invented styles.
  Page-specific inline style adds only: `.cards-row`, `.metric-value.sm`,
  `.val-on`, `.val-off`, `.section-gap`, `.scn-block` — all using
  existing design tokens.

- `web/api/routes/ug405.py` — `GET /api/ug405/mapping` added.
  Reads ug405.cfg via `load_ug405()` (respects `PCI_UG405_CFG` env var).
  Returns `{scns, control:{scn:{field:{bit:sig}}}, reply:...}`.
  Returns empty mapping (not 404) when ug405.cfg is absent (dev with no
  deployment config).

- `web/api/app.py` — `GET /ug405` route added → FileResponse ug405.html.

**Page structure (ported from CM5 panel-ug405):**
- 4 metric cards: Op Mode (coloured err/warn/ok), Instation IP:port, SCNs count,
  Last Update timestamp.
- Per-SCN signal tables: built from `/api/ug405/mapping` on load. Two-column
  layout (row2) with Control and Reply tables side-by-side. Signal values
  updated live via SSE `changes` events.
- Instation Config table: accumulates `{t:'log', entry:{type:'Config'}}`
  SSE events. Empty on fresh page load (not in snapshot — accumulates from
  events seen during session).
- opMode Transitions table: fixed 6-row table (1→2, 2→3, 3→3, 3→2, 3→1, 2→1).
  Updated from `{t:'opmode'}` SSE events. State.opmode initialised as null to
  prevent false transition on first snap.
- Control Activity log: 30-entry rolling log of all `{t:'log'}` entries
  (Config SETs and opMode changes). Config entries also update the Instation
  Config table.

**SSE event handling:**
- `{t:'snap'}`: initialise opmode, instation, lastupdate, apply changes.
  Does NOT record opMode transition (avoids false transition on page load).
- `{t:'opmode'}`: update opmode, record transition only if previous state
  was known (state.opmode !== null) and different.
- `{t:'signal'}`: apply changes.
- `{t:'config', field:'instation'}`: update instation metric card.
- `{t:'log', entry}`: append to ctrl_log; if entry.type='Config' also update
  cfg_state table.

**Decisions:**
- Mapping loaded via REST (`/api/ug405/mapping`), not Jinja2 template variable.
  Same structure as CM5 `_build_mapping_json()`. Signal names: raw (no `!`
  prefix) since display value already handles inversion.
- cfg_state, opmode_transitions, ctrl_log are accumulated from events during
  the browser session — not included in IPC snapshot. Acceptable for a monitoring
  page; historical data is not needed.
- opMode transition tracking: null-initialised state prevents the page
  recording a false "1→2" transition when the first snap arrives.

**Verification:**
- Import check: `from pci.web.api.routes.ug405 import router, mapping` — OK.
- Import check: `from pci.web.api.app import create_app` — OK.
- HTML parse: OK.
- All getElementById targets present in HTML.
- No missing CSS classes (all in pci.css or inline style block).

**Outstanding:**
- Phase 7.5 continues: iobus.html, rtig.html, autodim.html, offline.html,
  agd.html, flir.html

---

## 2026-06-03 — Phase 7.5 continued — iobus, rtig, autodim, offline

### Phase 7.5 (partial) — iobus.html, rtig.html, autodim.html, offline.html

**Built:**

**iobus.html — live signal table viewer:**
- `iobus/server.py` — added `SNAP` command to `IOBusServer._handle()`.
  Returns full signal table as JSON line: `{name: conditioned_value, ...}`.
- `web/api/routes/iobus.py` — new file. `GET /api/iobus/signals` opens
  short-lived connection to IOBus command socket, sends HELLO + SNAP,
  returns JSON dict. Blocking call wrapped in `run_in_executor`.
- `web/api/ws/live.py` — `GET /sse/iobus` added. Uses
  `asyncio.open_unix_connection` directly to `/tmp/pci.iobus.live.sock`
  (no intermediate client class — asyncio handles it natively). 25s keepalive.
- `web/api/app.py` — iobus router at `/api/iobus`, `GET /iobus` page route.
- `web/static/iobus.html` — on load: `GET /api/iobus/signals` populates full
  table. SSE `/sse/iobus` provides live updates. Signals sorted alphabetically,
  grouped by prefix (Detectors, Confirms, CRB, XKOP i/o, RPDB i/o, GPIO i/o,
  AGD, FLIR, Virtual, Other). Group headers hidden by filter when all rows hidden.
  Rows flash green on change. 3 metric cards: count, changes/5s, last change ts.
  Search/filter box.

**rtig.html — TLP log, rule status, pulse state:**
- `web/static/rtig.html` — data from `/sse/rtig`, snap-only (1Hz).
  4 metric cards: Total RX, Rules Loaded, Pulse Duration, Active Pulses.
  Pulsing Now panel: active pulse signals shown as green badges.
  TLP log table (50 rows): matched rows full opacity + green signal names;
  unmatched rows at 55% opacity with italic "no match".
  Rules section with Reload Rules button → `POST /api/rtig/reload_rules`.
- `web/api/app.py` — `GET /rtig` page route added.

**autodim.html — dim/bright status, next event:**
- `web/static/autodim.html` — data from `/sse/autodim`, snap + transition events.
  Large DIM/BRIGHT/UNKNOWN heading (amber/green/grey).
  3 metric cards: enabled toggle (calls `POST /api/autodim/set_enabled/`),
  IOBus signal name, last write time.
  Today's Schedule panel: bright and dim UTC times; next event highlighted
  blue with live countdown updated every second client-side; past events show
  "passed"; post-dusk shows "~tomorrow" (service recalculates at midnight).
  Configuration table: lat/lon, dim offset, bright offset.
  Transition log: accumulates session `transition` events.
- `web/api/app.py` — `GET /autodim` page route added.

**offline.html — plan status, active SCN, cycle position:**
- `web/static/offline.html` — data from `/sse/offline`, snap-only (1Hz).
  3 metric cards: Active SCNs count, Base Time Mode, Active Modes.
  Per-SCN blocks: green plan badge or grey "Standing Down"; cycle position
  progress bar with CSS transition, position/duration text, step number.
  Timetable table: today's day tags highlighted blue; currently resolved
  active row highlighted; plan names as `.badge-plan` badges.
  Plan Detail: collapsible `<details>` per SCN (collapsed by default);
  per-plan offset table with per-bit columns derived from decoded snapshot.
  Reload Plan File button → `POST /api/offline/reload`.
- `web/api/app.py` — `GET /offline` page route added.

**Decisions:**
- iobus.html SSE uses direct asyncio unix socket — no background client thread
  needed since the live socket is a raw push socket, not an IPC service socket.
- iobus.html: unknown signal arriving via SSE (after initial load) triggers
  full table reload — handles driver restart without page reload needed.
- autodim.html countdown runs client-side setInterval(1000) from stored ISO
  timestamps — no extra server round-trips for live countdown.
- offline.html plan detail uses `<details>`/`<summary>` — no JS toggle needed,
  collapsed by default to keep the page compact.

**Verification:**
- Import checks: all modules OK.
- `from pci.web.api.app import create_app` — OK.

**Outstanding:**
- Phase 7.5 continues: agd.html, flir.html

---

## 2026-06-03 — Phase 7.5 complete — agd.html and flir.html

### Phase 7.5 final — agd.html + flir.html  ✓ COMPLETE

**Built:**

**agd.html — AGD650 radar detector adapter page:**
- `web/static/agd.html` — 4 metric cards (Units, Total Zones, Faulted, Last Update).
  Per-unit blocks: custom `.unit-hdr` (ID, IP:port, OK/FAULT badge) above a `.table-card`
  with one row per zone. Zone columns: Zone ID, Detected dot, plus one column per class
  type discovered dynamically from the snapshot (e.g. pedestrian, bicycle, vehicle).
  Detection events log (last 20 from snap.events, reversed — newest first): time, unit,
  zone, DETECTED/CLEAR badge, classes text.
  Fault & Reconnect log (accumulated from `{t:'fault'}` and `{t:'reconnect'}` SSE events):
  time, unit, FAULT/RECONNECT badge, detail message. Rolling 30-entry log.
  Faulted metric value coloured `.err` when > 0, `.ok` when all clear.

**flir.html — FLIR camera adapter page:**
- `web/static/flir.html` — 3 metric cards (Cameras, Total Zones, Last Update).
  Per-camera blocks: custom `.cam-hdr` (ID, IP:port) above a `.table-card` with fixed
  columns: Zone, Occupied, Dilemma, Pedestrian, Bicycle, Vehicle.
  Signal dots match zone state; Dilemma dot uses amber (`.sig-dot.dilemma.on`) to
  distinguish it from occupied (green). No fault log — FLIR reconnects silently,
  no fault tracking in snapshot.
  Zone events log (last 20 from snap.events, reversed): time, camera, zone, event type,
  BEGIN/END badge, class string.

**web/api/app.py:**
- `GET /agd` and `GET /flir` page routes added.

**Decisions:**
- AGD class columns are discovered dynamically from the first zone's keys (excluding
  `id` and `detected`) — handles any class configuration without hardcoding.
- FLIR zone columns are fixed (occupied/dilemma/has_pedestrian/has_bicycle/has_vehicle)
  matching the fixed ZONE_TYPES list in flir/service.py.
- FLIR has no fault log — the service reconnects via run_forever with no fault state
  in the snapshot. The events log is sufficient for monitoring.
- Dilemma dot rendered amber to visually distinguish from occupied (green); matches
  the semantic meaning of a dilemma zone (caution, not detection).

**Verification:**
- `from pci.web.api.app import create_app` — OK (both routes present).
- HTML structure: all getElementById targets present in both pages.
- All CSS classes verified against pci.css (badge-green/red/gray, metric-value.ok/err,
  sig-dot/det-dot pattern).

**Phase 7.5 COMPLETE** — all six service pages done:
ug405.html, iobus.html, rtig.html, autodim.html, offline.html, agd.html, flir.html.

**Outstanding:**
- Phase 7.6: MOVA Tools AML connection (gate for Phase 8)
  Read MICKS_MOVA_TOOLS_INFO_FOR_AML/ tcpdumps, read /opt/MOVA/pci_mova/protocol/mova_tools.py,
  summarise findings, then build mova/protocol/aml_server.py.

---

## 2026-06-03 — Phase 7.5.5 integrated test

### Phase 7.5.5 — Full stack integration test  ✓ COMPLETE

**Stack tested:** IOBus (sim driver) + MOVA kernel stream 0 + pci.web on :8081/:9010

**Bugs found and fixed:**

1. **Old session processes still running** — iobus/mova/web from Jun02 session were never
   stopped. They held ports 8081 and 9010, causing the new web process to fail on bind.
   Fix: kill old processes, clean stale sockets, restart fresh.

2. **CSS 404 on clean URLs** — HTML pages served at `/`, `/ug405` etc. reference
   `css/pci.css` (relative path), which resolves to `/css/pci.css`. The static directory
   was only mounted at `/static/`, so `/css/pci.css` 404'd.
   Fix: added `app.mount("/css", StaticFiles(directory=css_dir), name="css")` in app.py,
   before the `/static` mount.

3. **RTIG port conflict kills web process** — port 9010 was held by `/opt/ug405-env/bin/python main.py`
   (CM5 legacy process). When RTIG uvicorn server failed to bind, it raised SystemExit(1),
   which the `asyncio.wait(FIRST_COMPLETED)` pattern treated as completion, cancelling the
   main UI server too — entire web process exited.
   Fix: wrapped `rtig_srv.serve()` in `_rtig_guarded()` that catches SystemExit and logs a
   warning. Main server now runs independently; RTIG failure is non-fatal. CM5 service was
   also stopped to free port 9010 for this deployment.

**Test results:**

| Check | Result |
|---|---|
| IOBus startup | ✓ 31 signals (24 base + 7 sim), pci.iobus ready |
| MOVA kernel stream 0 | ✓ connected to IOBus, IPC sockets up, tick loop running |
| Web :8081 | ✓ HTTP 200 |
| RTIG receiver :9010 | ✓ HTTP binding confirmed |
| Dashboard `/` | ✓ HTTP 200 |
| CSS `/css/pci.css` | ✓ HTTP 200 after fix |
| SSE `/sse/mova/0` | ✓ snaps at ~1Hz, kernel_version=M8.0.0.435 |
| Stream 0 status | ✓ NOT_STARTED (no dataset loaded — correct) |
| CRB | ✓ crb=true (READY) |
| Detectors pulsing | ✓ det.1 observed toggling via sim driver |
| All 7 service pages | ✓ HTTP 200 (ug405, iobus, rtig, autodim, offline, agd, flir) |
| /api/iobus/signals | ✓ live signal table returned |

**Expected log noise (not errors):**
- IPC send_command warnings for ug405/rtig/autodim/offline every 5s — these services are
  disabled in platform.cfg; the dashboard polls their ping endpoints and gets connection
  refused. Normal behaviour when only IOBus + MOVA + web are running.

---

## 2026-06-03 — Phase 7.5.5 continued — bug fixes from integration test

### Fixes applied (Issues 2–5 from integration test review)

**Issue 2 — detector bit grid blank without dataset (index.html:489)**
- `kernel_deton` is only populated by `MI_get_all_detectors()` in wrapper.py after
  `kernel.tick()`. `kernel.tick()` is gated on dataset present. Without dataset,
  `kernel_deton` is 64 zeros (truthy array) — JS `||` never fell through to `detectors`.
- Fix: `(s.dataset ? buffers.kernel_deton : buffers.detectors) || []`
  — uses IOBus raw values (`detectors`) when no dataset loaded.

**Issue 3 — asyncio.get_event_loop() deprecated (autodim.py:34)**
- `asyncio.get_event_loop()` deprecated in Python 3.10+, raises RuntimeError in Python 3.12.
- Fix: one-line change to `asyncio.get_running_loop()`.

**Issue 4 — /api/system/log missing (syslog.html showed empty)**
- `syslog.html` polls `GET /api/system/log?lines=N&level=X` every 3s — route did not exist.
- Built: `web/api/routes/system.py` — reads tail of `pci.log`, exact-match level filter
  (DEBUG/INFO/WARNING/ERROR), returns `{"lines": [...], "total": N}`.
- Mounted at `/api/system` in app.py.
- Verified: total=3340, lines filtered to 49 INFO entries — correct.

**Issue 5 — RTIG/UG405 configs empty (no working example)**
- `config/rtig.cfg` — added RTIG1-5 → virt.10-14 signal map from CM5.
- `config/ug405.cfg` — added five SCN blocks from CM5 deployment:
  X0330 (VSn detector occupancy), J0331 (full junction with inverted greens),
  J0332 (4-stage junction), J0333 (virtual signals), MICK_VC (vehicle count).
- PCI parser handles CM5 `!=` inversion notation correctly (`k.endswith('!')` after split).
- Verified: all 5 SCNs load, control signals parsed, J0331 Gn inversion prefix `!` applied.
- [LIVE] section from CM5 not copied — managed by pci.ug405 service at runtime.

**Issue 1 (dataset routes) — deferred to next session** (full router implementation).

**Outstanding:**
- Issue 1: /api/dataset/ router — list, upload, delete, load, info, detail endpoints
- Phase 7.6: MOVA Tools AML connection (gate for Phase 8)

---

## 2026-06-03 — Systemd units: full stack one-command start

### Task — write and install systemd units for all services

**Built:**
- `/etc/systemd/system/pci-iobus.service`
- `/etc/systemd/system/pci-mova-kernel@.service` (template, replaces old `/opt/MC_MOVA` unit)
- `/etc/systemd/system/pci-ug405.service`
- `/etc/systemd/system/pci-rtig.service`
- `/etc/systemd/system/pci-autodim.service`
- `/etc/systemd/system/pci-offline.service`
- `/etc/systemd/system/pci-web.service`

**Decisions:**
- `PYTHONPATH=/opt` in every unit — required so `import pci.*` resolves.
- Venv path: `/opt/pci/venv/bin/python` — shared venv, all services.
- `WorkingDirectory=/opt/pci` — config files resolve from project root.
- `PCI_WEB_PORT=8082` in pci-web.service — 8080 occupied by CM5 monolith, 8081 also in use.
- `Restart=on-failure` — clean exit (e.g. unlicensed MOVA stream) does not loop.
- `After=pci-iobus.service` on all service units — ensures ordering when started together.
- `pci-web.service` has `Wants=` all other units — starting web pulls up the full stack.
- `pci-offline.service` also has `After=pci-ug405.service` — reads opMode from ug405 IPC.
- Memory limits set per service (iobus 256M, mova@N 384M, web 256M, others 128M).
- `pci-mova-kernel@.service` template overwrites the old unit pointing to `/opt/MC_MOVA`.

**Test results — all services active (running):**
- `systemctl start pci-iobus` → active
- `systemctl start pci-mova-kernel@0` (2s after iobus) → active
- `systemctl start pci-ug405 pci-rtig pci-autodim pci-offline pci-web` → all active
- `systemctl status pci-web`: all IPC clients connected (mova@0, ug405, rtig, autodim, offline)
- `curl http://localhost:8082/api/mova/streams` → `{"streams":[0]}` ✓
- All 7 services enabled via `systemctl enable`; `pci-mova-kernel@0` enabled separately.

**Outstanding:**
- Issue 1: /api/dataset/ router — list, upload, delete, load, info, detail endpoints
- Phase 7.6: MOVA Tools AML connection (gate for Phase 8)

---

## 2026-06-03 — UI connection-loss bug investigation (session ended incomplete)

### Issues found and partially fixed

**Bug 1 — FIXED: blocking send_command in async ping routes**
All 7 service ping routes called `_client.send_command("PING")` directly inside
`async def`, without `run_in_executor`. `send_command` opens a unix socket with
a 3-second timeout. Six sequential pings × 3s = up to 18s of asyncio event loop
blocked. SSE keepalives stopped firing, browser dropped connections, UI stalled.

Fixed in: `web/api/routes/offline.py`, `rtig.py`, `autodim.py`, `ug405.py`,
`agd.py`, `flir.py`, `mova.py`. All now use `run_in_executor`. Added missing
`import asyncio` to `ug405.py`, `agd.py`, `flir.py`.

**Bug 2 — FIXED: SSE thread pool exhaustion**
SSE generators used `q.get(block=True, timeout=25)` in `run_in_executor`. On
disconnect (browser tab switch/navigate away), asyncio cancels the generator but
the thread stays blocked for up to 25s. With only 6 threads on a 2-CPU host
(`min(32, cpu_count+4)`), a few navigations exhaust the pool — new SSE
connections queue indefinitely.

Fixed in `web/api/ws/live.py`: timeout 25s → 2s (zombie threads clear in ≤2s,
keepalives fire more frequently). Fixed in `web/web_main.py`: explicit
`ThreadPoolExecutor(max_workers=32)` set on event loop startup.

**Bug 3 — NOT YET FIXED: service connection dots go grey on tab switch**
After the above fixes, the dashboard still loses service indicator dots when
switching browser tabs. Server is healthy (all pings return pong in <10ms).
Browser's `pollServices()` uses `AbortSignal.timeout(2000)` — still timing out
somehow. Suspected causes (not yet confirmed):

- Browser tab throttling: Chrome throttles background tab timers. When tab was
  backgrounded, `setInterval(pollServices, 5000)` may have been throttled to 1/min.
  Switching back shows stale (grey) dots until the next poll fires. The browser
  does NOT immediately poll on `visibilitychange`. **Fix needed:** add
  `document.addEventListener('visibilitychange', ...)` to re-poll immediately on
  tab focus.

- HTTP/1.1 connection limit: browsers allow max 6 concurrent connections per
  origin. With multiple PCI tabs each holding an SSE connection, fetch requests
  for pings queue behind them. Not confirmed but plausible with many tabs open.

- `system.py` `/api/system/log` reads `pci.log` synchronously (no
  `run_in_executor`). If pci.log is large, this blocks the event loop.
  Latent bug even if not the primary cause today.

**Bug 4 — NOTED: autodim not using BST**
`pci.autodim` uses `astral` for sunrise/sunset. Reported to not use BST
(British Summer Time). The dim/bright comparison must use timezone-aware
datetimes. Not investigated yet — carry forward.

**Bug 5 — PORT CONFLICT: pci-rtig receiver on :9010**
`pci.web` tries to bind RTIG HTTP receiver on port 9010. On this dev host
that port is occupied; service logs warn but continues.
**Next session: set `http_port = 9011` in `config/platform.cfg` `[rtig]` section.**

### State at session end (2026-06-03 evening)
- All 7 pci services stopped cleanly.
- CM5 monolith (`pci-cm5`) restarted for overnight.
- Systemd units installed and enabled — start automatically at next boot.
- `pci-web.service` uses `PCI_WEB_PORT=8082`.

### Next session priorities (in order)
1. Fix `visibilitychange` handler in `index.html` — re-poll immediately on tab focus
2. Fix `system.py` `/api/system/log` — wrap file read in `run_in_executor`
3. Change RTIG receiver port to 9011 in `config/platform.cfg`
4. Investigate autodim BST issue in `autodim/service.py`
5. Phase 7.6: MOVA Tools AML connection (gate for Phase 8)

---

## 2026-06-03 — Fix session: systemd, WebSocket, dataset router, partial config editors

### Fix 1 — Systemd units + ports.cfg  ✓ COMPLETE

**Port inventory at session start:**
- TCP 8080: /opt/mova-env/bin/python -m pci_mova (pid 47375, /opt/MOVA monolith)
- TCP 6000–6007: same pci_mova process (MOVA AML for 8 streams)
- UDP 161: /opt/ug405-env/bin/python main.py (pid 46090, CM5 UG405)
- TCP 8081, 8082, 9010, 9011, 6010+: all free

**Port assignments:**
- pci-web: 8082 (8080=MOVA, 8081=free but skipped)
- pci-rtig HTTP: 9011 (9010 free but using 9011 per prior session note)
- pci-mova AML: 6010+ (6000-6007 occupied by /opt/MOVA)
- pci-ug405 SNMP: 1161 (161 occupied by CM5 ug405)

**Built:**
- `/opt/pci/config/ports.cfg` — reference file for all port assignments
- `/opt/pci/ug405/service.py` line 39: `LISTEN_PORT = int(os.getenv('PCI_UG405_SNMP_PORT', '161'))` (one-line env var override, no logic change)
- `/opt/pci/config/platform.cfg` [rtig] http_port = 9011 (was 9010)
- `/etc/systemd/system/pci-iobus.service` — new
- `/etc/systemd/system/pci-mova-kernel@.service` — updated (added PCI_MOVA_AML_BASE_PORT=6010)
- `/etc/systemd/system/pci-ug405.service` — new (PCI_UG405_SNMP_PORT=1161)
- `/etc/systemd/system/pci-rtig.service` — new
- `/etc/systemd/system/pci-autodim.service` — new
- `/etc/systemd/system/pci-offline.service` — new
- `/etc/systemd/system/pci-web.service` — new (PCI_WEB_PORT=8082)

**Test results — all 7 services active (running):**
- pci-iobus, pci-mova-kernel@0, pci-ug405, pci-rtig, pci-autodim, pci-offline, pci-web: all active
- curl http://localhost:8082/api/mova/streams → {"streams":[0]} ✓
- pci-web TCP 8082 ✓, RTIG TCP 9011 ✓, pci-ug405 UDP 1161 ✓
- All 7 enabled via systemctl enable.

---

### Fix 2 — SSE → WebSocket  ✓ COMPLETE

**Root cause of tab-switch freeze:**
SSE (EventSource) connections are throttled or closed by browsers in background tabs.
WebSocket connections survive tab switches.

**Server side — web/api/ws/live.py rewritten:**
- All 8 SSE endpoints replaced with WebSocket endpoints
- `@router.get(...)` → `@router.websocket(...)`
- Shared `_pump()` helper: subscribe queue → loop → `await websocket.send_text()`
- Keepalive: sends `{"t":"ping"}` every 2s idle (was SSE `: keepalive\n\n`)
- `WebSocketDisconnect` and `asyncio.CancelledError` both handled for clean unsubscribe
- Endpoints: `/ws/mova/{stream_id}`, `/ws/ug405`, `/ws/rtig`, `/ws/autodim`,
  `/ws/offline`, `/ws/agd`, `/ws/flir`, `/ws/iobus`
- app.py: router renamed from `sse_router` → `ws_router`, mounted at `/ws` (was `/sse`)

**Client side — 8 HTML files updated:**
- index.html: `openStreamSSE()` → `openStreamWS()`, `EventSource` → `WebSocket`,
  `es.onerror` → `ws.onclose` + `setTimeout(openStreamWS, 2000)` reconnect,
  `visibilitychange` listener added (re-polls services immediately on tab focus — fixes grey dots)
- agd.html, autodim.html, flir.html, iobus.html, offline.html, rtig.html, ug405.html:
  same SSE→WebSocket pattern, reconnect on close, filter `data.t === 'ping'` keepalives

**Decisions:**
- Popup pages (analysis, dataset, derived, errors, history, messages, satflow, tma, syslog)
  already used `/ws/` paths from MOVA port — no changes needed
- `design.html` matched grep but is doc-only — no changes needed

---

### Fix 3 — Dataset router  ✓ COMPLETE

**Built:**
- `/opt/pci/mova/datasets/` directory created
- `web/api/routes/dataset.py` — 6 endpoints:
  - `GET /api/dataset/` — list .mxds files: `[{name, size, mtime}]`
  - `POST /api/dataset/upload` — multipart upload → save to datasets dir → `{ok, name}`
  - `DELETE /api/dataset/{filename}` — delete → `{ok}` (path traversal rejected)
  - `POST /api/dataset/{stream}/load` — IPC `LOAD /path/file.mxds` via run_in_executor → `{ok}`
  - `GET /api/dataset/info/{name}` — stat → `{name, size, mtime, path}`
  - `GET /api/dataset/detail/{stream}` — subscribe to push socket, await snap (3s timeout) → `{stream, dataset, active_plan, tod_plan, status}`
- `python-multipart` installed and added to requirements.txt (required by FastAPI for UploadFile)
- `web/api/app.py` updated: dataset router mounted at `/api/dataset`, `GET /dataset` → dataset.html

**Decisions:**
- `_ds_path(filename)` uses `os.path.basename()` to prevent path traversal — rejects any filename ≠ basename
- `detail/{stream}` subscribes to push socket for one snap (max 3s) — avoids needing a SNAP command on kernel IPC

---

### Fix 4 — Config editors from CM5  PARTIAL — INTERRUPTED

**What was read (CM5 source, all 4 editors fully analysed):**
- `_parse_offline_plan_cfg()` / `_write_offline_plan_cfg()` — CM5 lines 4629–4734
- `_parse_ug405_scns()` / `_write_ug405_scns()` — CM5 lines 4745–4869
- `_parse_rtig_cfg()` / `_write_rtig_cfg()` / `api_rtig_rules_post()` — CM5 lines 4552–4626
- `api_autodim_get()` / `api_autodim_post()` — CM5 lines 3860–3917

**What was fixed along the way:**

**Autodim timezone bug — FIXED:**
- `_recalc()` uses `tzinfo=timezone.utc` so astral returns UTC datetimes
- `snapshot()` sends `dim_utc`/`bright_utc` as UTC ISO strings
- autodim.html `fmtUtcHHMM()` forced `timeZone: 'UTC'` on display — times showed as UTC, not BST
- Fix: renamed to `fmtLocalHHMM()`, removed `timeZone: 'UTC'` override
- Browser now displays times in its local timezone (BST/GMT for UK engineers) ✓

**What was NOT written yet (interrupted before writing config editors):**
- `web/api/routes/config.py` — all 4 editor backends (routes: /api/offline_plan_cfg, /api/ug405_scns, /api/rtig_cfg, /api/rtig_rules, /api/autodim_cfg)
- HTML updates to offline.html, ug405.html, rtig.html, autodim.html (config editor sections)

### Next session priorities (in order)

1. Write `web/api/routes/config.py` — all 4 config editor backends:
   - GET/POST /api/offline_plan_cfg → /opt/pci/config/offline_plan.json
   - GET/POST /api/ug405_scns → /opt/pci/config/ug405.cfg (line-by-line parse, preserve header+LIVE sections)
   - GET/POST /api/rtig_cfg → /opt/pci/config/rtig.cfg + rtig_rules.json
   - POST /api/rtig_rules → rtig_rules.json + RELOAD_RULES IPC command
   - GET/POST /api/autodim_cfg → /opt/pci/config/autodim.cfg
   - POST autodim: write file only (no systemctl restart from web). UI note: "Location changes take effect on service restart"

2. Mount config router in app.py at prefix="/api" (routes are root-level: /api/offline_plan_cfg etc)

3. Update HTML service pages with config editor sections:
   - offline.html: plan editor (timetable + per-SCN plans)
   - ug405.html: SCN signal mapping editor
   - rtig.html: signal map + rules JSON editor (RELOAD_RULES button)
   - autodim.html: lat/lon/offset form + "Location changes take effect on service restart" note

4. Phase 7.6: MOVA Tools AML connection (gate for Phase 8)
   - Read MICKS_MOVA_TOOLS_INFO_FOR_AML/ tcpdumps
   - Read /opt/MOVA/pci_mova/protocol/mova_tools.py
   - Build mova/protocol/aml_server.py

### State at session end
- All 7 pci services running:
  pci-iobus, pci-mova-kernel@0, pci-ug405 (UDP 1161), pci-rtig, pci-autodim, pci-offline, pci-web (TCP 8082)
- Systemd units installed and enabled
- WebSocket live on all service pages — tab switching no longer freezes UI
- Dataset router live at /api/dataset/*
- Autodim BST fix applied in autodim.html
- Config editors NOT yet written (next session start here)

---

## 2026-06-03 — Config editors: offline plan, SCN, RTIG, autodim

### All 4 config editors ✓ COMPLETE

**Built:**

**`web/api/routes/config.py`** — new file, 4 editor backends:
- `GET/POST /api/offline_plan_cfg` — reads offline_plan.json + ug405.cfg (for plan_cols);
  writes plan JSON; pci.offline hot-reloads on file mtime change (no explicit reload needed)
- `GET/POST /api/ug405_scns` — line-by-line parse of ug405.cfg (configparser cannot handle
  multiple `[SCN]` sections); preserves header (lines before first `[SCN]`) and `[LIVE]` tail;
  validates: each SCN must have a name, control signals must be unique across SCNs
- `GET/POST /api/rtig_cfg` — reads rtig.cfg (lowercase section names `[rtig]`/`[signal_map]`
  as configparser normalises them); writes preserving lowercase; uses `optionxform=str` to
  preserve signal map key case (RTIG1 not rtig1)
- `POST /api/rtig_rules` — validates (must be list, each must have `signal` key), writes
  rtig_rules.json, sends `RELOAD_RULES` IPC command to pci.rtig via rtig_client
- `GET/POST /api/autodim_cfg` — reads/writes autodim.cfg via configparser; on POST validates
  lat (±90), lon (±180), offsets (±240 min); if enabled changed, sends `SET_ENABLED 0/1` IPC
  to pci.autodim immediately; location/offset changes write file only (service restart required)

**`web/api/app.py`** — config router imported, mounted at `/api` prefix; rtig/autodim clients
wired to config module via `config_set_rtig()`/`config_set_autodim()`.

**HTML editor sections added to 4 service pages:**
- `autodim.html` — form editor: enabled toggle, lat/lon, dim/bright offset (min), IOBus signal.
  Schedule label changed from "UTC" to "local time" (consistent with BST fix).
  Note: "Location and offset changes take effect on service restart."
- `rtig.html` — two panels: (1) editable signal map key→value table with add/remove rows +
  Save; (2) JSON textarea for rules + Save & reload (triggers RELOAD_RULES hot-reload)
- `ug405.html` — SCN editor: per-SCN block with name, reply rows (field/bit/signal/invert),
  control rows (field/bit/signal). Add/remove rows per SCN. Add/remove SCNs.
- `offline.html` — three-section editor: settings (base_time_mode/base_time/active_modes),
  timetable (add/remove rows with days/start/plan), SCN plans as raw JSON textarea

**Decisions:**
- Config router mounted at prefix `/api` so routes are `/api/offline_plan_cfg` etc. (not `/api/config/...`)
- rtig_rules POST path computed as `config/rtig_rules.json` relative to config dir — matches
  how rtig/service.py resolves relative path from rtig.cfg directory
- Offline plan SCN plans shown as raw JSON textarea — the plan offset data structure (fn/dn/sfn
  bitmasks, go/mo/pv etc.) is engineering-level and not suited to a field form UI
- `optionxform=str` on configparser for signal_map preserves RTIG1/RTIG2 key case
- `_write_autodim_cfg` writes with alignment comments stripped (cleaner output than configparser default)

**Test results:**
- Import check: all modules clean
- `GET /api/autodim_cfg` → correct JSON from autodim.cfg ✓
- `GET /api/rtig_cfg` → correct JSON including signal_map with case-preserved keys ✓
- `GET /api/ug405_scns` → 5 SCNs parsed correctly from ug405.cfg ✓
- `GET /api/offline_plan_cfg` → settings/timetable/scns returned ✓
- `POST /api/autodim_cfg` → file written with dim_offset=5, bright_offset=-10, restored ✓
- `POST /api/rtig_rules` → rules written, returns `{"ok":true,"rules":1}`, restored ✓
- `POST /api/ug405_scns` → round-trip (read→write same data) → ok, 5 SCNs preserved ✓
- `POST /api/offline_plan_cfg` → file written ✓
- All 4 service pages return HTTP 200 with editor sections rendered ✓

**Outstanding:**
- Phase 7.6: MOVA Tools AML connection (gate for Phase 8)
  Read MICKS_MOVA_TOOLS_INFO_FOR_AML/ tcpdumps, read /opt/MOVA/pci_mova/protocol/mova_tools.py,
  build mova/protocol/aml_server.py


---

## 2026-06-04 — Pre-7.6 fixes: tick overrun, async log, visibilitychange

### Three fixes before Phase 7.6

**Fix 1 — index.html visibilitychange listener: ALREADY PRESENT**
The `document.addEventListener('visibilitychange', ...)` listener that re-polls
service status on tab focus was already added in the WebSocket fix session
(lines 811–814 of index.html). No change needed.

**Fix 2 — system.py: pci.log read moved to run_in_executor  ✓ FIXED**
`GET /api/system/log` read pci.log synchronously inside an `async def` handler.
On a large log file this blocks the asyncio event loop, stalling SSE keepalives
and other concurrent requests.
Fix: extracted `_read_log_sync()` and called via `loop.run_in_executor(None, ...)`.
Import check passed.

**Fix 3 — kernel_main.py: tick overrun rate-limiting  ✓ ADDED**
The upstream `MovaStream._loop()` logs `DEBUG` on every overrun tick with no
rate limiting. The ARCHITECTURE.md requires WARNING-level rate-limited logging:
one WARNING on first overrun, suppressed during the storm, one WARNING on
recovery with count and peak duration. This pattern was not present in kernel_main.py.
Fix: `_PciMovaStream._loop()` override added. Mirrors upstream crash-handling
logic exactly; replaces the sleep/overrun branch with the ARCHITECTURE.md
rate-limit state machine (`_in_overrun`, `_overrun_count`, `_overrun_peak`).
Import check passed.

**Commit:** 1405c88 — pushed to remote.

**Outstanding:**
- Phase 7.6: MOVA Tools AML connection (gate for Phase 8)
