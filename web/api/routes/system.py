# web/api/routes/system.py
# System utility routes.

import asyncio
import os
import logging

from fastapi import APIRouter, Query

log    = logging.getLogger('pci.web.system')
router = APIRouter()

_LOG_DIR = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'logs')
_PCI_LOG = os.path.join(_LOG_DIR, 'pci.log')


def _read_log_sync():
    with open(_PCI_LOG, encoding='utf-8', errors='replace') as f:
        return [l.rstrip('\n') for l in f if l.strip()]


@router.get("/log")
async def get_log(lines: int = Query(default=200, ge=1, le=5000),
                  level: str = Query(default="")):
    """
    Return the tail of pci.log as a JSON list of raw strings.
    Response: {"lines": [...], "total": N}
    level — exact match filter (DEBUG / INFO / WARNING / ERROR); empty = all.
    """
    loop = asyncio.get_running_loop()
    try:
        all_lines = await loop.run_in_executor(None, _read_log_sync)
    except FileNotFoundError:
        return {"lines": [], "total": 0, "error": "pci.log not found"}
    except OSError as e:
        return {"lines": [], "total": 0, "error": str(e)}

    if level:
        tag = level.upper()
        all_lines = [l for l in all_lines if f'] {tag} ' in l or f'] {tag}\t' in l]

    total = len(all_lines)
    return {"lines": all_lines[-lines:], "total": total}
