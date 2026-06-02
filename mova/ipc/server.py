# MOVA kernel IPC server — push socket + command socket.
# Runs two background threads inside the kernel process.
#
# Push socket  /tmp/pci.mova.N.live.sock  — kernel → web (one way)
#   snap every ~1s, immediate events: phase_change, fault, stage_forced, msg
#
# Command socket  /tmp/pci.mova.N.cmd.sock  — web → kernel (req/ack)
#   one-shot: open, send line, read ack, close

import json
import logging
import os
import queue
import socket
import threading
import time

log = logging.getLogger('pci.mova.ipc')

_SNAP_TICKS  = 5    # push snap every 5 × 200ms = 1s
_POLL_SLEEP  = 0.2  # 200ms push loop poll


def _sock_path(stream_id, kind):
    return f'/tmp/pci.mova.{stream_id}.{kind}.sock'


def _clean(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


class IPCServer:
    """
    Push + command IPC server for one MOVA kernel instance.

    stream    : MovaStream — provided via set_stream() before start()
    stream_id : int — used for socket paths and logging
    event_q   : thread-safe queue for immediate events (faults etc.) —
                caller puts dicts in; push loop drains them first
    """

    def __init__(self, stream_id):
        self._sid      = stream_id
        self._stream   = None
        self._load_cb  = None         # callable(path) → dataset | None
        self._event_q  = queue.Queue()
        self._clients  = []           # connected push socket clients
        self._cli_lock = threading.Lock()

    def set_stream(self, stream):
        self._stream = stream

    def set_load_callback(self, cb):
        """cb(path) should return a loaded MovaDataset or None on error."""
        self._load_cb = cb

    def push_event(self, ev):
        """Thread-safe: put an immediate event into the push queue."""
        self._event_q.put(ev)

    def start(self):
        threading.Thread(
            target=self._push_accept, daemon=True,
            name=f'mova-push-accept-{self._sid}',
        ).start()
        threading.Thread(
            target=self._push_loop, daemon=True,
            name=f'mova-push-loop-{self._sid}',
        ).start()
        threading.Thread(
            target=self._cmd_accept, daemon=True,
            name=f'mova-cmd-{self._sid}',
        ).start()
        log.info("stream %d: IPC server started", self._sid)

    # ── Push socket ──────────────────────────────────────────────────────────

    def _push_accept(self):
        path = _sock_path(self._sid, 'live')
        _clean(path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(8)
        log.info("stream %d: push socket %s", self._sid, path)
        while True:
            try:
                conn, _ = srv.accept()
                with self._cli_lock:
                    self._clients.append(conn)
                log.debug("stream %d: push client connected (%d total)",
                          self._sid, len(self._clients))
            except Exception as e:
                log.error("stream %d: push accept error: %s", self._sid, e)

    def _push_to_all(self, data: bytes):
        dead = []
        with self._cli_lock:
            for c in list(self._clients):
                try:
                    c.sendall(data)
                except Exception:
                    dead.append(c)
            for c in dead:
                try:
                    c.close()
                except Exception:
                    pass
                self._clients.remove(c)
                log.debug("stream %d: push client disconnected (%d total)",
                          self._sid, len(self._clients))

    def _push_loop(self):
        snap_ctr    = 0
        last_stage  = None
        last_seq    = 0

        while True:
            time.sleep(_POLL_SLEEP)

            # Drain immediate events first (faults, stage_forced etc.)
            while True:
                try:
                    ev = self._event_q.get_nowait()
                    self._push_to_all((json.dumps(ev, default=str) + '\n').encode())
                except queue.Empty:
                    break

            if self._stream is None:
                continue

            # New kernel messages → push as "msg" events
            new_msgs = self._stream.messages_since(last_seq)
            for msg in new_msgs:
                ev = {"v": 1, "t": "msg", "ts": time.time()}
                ev.update(msg)
                self._push_to_all((json.dumps(ev, default=str) + '\n').encode())
                last_seq = max(last_seq, msg.get("seq", 0))

            # Detect phase change
            try:
                snap = self._stream.snapshot()
            except Exception as e:
                log.debug("stream %d: snapshot error: %s", self._sid, e)
                continue

            cur_stage = snap.get("buffers", {}).get("kernel_current_stage", 0)
            if last_stage is not None and cur_stage != last_stage:
                ev = {"v": 1, "t": "phase_change", "ts": time.time(),
                      "from": last_stage, "to": cur_stage}
                self._push_to_all((json.dumps(ev) + '\n').encode())
            last_stage = cur_stage

            # Snapshot at 1Hz
            snap_ctr += 1
            if snap_ctr >= _SNAP_TICKS:
                snap_ctr = 0
                snap["v"]  = 1
                snap["t"]  = "snap"
                snap["ts"] = time.time()
                self._push_to_all((json.dumps(snap, default=str) + '\n').encode())

    # ── Command socket ────────────────────────────────────────────────────────

    def _cmd_accept(self):
        path = _sock_path(self._sid, 'cmd')
        _clean(path)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(path)
        srv.listen(8)
        log.info("stream %d: command socket %s", self._sid, path)
        while True:
            try:
                conn, _ = srv.accept()
                threading.Thread(
                    target=self._cmd_handle, args=(conn,), daemon=True,
                ).start()
            except Exception as e:
                log.error("stream %d: cmd accept error: %s", self._sid, e)

    def _cmd_handle(self, conn):
        try:
            line = conn.makefile('r').readline()
            ack  = self._dispatch(line.strip())
            conn.sendall((json.dumps(ack, default=str) + '\n').encode())
        except Exception as e:
            log.warning("stream %d: cmd handle error: %s", self._sid, e)
        finally:
            conn.close()

    def _dispatch(self, line):
        parts = line.split()
        if not parts:
            return {"v": 1, "t": "err", "msg": "empty command"}
        cmd = parts[0].upper()

        if cmd == 'PING':
            return {"v": 1, "t": "pong"}

        if self._stream is None:
            return {"v": 1, "t": "err", "msg": "stream not ready"}

        try:
            if cmd == 'LOAD' and len(parts) >= 2:
                path = parts[1]
                ok   = False
                if self._load_cb:
                    dataset = self._load_cb(path)
                    if dataset is not None:
                        ok = self._stream.load_dataset(dataset)
                return {"v": 1, "t": "ack", "cmd": "LOAD", "ok": ok}

            elif cmd == 'UNLOAD':
                self._stream.stop()
                return {"v": 1, "t": "ack", "cmd": "UNLOAD", "ok": True}

            elif cmd == 'FORCE_STAGE' and len(parts) == 2:
                stage = int(parts[1])
                self._stream.force_stage(stage)
                self.push_event({"v": 1, "t": "stage_forced",
                                 "ts": time.time(), "stage": stage})
                return {"v": 1, "t": "ack", "cmd": "FORCE_STAGE", "ok": True}

            elif cmd == 'SWITCH_PLAN' and len(parts) == 2:
                plan = int(parts[1])
                ok   = self._stream.kernel.switch_plan(plan)
                return {"v": 1, "t": "ack", "cmd": "SWITCH_PLAN", "ok": bool(ok)}

            elif cmd == 'SET_IO' and len(parts) == 3:
                self._stream.buffers.io[int(parts[1])] = int(parts[2])
                return {"v": 1, "t": "ack", "cmd": "SET_IO", "ok": True}

            elif cmd == 'SET_DET' and len(parts) == 3:
                self._stream.buffers.set_detector(int(parts[1]), int(parts[2]))
                return {"v": 1, "t": "ack", "cmd": "SET_DET", "ok": True}

            elif cmd == 'SET_CONFIRM' and len(parts) == 3:
                self._stream.buffers.set_confirm(int(parts[1]), int(parts[2]))
                return {"v": 1, "t": "ack", "cmd": "SET_CONFIRM", "ok": True}

            elif cmd == 'SET_CRB' and len(parts) == 2:
                self._stream.buffers.crb = bool(int(parts[1]))
                return {"v": 1, "t": "ack", "cmd": "SET_CRB", "ok": True}

            elif cmd == 'SET_SPEED' and len(parts) == 2:
                self._stream.kernel.set_speed(int(parts[1]))
                return {"v": 1, "t": "ack", "cmd": "SET_SPEED", "ok": True}

            elif cmd == 'SET_TOD_OFFSET' and len(parts) == 2:
                self._stream.kernel.set_time_offset(float(parts[1]))
                return {"v": 1, "t": "ack", "cmd": "SET_TOD_OFFSET", "ok": True}

            elif cmd == 'RESET':
                self._stream.buffers.io[16] = 0   # clear error count
                if self._stream._dataset is not None:
                    stages = self._stream._dataset.constants.stages
                    self._stream.kernel.set_warmup_counter(stages)
                return {"v": 1, "t": "ack", "cmd": "RESET", "ok": True}

            else:
                return {"v": 1, "t": "err", "msg": f"unknown: {parts[0]}"}

        except Exception as e:
            log.exception("stream %d: command %s error", self._sid, cmd)
            return {"v": 1, "t": "err", "cmd": cmd, "msg": str(e)}
