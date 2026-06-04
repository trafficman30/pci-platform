# ws/live.py — WebSocket live push endpoints, web → browser.
# Replaces SSE (EventSource): WebSocket survives browser tab switches.
# Push pattern unchanged: 1Hz snap + immediate events.

import asyncio
import json
import logging
import queue

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

log = logging.getLogger('pci.web.ws')

router          = APIRouter()
_registry       = None
_ug405_client   = None
_rtig_client    = None
_autodim_client  = None
_offline_client  = None
_agd_client      = None
_flir_client     = None


def set_registry(r):
    global _registry
    _registry = r


def set_ug405_client(c):
    global _ug405_client
    _ug405_client = c


def set_rtig_client(c):
    global _rtig_client
    _rtig_client = c


def set_autodim_client(c):
    global _autodim_client
    _autodim_client = c


def set_offline_client(c):
    global _offline_client
    _offline_client = c


def set_agd_client(c):
    global _agd_client
    _agd_client = c


def set_flir_client(c):
    global _flir_client
    _flir_client = c


def _get_client(stream_id: int):
    if _registry is None:
        return None
    return _registry.get(stream_id)


async def _pump(websocket: WebSocket, client, subscribe_fn, unsubscribe_fn):
    """Read from IPC subscriber queue and push to WebSocket client."""
    q = subscribe_fn()
    loop = asyncio.get_event_loop()
    try:
        while True:
            try:
                ev = await loop.run_in_executor(
                    None, lambda: q.get(block=True, timeout=2)
                )
                await websocket.send_text(json.dumps(ev, default=str))
            except queue.Empty:
                # Keepalive — prevents proxy/browser idle timeout
                await websocket.send_text('{"t":"ping"}')
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as e:
        log.debug("ws send error: %s", e)
    finally:
        unsubscribe_fn(q)


async def _pump_transform(websocket: WebSocket, client, transform):
    """Like _pump but applies transform(event) -> out | None. None = skip."""
    q = client.subscribe()
    loop = asyncio.get_event_loop()
    try:
        while True:
            try:
                ev = await loop.run_in_executor(None, lambda: q.get(block=True, timeout=2))
                out = transform(ev)
                if out is not None:
                    await websocket.send_text(json.dumps(out, default=str))
            except queue.Empty:
                await websocket.send_text('{"t":"ping"}')
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as e:
        log.debug("ws send error: %s", e)
    finally:
        client.unsubscribe(q)


@router.websocket("/mova/{stream_id}")
async def mova_ws(websocket: WebSocket, stream_id: int):
    if _registry is None:
        await websocket.close(code=1013)
        return
    client = _registry.get(stream_id)
    if client is None:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    await _pump(websocket, client, client.subscribe, client.unsubscribe)


@router.websocket("/ug405")
async def ug405_ws(websocket: WebSocket):
    if _ug405_client is None:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    await _pump(websocket, _ug405_client,
                _ug405_client.subscribe, _ug405_client.unsubscribe)


@router.websocket("/rtig")
async def rtig_ws(websocket: WebSocket):
    if _rtig_client is None:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    await _pump(websocket, _rtig_client,
                _rtig_client.subscribe, _rtig_client.unsubscribe)


@router.websocket("/autodim")
async def autodim_ws(websocket: WebSocket):
    if _autodim_client is None:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    await _pump(websocket, _autodim_client,
                _autodim_client.subscribe, _autodim_client.unsubscribe)


@router.websocket("/offline")
async def offline_ws(websocket: WebSocket):
    if _offline_client is None:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    await _pump(websocket, _offline_client,
                _offline_client.subscribe, _offline_client.unsubscribe)


@router.websocket("/agd")
async def agd_ws(websocket: WebSocket):
    if _agd_client is None:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    await _pump(websocket, _agd_client,
                _agd_client.subscribe, _agd_client.unsubscribe)


@router.websocket("/flir")
async def flir_ws(websocket: WebSocket):
    if _flir_client is None:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    await _pump(websocket, _flir_client,
                _flir_client.subscribe, _flir_client.unsubscribe)


@router.websocket("/iobus")
async def iobus_ws(websocket: WebSocket):
    _LIVE = '/tmp/pci.iobus.live.sock'
    await websocket.accept()
    try:
        reader, writer = await asyncio.open_unix_connection(_LIVE)
    except Exception as e:
        log.warning("iobus live socket not available: %s", e)
        await websocket.send_text(json.dumps({'error': 'IOBus not connected'}))
        await websocket.close()
        return
    try:
        while True:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=2.0)
                if not line:
                    break
                await websocket.send_text(line.decode().strip())
            except asyncio.TimeoutError:
                await websocket.send_text('{"t":"ping"}')
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as e:
        log.debug("iobus ws error: %s", e)
    finally:
        try:
            writer.close()
        except Exception:
            pass


# ── Per-stream popup WebSocket endpoints ─────────────────────────────────────

@router.websocket("/derived/{stream_id}")
async def derived_ws(websocket: WebSocket, stream_id: int):
    """Full snap forwarded to derived.html and satflow.html."""
    client = _get_client(stream_id)
    if client is None:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    await _pump_transform(websocket, client,
                          lambda ev: ev if ev.get('t') == 'snap' else None)


@router.websocket("/messages/{stream_id}")
async def messages_ws(websocket: WebSocket, stream_id: int):
    """Forward msg events as {messages: [msg]} to messages.html and tma.html."""
    client = _get_client(stream_id)
    if client is None:
        await websocket.close(code=1013)
        return
    await websocket.accept()

    def _xform(ev):
        if ev.get('t') != 'msg':
            return None
        return {'messages': [ev]}

    await _pump_transform(websocket, client, _xform)


@router.websocket("/tma/{stream_id}")
async def tma_ws(websocket: WebSocket, stream_id: int):
    """Push {tma_counts, no_lanes} from snaps to tma.html."""
    client = _get_client(stream_id)
    if client is None:
        await websocket.close(code=1013)
        return
    await websocket.accept()

    def _xform(ev):
        if ev.get('t') != 'snap':
            return None
        d = ev.get('derived') or {}
        return {'tma_counts': {}, 'no_lanes': d.get('no_lanes', 0)}

    await _pump_transform(websocket, client, _xform)


@router.websocket("/errors/{stream_id}")
async def errors_ws(websocket: WebSocket, stream_id: int):
    """Push {active, history} to errors.html. History accumulated per-connection."""
    client = _get_client(stream_id)
    if client is None:
        await websocket.close(code=1013)
        return
    await websocket.accept()

    fault_history = []
    prev_keys: set = set()
    q = client.subscribe()
    loop = asyncio.get_event_loop()
    try:
        while True:
            try:
                ev = await loop.run_in_executor(None, lambda: q.get(block=True, timeout=2))
                if ev.get('t') != 'snap':
                    continue
                active = ev.get('active_faults') or []
                cur_keys = {f.get('key') for f in active}
                for f in active:
                    if f.get('key') not in prev_keys:
                        fault_history.append({**f, 'cleared': False})
                for key in (prev_keys - cur_keys):
                    for f in reversed(fault_history):
                        if f.get('key') == key and not f.get('cleared'):
                            f['cleared'] = True
                            break
                prev_keys = cur_keys
                if len(fault_history) > 200:
                    fault_history = fault_history[-200:]
                await websocket.send_text(
                    json.dumps({'active': active, 'history': fault_history}, default=str)
                )
            except queue.Empty:
                await websocket.send_text('{"t":"ping"}')
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    except Exception as e:
        log.debug("ws errors error: %s", e)
    finally:
        client.unsubscribe(q)
