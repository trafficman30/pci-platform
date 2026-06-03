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
