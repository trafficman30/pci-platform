# MOVA REST routes — web process.

import asyncio
import gzip
import io
import json
import logging
import os
from datetime import date as dt_cls, datetime, timedelta

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response

log = logging.getLogger('pci.web.mova')

router         = APIRouter()
streams_router = APIRouter()
_registry      = None

_LOG_DIR = os.getenv('MOVA_LOG_DIR', '/opt/MOVA/pci_mova/logs')


def set_registry(r):
    global _registry
    _registry = r


# ── Log file helpers ──────────────────────────────────────────────────────────

def _log_path(stream_id: int, date_str: str) -> str:
    return os.path.join(_LOG_DIR, f"stream_{stream_id}_{date_str}.jsonl")


def _log_path_gz(stream_id: int, date_str: str) -> str:
    return _log_path(stream_id, date_str) + '.gz'


def _resolve_log(stream_id: int, date_str: str):
    """Return (path, is_gz) for the given stream+date, or (None, False) if not found."""
    p = _log_path(stream_id, date_str)
    if os.path.isfile(p):
        return p, False
    gz = _log_path_gz(stream_id, date_str)
    if os.path.isfile(gz):
        return gz, True
    return None, False


def _available_dates(stream_id: int) -> list:
    prefix = f"stream_{stream_id}_"
    try:
        entries = os.listdir(_LOG_DIR)
    except OSError:
        return []
    dates = set()
    for name in entries:
        if not name.startswith(prefix):
            continue
        rest = name[len(prefix):]
        if rest.endswith('.jsonl'):
            d = rest[:-6]
        elif rest.endswith('.jsonl.gz'):
            d = rest[:-9]
        else:
            continue
        if len(d) == 10:
            dates.add(d)
    return sorted(dates)


def _open_log(path: str, is_gz: bool):
    """Return an iterable of text lines from a .jsonl or .jsonl.gz file."""
    if is_gz:
        return gzip.open(path, 'rt', encoding='utf-8')
    return open(path, 'r', encoding='utf-8')


def _read_records(path: str, is_gz: bool,
                  from_ts: float = None, to_ts: float = None,
                  max_records: int = 50_000):
    """Read JSONL records from path, optionally filtered by timestamp."""
    records = []
    truncated = False
    with _open_log(path, is_gz) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get('ts', 0)
            if from_ts is not None and ts < from_ts:
                continue
            if to_ts is not None and ts > to_ts:
                continue
            records.append(rec)
            if len(records) >= max_records:
                truncated = True
                break
    return records, truncated


@router.get("/streams")
async def list_streams():
    if _registry is None:
        return {"streams": []}
    return {"streams": _registry.all_ids()}


@router.get("/streams/{stream_id}/ping")
async def ping_stream(stream_id: int):
    if _registry is None:
        raise HTTPException(503, "registry not initialised")
    client = _registry.get(stream_id)
    if client is None:
        raise HTTPException(404, f"stream {stream_id} not configured")
    loop = asyncio.get_event_loop()
    ack = await loop.run_in_executor(None, client.send_command, "PING")
    if ack is None:
        raise HTTPException(503, f"stream {stream_id} kernel not responding")
    return ack


@router.post("/streams/{stream_id}/cmd")
async def send_cmd(stream_id: int, body: dict):
    """Send an arbitrary command string to the kernel. Body: {"cmd": "FORCE_STAGE 2"}"""
    if _registry is None:
        raise HTTPException(503, "registry not initialised")
    client = _registry.get(stream_id)
    if client is None:
        raise HTTPException(404, f"stream {stream_id} not configured")
    cmd = body.get("cmd", "").strip()
    if not cmd:
        raise HTTPException(400, "cmd field required")
    loop = asyncio.get_event_loop()
    ack = await loop.run_in_executor(None, client.send_command, cmd)
    if ack is None:
        raise HTTPException(503, "kernel not responding")
    return ack


# ── /api/streams/{id}/… — log endpoints (used by history.html, analysis.html, tma.html) ──

@streams_router.get("/{stream_id}")
async def stream_status(stream_id: int):
    """Return the latest snap for a stream (used by tma.html for no_lanes)."""
    if _registry is None:
        raise HTTPException(503, "registry not initialised")
    client = _registry.get(stream_id)
    if client is None:
        raise HTTPException(404, f"stream {stream_id} not configured")
    return client.latest_snap()


@streams_router.get("/{stream_id}/logs")
def list_logs(stream_id: int):
    """List available log dates for a stream: [{date, size}]."""
    if _registry is None or _registry.get(stream_id) is None:
        raise HTTPException(404, f"stream {stream_id} not found")
    dates = _available_dates(stream_id)
    result = []
    for d in dates:
        path, is_gz = _resolve_log(stream_id, d)
        if path:
            result.append({"date": d, "size": os.path.getsize(path)})
    return result


@streams_router.get("/{stream_id}/log")
def download_log(stream_id: int, date: str = None):
    """Download the JSONL for a stream on a given date (defaults to today)."""
    if _registry is None or _registry.get(stream_id) is None:
        raise HTTPException(404, f"stream {stream_id} not found")
    log_date = date or dt_cls.today().isoformat()
    path, is_gz = _resolve_log(stream_id, log_date)
    if path is None:
        raise HTTPException(404, f"No log for stream {stream_id} on {log_date}")
    fname = f"mova_stream_{stream_id}_{log_date}.jsonl"
    if is_gz:
        fname += '.gz'
    return FileResponse(path, media_type="application/x-ndjson", filename=fname)


@streams_router.get("/{stream_id}/log/export")
def export_log(stream_id: int, from_date: str, to_date: str = None):
    """Export all JSONL records across a date range as a gzipped file."""
    if _registry is None or _registry.get(stream_id) is None:
        raise HTTPException(404, f"stream {stream_id} not found")
    try:
        start = dt_cls.fromisoformat(from_date)
        end   = dt_cls.fromisoformat(to_date) if to_date else start
    except ValueError as exc:
        raise HTTPException(422, f"Invalid date: {exc}")
    if end < start:
        raise HTTPException(422, "to_date must be >= from_date")

    buf = io.BytesIO()
    found = False
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        d = start
        while d <= end:
            path, is_gz = _resolve_log(stream_id, d.isoformat())
            if path:
                if is_gz:
                    with gzip.open(path, 'rb') as src:
                        gz.write(src.read())
                else:
                    with open(path, 'rb') as src:
                        gz.write(src.read())
                found = True
            d += timedelta(days=1)

    if not found:
        raise HTTPException(404, "No log data found for the specified date range")

    fname = f"mova_s{stream_id}_{from_date}"
    if to_date and to_date != from_date:
        fname += f"_to_{to_date}"
    fname += ".jsonl.gz"
    return Response(
        content=buf.getvalue(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@streams_router.get("/{stream_id}/log/slice")
def log_slice(stream_id: int, date: str = None,
              from_time: str = None, to_time: str = None):
    """Return filtered JSON records from the log. Response: {records, count, date, dimensions}."""
    if _registry is None or _registry.get(stream_id) is None:
        raise HTTPException(404, f"stream {stream_id} not found")

    log_date = date or dt_cls.today().isoformat()
    path, is_gz = _resolve_log(stream_id, log_date)
    if path is None:
        raise HTTPException(404, f"No log for stream {stream_id} on {log_date}")

    base = datetime.strptime(log_date, "%Y-%m-%d")

    def _parse_hms(s):
        parts = s.split(":")
        h, m, sec = int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0
        return base.replace(hour=h, minute=m, second=sec).timestamp()

    from_ts = to_ts = None
    try:
        if from_time:
            from_ts = _parse_hms(from_time)
    except Exception:
        pass
    try:
        if to_time:
            to_ts = _parse_hms(to_time)
    except Exception:
        pass

    records, truncated = _read_records(path, is_gz, from_ts, to_ts)

    # Dimensions: parse from session record in log (no live access from web process)
    dims = {"no_links": 0, "no_lanes": 0, "no_stages": 0, "no_dets": 0}
    det_meta = []
    for rec in records:
        if rec.get("t") == "session":
            dims = {"no_links": rec.get("nl", 0), "no_lanes": rec.get("nla", 0),
                    "no_stages": rec.get("ns", 0),  "no_dets": rec.get("nd", 0)}
            if rec.get("dm"):
                det_meta = rec["dm"]

    return {"records": records, "count": len(records), "date": log_date,
            "from_ts": from_ts, "to_ts": to_ts, "dimensions": dims,
            "detector_meta": det_meta, "truncated": truncated}
