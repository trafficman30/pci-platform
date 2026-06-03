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
    stream_id: int = Query(None),
):
    """
    Load a .mxds file into a running kernel stream.
    IPC command: LOAD /full/path/to/file.mxds
    """
    if _registry is None:
        raise HTTPException(503, "registry not initialised")

    target = stream_id if stream_id is not None else stream
    client = _registry.get(target)
    if client is None:
        raise HTTPException(404, f"stream {target} not configured")

    path = _ds_path(filename)
    if not os.path.exists(path):
        raise HTTPException(404, f"{filename} not found in datasets directory")

    loop = asyncio.get_event_loop()
    ack = await loop.run_in_executor(None, client.send_command, f"LOAD {path}")
    if ack is None:
        raise HTTPException(503, f"stream {target} kernel not responding")
    if not ack.get("ok"):
        raise HTTPException(500, {"error": "load failed", "ack": ack})
    log.info("stream %d: loaded dataset %s", target, filename)
    return ack


@router.get("/info/{name}")
async def dataset_info(name: str):
    """Return file metadata for a named .mxds file."""
    path = _ds_path(name)
    if not os.path.exists(path):
        raise HTTPException(404, f"{name} not found")
    st = os.stat(path)
    return {
        "name":  name,
        "size":  st.st_size,
        "mtime": st.st_mtime,
        "path":  path,
    }


@router.get("/detail/{stream}")
async def dataset_detail(stream: int):
    """
    Return dataset name and plan info from the running kernel's live snap.
    Subscribes to the push socket and waits up to 3s for a snap message.
    """
    if _registry is None:
        raise HTTPException(503, "registry not initialised")
    client = _registry.get(stream)
    if client is None:
        raise HTTPException(404, f"stream {stream} not configured")

    q = client.subscribe()
    try:
        loop = asyncio.get_event_loop()
        deadline = time.monotonic() + 3.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            timeout = min(remaining, 1.0)
            try:
                ev = await loop.run_in_executor(
                    None, lambda t=timeout: q.get(block=True, timeout=t)
                )
                if ev.get("t") == "snap":
                    return {
                        "stream":      stream,
                        "dataset":     ev.get("dataset"),
                        "active_plan": ev.get("active_plan"),
                        "tod_plan":    ev.get("tod_plan"),
                        "status":      ev.get("status"),
                    }
            except queue.Empty:
                continue
        raise HTTPException(504, f"stream {stream} did not send a snap within 3s")
    finally:
        client.unsubscribe(q)
