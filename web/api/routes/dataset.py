# web/api/routes/dataset.py
# Dataset management routes — list, upload, delete, load into kernel.
# IPC pattern: same as mova.py — send_command() via run_in_executor.

import asyncio
import logging
import os
import queue
import time

from fastapi import APIRouter, HTTPException, UploadFile, File, Query

log = logging.getLogger('pci.web.dataset')

router    = APIRouter()
_registry = None

DATASETS_DIR = os.environ.get(
    'PCI_DATASETS_DIR',
    os.path.join(os.path.dirname(__file__), '..', '..', '..', 'mova', 'datasets'),
)


def set_registry(r):
    global _registry
    _registry = r


def _ds_path(filename: str) -> str:
    """Return full path, reject any path traversal."""
    safe = os.path.basename(filename)
    if not safe or safe != filename:
        raise HTTPException(400, "invalid filename")
    return os.path.join(DATASETS_DIR, safe)


@router.get("/")
async def list_datasets():
    """List all .mxds files in the datasets directory."""
    try:
        entries = []
        for name in sorted(os.listdir(DATASETS_DIR)):
            if not name.lower().endswith('.mxds'):
                continue
            path = os.path.join(DATASETS_DIR, name)
            try:
                st = os.stat(path)
                entries.append({
                    "name":  name,
                    "size":  st.st_size,
                    "mtime": st.st_mtime,
                })
            except OSError:
                pass
        return entries
    except FileNotFoundError:
        return []


@router.post("/upload")
async def upload_dataset(file: UploadFile = File(...)):
    """Accept multipart .mxds upload, save to datasets directory."""
    name = os.path.basename(file.filename or 'upload.mxds')
    if not name.lower().endswith('.mxds'):
        raise HTTPException(400, "only .mxds files accepted")
    os.makedirs(DATASETS_DIR, exist_ok=True)
    dest = os.path.join(DATASETS_DIR, name)
    loop = asyncio.get_event_loop()

    def _write():
        content = asyncio.run_coroutine_threadsafe(file.read(), loop).result()
        with open(dest, 'wb') as f:
            f.write(content)

    try:
        data = await file.read()
        with open(dest, 'wb') as f:
            f.write(data)
    except Exception as e:
        log.error("upload failed: %s", e)
        raise HTTPException(500, f"upload error: {e}")

    log.info("dataset uploaded: %s (%d bytes)", name, len(data))
    return {"ok": True, "name": name}


@router.delete("/{filename}")
async def delete_dataset(filename: str):
    """Delete a .mxds file from the datasets directory."""
    path = _ds_path(filename)
    if not os.path.exists(path):
        raise HTTPException(404, f"{filename} not found")
    try:
        os.remove(path)
        log.info("dataset deleted: %s", filename)
    except Exception as e:
        raise HTTPException(500, f"delete error: {e}")
    return {"ok": True}


@router.post("/{stream}/load")
async def load_dataset(
    stream: int,
    filename: str = Query(...),
    stream_id: str = Query(None),
):
    """
    Load a .mxds file into a running kernel stream.
    IPC command: LOAD /full/path/to/file.mxds
    stream_id — ControllerStream <ID> within the file (ignored by IPC for now;
    reserved for future multi-stream LOAD support).
    """
    if _registry is None:
        raise HTTPException(503, "registry not initialised")

    client = _registry.get(stream)
    if client is None:
        raise HTTPException(404, f"stream {stream} not configured")

    path = _ds_path(filename)
    if not os.path.exists(path):
        raise HTTPException(404, f"{filename} not found in datasets directory")

    loop = asyncio.get_running_loop()
    ack = await loop.run_in_executor(None, client.send_command, f"LOAD {path}")
    if ack is None:
        raise HTTPException(503, f"stream {stream} kernel not responding")
    if not ack.get("ok"):
        raise HTTPException(500, {"error": "load failed", "ack": ack})
    log.info("stream %d: loaded dataset %s", stream, filename)
    return ack


@router.get("/info/{name}")
async def dataset_info(name: str):
    """
    Parse a .mxds file and return all ControllerStream metadata.
    Response mirrors MOVA /api/dataset/info/{filename}.
    """
    import sys
    if '/opt/MOVA' not in sys.path:
        sys.path.insert(0, '/opt/MOVA')

    path = _ds_path(name)
    if not os.path.exists(path):
        raise HTTPException(404, f"{name} not found")

    loop = asyncio.get_running_loop()
    try:
        from pci_mova.dataset.parser import load_all
        all_streams = await loop.run_in_executor(None, load_all, path)
    except Exception as exc:
        raise HTTPException(422, f"Dataset parse error: {exc}")

    return {
        "filename": name,
        "streams": {
            sid: {
                "stream_id": sid,
                "title":     ds.header.title,
                "stages":    len(ds.stages),
                "links":     len(ds.links),
                "detectors": len(ds.detectors),
            }
            for sid, ds in all_streams.items()
        },
    }


@router.get("/detail/{stream}")
async def dataset_detail(stream: int):
    """
    Return full parsed dataset detail for the Dataset Viewer popup.
    Gets filename + stream_id_str from the live kernel snap, then calls
    parse_full_detail() on the .mxds file — same data shape as MOVA.
    """
    import sys
    if '/opt/MOVA' not in sys.path:
        sys.path.insert(0, '/opt/MOVA')

    if _registry is None:
        raise HTTPException(503, "registry not initialised")
    client = _registry.get(stream)
    if client is None:
        raise HTTPException(404, f"stream {stream} not configured")

    # Get dataset filename and ControllerStream ID from the live snap
    q = client.subscribe()
    snap_ds = None
    try:
        loop = asyncio.get_running_loop()
        deadline = time.monotonic() + 3.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                ev = await loop.run_in_executor(
                    None, lambda t=min(remaining, 1.0): q.get(block=True, timeout=t)
                )
                if ev.get("t") == "snap":
                    snap_ds = ev.get("dataset")
                    break
            except queue.Empty:
                continue
    finally:
        client.unsubscribe(q)

    if not snap_ds or not snap_ds.get("filename"):
        raise HTTPException(404, "No dataset loaded on this stream")

    filename  = snap_ds["filename"]
    id_str    = snap_ds.get("stream_id_str", "")
    path      = os.path.join(DATASETS_DIR, os.path.basename(filename))
    if not os.path.exists(path):
        raise HTTPException(404, f"Dataset file not found: {filename}")

    from pci_mova.dataset.parser import parse_full_detail
    try:
        detail = await loop.run_in_executor(None, parse_full_detail, path, id_str)
    except Exception as exc:
        raise HTTPException(422, f"Dataset parse error: {exc}")

    return detail
