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
