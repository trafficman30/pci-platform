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
